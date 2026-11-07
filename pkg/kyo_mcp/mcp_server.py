"""
Kyo MCP Server: Core Logic & Tool Implementation.
Orchestrates the semantic metadata graph aligned with Google OKF v0.2.
Specifically enforces strict `generated`, `verified`, and `sources` structures defined in §5.2/§5.1.

MCP 2026-07-28 spec: stateless protocol, MRTR, Streamable HTTP transport.

The bundle of OKF concept files is the source of truth (kyo_mcp/store.py);
SQLite is an index derived from it. Every tool first refreshes the index
from the files, so edits made to them directly are always seen, and every
write goes through the store, which writes the file before indexing it.
Several server processes (one per stdio client, plus the CLI) can share one
store, so no graph state is cached in memory; graph algorithms build a
NetworkX view on demand.
"""

import functools
import itertools
import logging
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import networkx as nx
from kyo_mcp import store
from kyo_mcp.bridge import BridgeLayer
from kyo_mcp.bundle import BundleError, export_bundle, import_bundle, validate_bundle
from kyo_mcp.common import GENERATOR, is_stale, new_node_id, now_iso
from kyo_mcp.database import (
    clear_pending_deletion,
    get_all_links,
    get_concept_by_id,
    get_links,
    get_pending_deletion,
    query_catalog,
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


def kyo_tool(fn):
    """Register an MCP tool that first refreshes the index from the bundle,
    so every tool sees the files as they are now."""

    @functools.wraps(fn)
    async def refreshed(*args, **kwargs):
        store.refresh()
        return await fn(*args, **kwargs)

    return mcp.tool()(refreshed)


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


@kyo_tool
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
    Implements Freshness: `stale_after` (ISO 8601 timestamp; a bare date
    is stored as midnight UTC, per OKF §5).
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

    # create=True: an id collision must fail, never overwrite.
    saved = store.save_concept(
        okf_schema.model_dump(mode="python"), markdown_body, create=True
    )
    file = store.concept_file(saved["bundle_path"])
    return (
        f"Node created successfully: {okf_schema.id} ({okf_schema.title}) | "
        f"Trust Tier: Unverified | File: {file}"
    )


@kyo_tool
async def get_kyo_node(
    node_id: str, format: Literal["markdown", "turtle"] = "markdown"
) -> str:
    """Get a node as its OKF concept file, exactly as stored (followed by a
    summary of its links), or as Turtle RDF."""
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

    output = store.read_concept_file(node_id) or concept.to_markdown(
        node.get("body_text") or ""
    )
    output += f"\n<!-- file: {store.concept_file(node['bundle_path'])} -->\n"
    links = get_links(node_id)
    if links:
        output += "\n## Links\n\n"
        for link in links:
            output += f"- {link['source_id']} -[{link['relation_type']}]-> {link['target_id']}\n"
    return output


@kyo_tool
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

    updated = store.update_concept(node_id, changes, body=body)
    if updated is None:
        raise ToolError(f"Node {node_id} not found in catalogue.")
    return f"Node {node_id} updated ({updated['title']})"


@kyo_tool
async def delete_kyo_node(node_id: str, local_only: bool = False) -> str:
    """Delete a node and every link to or from it, along with what it
    synced to Hindsight and Mnemosyne.

    If the remote delete fails, the node is kept so the delete can be
    retried; local_only=True deletes it locally regardless and leaves any
    synced memories behind.

    Also takes the id of a node already gone from the catalogue because its
    file was deleted, moved, or replaced: its synced memories are then
    removed now instead of on the next sync (or kept, with local_only).
    """
    node = get_concept_by_id(node_id)
    tombstone = get_pending_deletion(node_id)
    if node is None and tombstone is None:
        raise ToolError(f"Node {node_id} not found in catalogue.")
    if local_only:
        if node is not None:
            store.delete_concept(node_id, tombstone=False)
        clear_pending_deletion(node_id)
        return f"Node {node_id} deleted locally; its synced memories were kept"

    bridge = BridgeLayer()
    result = await bridge.delete_concept_from_memory_systems(node_id)
    if bridge.last_error:
        kept = "Node" if node is not None else "Pending deletion of"
        raise ToolError(
            f"{kept} {node_id} kept: {bridge.last_error}. "
            "Retry, or pass local_only=True to delete it locally only."
        )
    remote = f"hindsight: {result['hindsight']}, mnemosyne: {result['mnemosyne']}"
    clear_pending_deletion(node_id)
    if node is None:
        return f"Removed the synced memories of {node_id}, already gone from the catalogue ({remote})"
    store.delete_concept(node_id, tombstone=False)
    return f"Node {node_id} deleted ({remote})"


@kyo_tool
async def link_kyo_nodes(source_id: str, target_id: str, relation_type: str) -> str:
    """Connect two knowledge nodes (create a directed edge), e.g. with
    relation_type 'references' or 'derives_from'."""
    missing = [i for i in (source_id, target_id) if not get_concept_by_id(i)]
    if missing:
        raise ToolError(f"Node(s) not found in catalogue: {', '.join(missing)}")

    store.add_link(source_id, target_id, relation_type)
    return f"Linked {source_id} -> {target_id} ({relation_type})"


@kyo_tool
async def unlink_kyo_nodes(
    source_id: str, target_id: str, relation_type: Optional[str] = None
) -> str:
    """Remove the link(s) from source to target. Without relation_type,
    every relation between the two (in that direction) is removed. A link
    written in the source's body text stays until the body is edited."""
    removed, in_body = store.remove_link(source_id, target_id, relation_type)
    note = (
        f"; {source_id}'s body still links to {target_id}, so a "
        "'references' link remains until that text is edited"
        if in_body
        else ""
    )
    if not removed:
        if in_body:
            raise ToolError(f"No typed link from {source_id} to {target_id}{note}.")
        raise ToolError(f"No matching link from {source_id} to {target_id}.")
    return f"Removed {removed} link(s) {source_id} -> {target_id}{note}"


@kyo_tool
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


@kyo_tool
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


@kyo_tool
async def search_knowledge(
    search_term: str = "", concept_type: str = "all", limit: int = 20
) -> str:
    """Full-text search across node titles, descriptions, tags, and bodies,
    ranked by relevance. Tries an exact match first (every word, as a
    prefix); a multi-word query that matches nothing falls back to any
    word matching, so a natural-language query still surfaces the closest
    results. An empty search_term lists the most recently updated nodes."""
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


@kyo_tool
async def verify_kyo_node(node_id: str, human_actor: str) -> str:
    """Mark a node as human-verified to shift its trust tier to 'Human-Reviewed' (OKF §5.3)."""
    # Stored as "human:<actor>"; a "human:" prefix on the input is accepted.
    event = {"by": human_actor, "at": now_iso()}
    if not store.verify_concept(node_id, event):
        raise ToolError(f"Node {node_id} not found in catalogue.")
    actor = human_actor.removeprefix("human:")
    return f"Node {node_id} marked as **Human-Reviewed** by human:{actor}"


@kyo_tool
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


@kyo_tool
async def sync_to_mnemosyne(node_id: str) -> str:
    """Sync a knowledge node to Mnemosyne for spaced repetition.

    This moves the concept into a spaced repetition system for long-term retention.
    """
    concept = OKFConcept.model_validate(_require_node(node_id))
    bridge = BridgeLayer()
    if not await bridge.sync_concept_to_mnemosyne(concept):
        raise ToolError(f"Failed to sync {node_id} to Mnemosyne: {bridge.last_error}")
    return f"Synced {node_id} ({concept.title}) to Mnemosyne for spaced repetition"


@kyo_tool
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


@kyo_tool
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


@kyo_tool
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


@kyo_tool
async def materialize_from_hindsight(query: str, top_k: int = 10) -> str:
    """Pull memories back from Hindsight into kyo's local catalogue.

    recall_from_hindsight alone only returns text for one turn; this
    closes the loop so a memory that was synced out and dropped locally
    can become a working kyo node again. A recalled memory that maps back
    to an existing kyo node (via Hindsight provenance metadata) touches
    that node instead of duplicating it; a memory with no such node is
    materialized as a new one.
    """
    bridge = BridgeLayer()
    stats = await bridge.materialize_from_hindsight(query, top_k)
    if bridge.last_error:
        raise ToolError(f"Error materializing from Hindsight: {bridge.last_error}")
    if stats["recalled"] == 0:
        return f"No results found for: '{query}'"
    return (
        f"Recalled {stats['recalled']} memor"
        f"{'y' if stats['recalled'] == 1 else 'ies'} for '{query}': "
        f"{stats['touched']} existing node(s) touched, "
        f"{stats['created']} new node(s) materialized."
    )


@kyo_tool
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


@kyo_tool
async def trigger_consolidation() -> str:
    """Trigger consolidation in Hindsight.

    Strengthen memory associations and organize knowledge.
    """
    bridge = BridgeLayer()
    if not await bridge.trigger_consolidation():
        raise ToolError(f"Failed to trigger consolidation: {bridge.last_error}")
    return "Consolidation triggered successfully"


@kyo_tool
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
    if stats["deletions_completed"] or stats["deletions_failed"]:
        output += (
            "  Memories of removed nodes deleted: "
            f"{stats['deletions_completed']} (failed, will retry: "
            f"{stats['deletions_failed']})\n"
        )
    if bridge.last_error:
        output += f"  Last error: {bridge.last_error}\n"
    return output


def _format_problems(problems: List[Dict[str, str]], limit: int = 20) -> str:
    lines = [f"  - {p['path']} [{p['rule']}] {p['message']}" for p in problems[:limit]]
    if len(problems) > limit:
        lines.append(f"  ... and {len(problems) - limit} more")
    return "\n".join(lines)


@kyo_tool
async def export_okf_bundle(out_dir: str, overwrite: bool = False) -> str:
    """Write a standalone copy of the catalogue as an OKF v0.2 bundle: the
    store's concept files, copied byte for byte, plus an index.md in every
    directory (docs/SPEC.md §8).

    out_dir must be missing or empty. With overwrite=True it may instead
    hold a previous Kyo export, whose files are replaced. Output is
    deterministic, so an unchanged catalogue exports to identical files.
    """
    try:
        result = export_bundle(out_dir, overwrite=overwrite)
    except BundleError as e:
        raise ToolError(str(e)) from e
    validation = result["validation"]
    output = (
        f"Exported {result['concepts']} concept(s) to {result['bundle']}\n"
        f"  Index files: {result['index_files']}\n"
        f"  OKF v0.2 conformant: {'yes' if validation['conformant'] else 'NO'}\n"
    )
    if validation["errors"]:
        output += _format_problems(validation["errors"]) + "\n"
    return output


@kyo_tool
async def import_okf_bundle(bundle_dir: str) -> str:
    """Copy an OKF bundle's concept files into the catalogue's store, byte
    for byte and at the same paths, then index them. A file whose id (its
    `kyo_id`, else one derived from its path) is already in the store
    replaces that concept. Conflicting files and files that aren't concept
    documents are skipped and reported. Broken links are kept as text."""
    try:
        result = import_bundle(bundle_dir)
    except BundleError as e:
        raise ToolError(str(e)) from e
    output = (
        f"Imported {result['concepts']} concept(s) from {result['bundle']}\n"
        f"  Created: {result['created']}, updated: {result['updated']}, "
        f"unchanged: {result['unchanged']}\n"
        f"  Store: {result['store']}\n"
    )
    if result["broken_links"]:
        output += (
            "  Broken links (kept in the text, not linked): "
            f"{len(result['broken_links'])}\n"
        )
    for title, items in (("Skipped files", result["skipped"]),):
        if items:
            output += (
                f"  {title}:\n"
                + "\n".join(f"  - {i['path']}: {i['reason']}" for i in items)
                + "\n"
            )
    if result["warnings"]:
        output += (
            "  Warnings:\n"
            + _format_problems([{**w, "rule": "id"} for w in result["warnings"]])
            + "\n"
        )
    return output


@kyo_tool
async def refresh_okf_bundle() -> str:
    """Re-read the catalogue's bundle (its OKF concept files) and report
    what changed: files edited, added, or removed outside Kyo, files that
    don't parse (their last indexed version is kept), and id conflicts.
    Every tool already does this first; use this one to see the report."""
    report = store.refresh()
    output = (
        f"Bundle: {report['bundle']}\n"
        f"  Re-indexed: {report['indexed']}, removed: {report['removed']}, "
        f"unchanged: {report['unchanged']}, written: {report['written']}\n"
        f"  Broken links (tolerated): {report['broken_links']}\n"
    )
    if report["pending_memory_deletions"]:
        output += (
            "  Removed nodes whose synced memories are still to be deleted: "
            f"{report['pending_memory_deletions']} (deleted on the next "
            "sync_all_to_memory_systems, or now with delete_kyo_node)\n"
        )
    for title, items in (
        ("Errors", report["errors"]),
        ("Warnings", report["warnings"]),
    ):
        if items:
            output += (
                f"  {title}:\n"
                + "\n".join(f"  - {i['path']}: {i['message']}" for i in items)
                + "\n"
            )
    return output


@kyo_tool
async def validate_okf_bundle(bundle_dir: str) -> str:
    """Check a directory against the OKF v0.2 conformance rules (§11): every
    concept file has YAML frontmatter with a non-empty `type`, and any
    index.md / log.md follows §8 / §9. Warnings are soft guidance."""
    try:
        result = validate_bundle(bundle_dir)
    except BundleError as e:
        raise ToolError(str(e)) from e
    verdict = "conformant" if result["conformant"] else "NOT conformant"
    output = (
        f"{bundle_dir}: {verdict} with OKF v{result['okf_version']} "
        f"({result['concepts']} concept(s))\n"
    )
    if result["errors"]:
        output += f"Errors:\n{_format_problems(result['errors'])}\n"
    if result["warnings"]:
        output += f"Warnings:\n{_format_problems(result['warnings'])}\n"
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
