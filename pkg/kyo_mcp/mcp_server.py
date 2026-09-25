"""
Kyo MCP Server: Core Logic & Tool Implementation.
Orchestrates the semantic metadata graph aligned with Google OKF v0.2.
Specifically enforces strict `generated`, `verified`, and `sources` structures defined in §5.2/§5.1.

MCP 2026-07-28 spec: stateless protocol, MRTR, Streamable HTTP transport.

SQLite is the only source of truth. Several server processes (one per
stdio client, plus the CLI) can share one database, so no graph state is
cached in memory; graph algorithms build a NetworkX view on demand.
"""

import itertools
import logging
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import networkx as nx
from kyo_mcp.bridge import BridgeLayer
from kyo_mcp.common import GENERATOR, is_stale, new_node_id, now_iso
from kyo_mcp.database import (
    create_concept,
    create_link,
    delete_concept,
    delete_link,
    get_all_links,
    get_concept_by_id,
    get_links,
    query_catalog,
    update_concept,
    update_node_verified,
)
from kyo_mcp.okf_schema import (
    ConceptStatus,
    GeneratedInfo,
    OKFConcept,
    ProvenanceSource,
    trust_tier,
)
from kyo_mcp.ontology import export_to_rdf_turtle
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.server import MCPServer

mcp = MCPServer(
    name="Kyo Catalogue Manager",
    title="Kyo Knowledge Catalogue",
    description="OKF v0.2 knowledge graph with Mnemosyne & Hindsight integration",
    version="0.2.0",
)


def build_graph(db_path: Optional[Path] = None) -> nx.MultiDiGraph:
    """Build a fresh directed graph of every concept and link. A multigraph,
    since two nodes can be linked by several relation types."""
    graph = nx.MultiDiGraph()
    for node in query_catalog(db_path=db_path):
        graph.add_node(node["id"], title=node.get("title"), type=node["type"])
    for link in get_all_links(db_path=db_path):
        graph.add_edge(link["source_id"], link["target_id"], type=link["relation_type"])
    return graph


def _require_node(node_id: str) -> Dict[str, Any]:
    node = get_concept_by_id(node_id)
    if not node:
        raise ToolError(f"Node {node_id} not found in catalogue.")
    return node


@mcp.tool()
async def create_kyo_node(
    title: str,
    description: str,
    resource_uri: Optional[str] = None,
    concept_type: str = "concept",
    tags: Optional[list[str]] = None,
    status: ConceptStatus = "stable",
    stale_after: Optional[str] = None,
    sources: Optional[List[Dict]] = None,
    body: Optional[str] = None,
) -> str:
    """Create a new knowledge node in the Kyo Knowledge Catalogue (OKF v0.2).

    Implements Trust Signals: `generated` (always), and optional `sources`.
    Implements Freshness: `stale_after` (ISO 8601 date or timestamp).
    `body` is the node's markdown body; a minimal one is generated from the
    title and description when omitted.
    """
    okf_schema = OKFConcept(
        id=new_node_id(),
        type=concept_type,
        title=title,
        description=description,
        resource={"uri": resource_uri} if resource_uri else None,
        tags=tags or [],
        status=status,
        stale_after=stale_after,
        generated=GeneratedInfo(by=GENERATOR, at=now_iso()),
        verified=None,  # Explicitly unverified on creation
        sources=[ProvenanceSource(**s) for s in (sources or [])],
    )
    markdown_body = body or f"# {title}\n\n{description}\n"

    # update_existing=False: an id collision must fail, never overwrite.
    create_concept(
        okf_schema.model_dump(mode="python"), markdown_body, update_existing=False
    )

    return f"Node created successfully: {okf_schema.id} ({okf_schema.title}) | Trust Tier: Unverified"


@mcp.tool()
async def get_kyo_node(
    node_id: str, format: Literal["markdown", "turtle"] = "markdown"
) -> str:
    """Get a node as an OKF markdown bundle file (frontmatter plus body,
    followed by its links) or as Turtle RDF."""
    node = _require_node(node_id)
    concept = OKFConcept.model_validate(node)

    if format == "turtle":
        return export_to_rdf_turtle(
            concept_id=node_id,
            concept_type=concept.type,
            title=concept.title or "",
            description=concept.description or "",
            ontology=concept.ontology,
            tags=concept.tags,
            links=get_links(node_id, direction="out"),
        )

    output = concept.to_markdown(node.get("body_text") or "")
    links = get_links(node_id)
    if links:
        output += "\n## Links\n\n"
        for link in links:
            output += f"- {link['source_id']} -[{link['relation_type']}]-> {link['target_id']}\n"
    return output


@mcp.tool()
async def update_kyo_node(
    node_id: str,
    title: Optional[str] = None,
    description: Optional[str] = None,
    resource_uri: Optional[str] = None,
    concept_type: Optional[str] = None,
    tags: Optional[list[str]] = None,
    status: Optional[ConceptStatus] = None,
    stale_after: Optional[str] = None,
    body: Optional[str] = None,
) -> str:
    """Update some fields of an existing node; omitted fields are unchanged.

    The `generated` stamp is refreshed, since the content was re-produced.
    Verification history is kept.
    """
    _require_node(node_id)
    requested = {
        "title": title,
        "description": description,
        "resource": {"uri": resource_uri} if resource_uri else None,
        "type": concept_type,
        "tags": tags,
        "status": status,
        "stale_after": stale_after,
    }
    changes: Dict[str, Any] = {k: v for k, v in requested.items() if v is not None}
    if not changes and body is None:
        raise ToolError("Nothing to update: pass at least one field.")
    changes["generated"] = {"by": GENERATOR, "at": now_iso()}

    updated = update_concept(node_id, changes, markdown_body=body)
    if updated is None:
        raise ToolError(f"Node {node_id} not found in catalogue.")
    return f"Node {node_id} updated ({updated['title']})"


@mcp.tool()
async def delete_kyo_node(node_id: str) -> str:
    """Delete a node and every link to or from it.

    Memories already synced to Mnemosyne or Hindsight are not removed.
    """
    if not delete_concept(node_id):
        raise ToolError(f"Node {node_id} not found in catalogue.")
    return f"Node {node_id} deleted"


@mcp.tool()
async def link_kyo_nodes(source_id: str, target_id: str, relation_type: str) -> str:
    """Connect two knowledge nodes (create a directed edge), e.g. with
    relation_type 'references' or 'derives_from'."""
    missing = [i for i in (source_id, target_id) if not get_concept_by_id(i)]
    if missing:
        raise ToolError(f"Node(s) not found in catalogue: {', '.join(missing)}")

    create_link(source_id, target_id, relation_type)
    return f"Linked {source_id} -> {target_id} ({relation_type})"


@mcp.tool()
async def unlink_kyo_nodes(
    source_id: str, target_id: str, relation_type: Optional[str] = None
) -> str:
    """Remove the link(s) from source to target. Without relation_type,
    every relation between the two (in that direction) is removed."""
    removed = delete_link(source_id, target_id, relation_type)
    if not removed:
        raise ToolError(f"No matching link from {source_id} to {target_id}.")
    return f"Removed {removed} link(s) {source_id} -> {target_id}"


@mcp.tool()
async def get_node_links(
    node_id: str, direction: Literal["in", "out", "both"] = "both"
) -> str:
    """List a node's links: outgoing ("out"), incoming ("in"), or both."""
    _require_node(node_id)
    links = get_links(node_id, direction=direction)
    if not links:
        return f"Node {node_id} has no {direction} links."

    titles: Dict[str, str] = {}
    lines = []
    for link in links:
        other = link["target_id"] if link["source_id"] == node_id else link["source_id"]
        if other not in titles:
            concept = get_concept_by_id(other)
            titles[other] = concept["title"] if concept else "?"
        arrow = "->" if link["source_id"] == node_id else "<-"
        lines.append(f"{arrow} [{link['relation_type']}] {other} ({titles[other]})")
    return f"Links for {node_id}:\n" + "\n".join(lines)


@mcp.tool()
async def find_path(source_id: str, target_id: str, directed: bool = True) -> str:
    """Find the shortest chain of links between two nodes. With
    directed=False, links may be followed in either direction."""
    _require_node(source_id)
    _require_node(target_id)
    graph = build_graph()
    view = graph if directed else graph.to_undirected(as_view=True)
    try:
        path = nx.shortest_path(view, source_id, target_id)
    except nx.NetworkXNoPath:
        return f"No path from {source_id} to {target_id}."

    steps = [f"{path[0]} ({graph.nodes[path[0]]['title']})"]
    for a, b in itertools.pairwise(path):
        edges = graph.get_edge_data(a, b) or graph.get_edge_data(b, a) or {}
        relation = next(iter(edges.values()), {}).get("type", "?")
        steps.append(f"  -[{relation}]-> {b} ({graph.nodes[b]['title']})")
    return "\n".join(steps)


@mcp.tool()
async def search_knowledge(
    search_term: str = "", concept_type: str = "all", limit: int = 20
) -> str:
    """Full-text search across node titles, descriptions, tags, and bodies,
    ranked by relevance. Every word must match (as a prefix). An empty
    search_term lists the most recently updated nodes."""
    results = query_catalog(
        type_filter=concept_type if concept_type != "all" else None,
        search_term=search_term,
        limit=max(1, limit),
    )

    output = f'Found {len(results)} result(s) for: "{search_term}"\n---\n'
    for r in results:
        tier = trust_tier(r.get("verified"))
        stale = " [Stale]" if is_stale(r.get("stale_after")) else ""
        output += (
            f"[{tier}]{stale} **{r['title']}** ({r['type']}, {r['status']})\n"
            f"ID: `{r['id']}`\nTags: {', '.join(r.get('tags', []))}\n\n"
        )

    return output


@mcp.tool()
async def verify_kyo_node(node_id: str, human_actor: str) -> str:
    """Mark a node as human-verified to shift its trust tier to 'Human-Reviewed' (OKF §5.3)."""
    # Stored as "human:<actor>"; a "human:" prefix on the input is accepted.
    event = {"by": human_actor, "at": now_iso()}
    if not update_node_verified(node_id, event):
        raise ToolError(f"Node {node_id} not found in catalogue.")
    actor = human_actor.removeprefix("human:")
    return f"Node {node_id} marked as **Human-Reviewed** by human:{actor}"


@mcp.tool()
async def get_node_trust_status(node_id: str) -> str:
    """Retrieve the full trust and provenance metadata for a node."""
    r = _require_node(node_id)
    stale_after = r.get("stale_after")
    freshness = "Stale" if is_stale(stale_after) else "Fresh"
    generated = r.get("generated") or {}

    return f"""
Node: {r["title"]} (ID: {node_id})
Trust Tier: {trust_tier(r.get("verified"))}
Status: {r.get("status", "stable")}
Freshness: {freshness} (stale after: {stale_after or "never"})
Generated By: {generated.get("by", "N/A")} at {generated.get("at", "N/A")}
Verified By: {", ".join(v.get("by", "") for v in r.get("verified") or []) or "nobody"}
Sources: {len(r.get("sources", []))} attached.
    """.strip()


@mcp.tool()
async def sync_to_mnemosyne(node_id: str) -> str:
    """Sync a knowledge node to Mnemosyne for spaced repetition.

    This moves the concept into a spaced repetition system for long-term retention.
    """
    concept = OKFConcept.model_validate(_require_node(node_id))
    bridge = BridgeLayer()
    if not await bridge.sync_concept_to_mnemosyne(concept):
        raise ToolError(f"Failed to sync {node_id} to Mnemosyne: {bridge.last_error}")
    return f"Synced {node_id} ({concept.title}) to Mnemosyne for spaced repetition"


@mcp.tool()
async def sync_to_hindsight(node_id: str) -> str:
    """Queue a knowledge node for Hindsight fact extraction.

    This submits the node asynchronously (Hindsight's own async=true
    retain mode) and returns as soon as it's queued. Fact extraction
    itself can take a long time on a slow or CPU-only LLM backend, so this
    call doesn't wait for it to finish. Use check_hindsight_sync_status
    to find out when extraction has actually completed.
    """
    concept = OKFConcept.model_validate(_require_node(node_id))
    bridge = BridgeLayer()
    if not await bridge.sync_concept_to_hindsight(concept):
        raise ToolError(f"Failed to queue {node_id} for Hindsight: {bridge.last_error}")
    return (
        f"Queued {node_id} ({concept.title}) for Hindsight fact extraction. "
        "Use check_hindsight_sync_status to see when it's done."
    )


@mcp.tool()
async def check_hindsight_sync_status(node_id: str) -> str:
    """Check the status of a node's Hindsight fact-extraction sync.

    Call this after sync_to_hindsight to find out whether extraction has
    actually finished, since that call itself only confirms the work was
    queued, not completed.
    """
    _require_node(node_id)
    result = await BridgeLayer().check_hindsight_operation(node_id)
    state = result.get("state")

    if state == "no_operation":
        return f"No Hindsight sync currently pending for {node_id}."
    if state == "completed":
        return f"{node_id} finished Hindsight fact extraction."
    if state in ("pending", "processing"):
        return f"{node_id} is still {state} in Hindsight (operation {result.get('operation_id')})."
    if state in ("failed", "cancelled", "not_found"):
        error = result.get("error")
        suffix = f": {error}" if error else ""
        return (
            f"{node_id}'s Hindsight sync ended in state '{state}'{suffix}. "
            "A fresh sync_to_hindsight call will retry."
        )
    raise ToolError(
        f"Error checking {node_id}'s Hindsight status: {result.get('error')}"
    )


@mcp.tool()
async def recall_from_hindsight(query: str, top_k: int = 5) -> str:
    """Recall memories from Hindsight using semantic search.

    Search across all synced concepts using natural language.
    """
    bridge = BridgeLayer()
    results = await bridge.recall_from_hindsight(query, top_k)
    if bridge.last_error:
        raise ToolError(f"Error recalling from Hindsight: {bridge.last_error}")
    if not results:
        return f"No results found for: '{query}'"

    output = f"Found {len(results)} result(s) for: '{query}'\n---\n"
    for i, result in enumerate(results, 1):
        content = result.get("content", result.get("text", str(result)))
        output += f"{i}. {content}\n"
    return output


@mcp.tool()
async def trigger_reflection(query: str) -> str:
    """Trigger reflection in Hindsight to generate insights.

    Ask Hindsight to analyze your knowledge base and generate observations.
    """
    bridge = BridgeLayer()
    result = await bridge.trigger_reflection(query)
    if bridge.last_error:
        raise ToolError(f"Error triggering reflection: {bridge.last_error}")
    if not result:
        return "No reflections generated."

    # Hindsight's actual ReflectResponse carries the answer as markdown in
    # `text` (confirmed against its OpenAPI schema); this used to look for
    # "observations"/"insights", which that response never has, so a real
    # reflection always fell through to a raw dict dump below instead of
    # the markdown Hindsight actually generated.
    text = result.get("text")
    if text:
        return f"Reflection on: '{query}'\n---\n{text}"
    return f"Reflection on: '{query}'\n---\n{result}"


@mcp.tool()
async def trigger_consolidation() -> str:
    """Trigger consolidation in Hindsight.

    Strengthen memory associations and organize knowledge.
    """
    bridge = BridgeLayer()
    if not await bridge.trigger_consolidation():
        raise ToolError(f"Failed to trigger consolidation: {bridge.last_error}")
    return "Consolidation triggered successfully"


@mcp.tool()
async def sync_all_to_memory_systems() -> str:
    """Sync all knowledge nodes to Mnemosyne and Hindsight.

    Unchanged nodes are skipped. In-flight Hindsight operations are checked
    first, so completed ones are recorded rather than resubmitted.
    """
    bridge = BridgeLayer()
    stats = await bridge.sync_all_concepts()

    output = "Sync completed:\n"
    output += f"  Total concepts: {stats['total']}\n"
    output += f"  Synced to Mnemosyne: {stats['mnemosyne_success']}\n"
    output += f"  Queued or current in Hindsight: {stats['hindsight_success']}\n"
    output += f"  Hindsight extractions completed since last check: {stats['hindsight_completed']}\n"
    if bridge.last_error:
        output += f"  Last error: {bridge.last_error}\n"
    return output


def main():
    """Entry point for the Kyo MCP service."""
    import argparse

    parser = argparse.ArgumentParser(description="Kyo MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="Transport protocol (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address for HTTP transports (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for HTTP transports (default: 8000)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    logging.getLogger("kyo").info(
        f"Starting Kyo Knowledge Catalogue with {args.transport} transport..."
    )
    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=args.transport, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
