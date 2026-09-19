"""
Kyo MCP Server: Core Logic & Tool Implementation.
Orchestrates the semantic metadata graph aligned with Google OKF v0.2.
Specifically enforces strict `generated`, `verified`, and `sources` structures defined in §5.2/§5.1.

MCP 2026-07-28 spec: stateless protocol, MRTR, Streamable HTTP transport.
"""

import datetime
import logging
from pathlib import Path
from typing import Dict, List, Optional

import networkx as nx
from kyo_mcp.bridge import BridgeLayer
from kyo_mcp.database import (
    create_concept,
    create_link,
    get_all_links,
    get_concept_by_id,
    query_catalog,
    update_node_verified,
)
from kyo_mcp.okf_schema import GeneratedInfo, OKFConcept, ProvenanceSource
from mcp.server.mcpserver.server import MCPServer
from pydantic import BaseModel, Field

# Global State (In-memory graph + persistent DB sync)
G = nx.DiGraph()  # Directed Graph for Knowledge Links
mcp = MCPServer(
    name="Kyo Catalogue Manager",
    title="Kyo Knowledge Catalogue",
    description="OKF v0.2 knowledge graph with Mnemosyne & Hindsight integration",
    version="0.2.0",
)


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class LinkInput(BaseModel):
    source_id: str
    target_id: str
    relation_type: str = Field(description="e.g., 'references', 'derives_from'")


class QueryInput(BaseModel):
    search_term: str = Field(
        default="", description="Keywords to find in title/description"
    )
    concept_type: str = Field(
        default="all", description="Filter by type (e.g., 'concept', 'dataset')"
    )


def load_graph_data(db_path: Optional[Path] = None):
    # Rebuilds in-memory graph from persistent DB index on startup
    try:
        nodes = query_catalog(db_path=db_path)
        for node in nodes:
            G.add_node(node["id"], **node)

        links = get_all_links(db_path=db_path)
        for link in links:
            if link["source_id"] in G.nodes and link["target_id"] in G.nodes:
                G.add_edge(
                    link["source_id"], link["target_id"], type=link["relation_type"]
                )
    except Exception as e:
        logging.getLogger("kyo").warning(
            f"Initial graph load failed (DB might be empty): {e}"
        )


@mcp.tool()
async def create_kyo_node(
    title: str,
    description: str,
    resource_uri: Optional[str] = None,
    concept_type: str = "concept",
    tags: Optional[list[str]] = None,
    stale_after: Optional[str] = None,
    sources: Optional[List[Dict]] = None,
) -> str:
    """Create a new knowledge node in the Kyo Knowledge Catalogue (OKF v0.2).

    Implements Trust Signals: `generated` (always), and optional `verified`, `sources`.
    Implements Freshness: `stale_after`.
    """

    # 1. Construct OKF-compliant schema object
    generated_info = GeneratedInfo(by="process:kyo-mcp", at=now_iso())
    sources_list = [ProvenanceSource(**s) for s in (sources or [])]

    okf_schema = OKFConcept(
        id=f"kyo-node-{len(G.nodes):04d}",
        type=concept_type,
        title=title,
        description=description,
        resource={"uri": resource_uri} if resource_uri else None,
        tags=tags or [],
        status="stable",
        generated=generated_info,
        verified=None,  # Explicitly unverified on creation
        sources=sources_list,
    )

    # If stale_after is provided, add to metadata (handled by DB logic)
    if stale_after:
        okf_schema.metadata = {**okf_schema.metadata, "stale_after": stale_after}

    # 2. Serialize to Markdown Bundle content (simulated)
    markdown_body = f"# {title}\n\n{description}\n\nType: {concept_type}\nTags: {', '.join(tags or [])}"

    # 3. Persist & Index
    create_concept(okf_schema.model_dump(mode="python"), markdown_body)

    # 4. Update In-Memory Graph
    G.add_node(
        okf_schema.id,
        label=okf_schema.title,
        type=okf_schema.type,
        status=okf_schema.status,
    )

    return f"Node created successfully: {okf_schema.id} ({okf_schema.title}) | Trust Tier: Unverified"


@mcp.tool()
async def link_kyo_nodes(source_id: str, target_id: str, relation_type: str) -> str:
    """Connect two knowledge nodes (create a directed edge)."""

    # Validation: ensure both IDs exist in the catalogue
    if source_id not in G.nodes or target_id not in G.nodes:
        return "Error: One or both IDs could not be found in the local knowledge graph."

    # Persist to DB first so the edge survives a restart
    create_link(source_id, target_id, relation_type)

    # Update in-memory graph topology to match
    G.add_edge(source_id, target_id, type=relation_type)

    return f"Links {source_id} -> {target_id} ({relation_type})"


@mcp.tool()
async def search_knowledge(query: QueryInput) -> str:
    """Semantic search across the Kyo Knowledge Catalogue."""

    # Delegate to high-speed DuckDB index (now SQLite)
    results = query_catalog(
        type_filter=query.concept_type if query.concept_type != "all" else None,
        search_term=query.search_term,
    )

    output = f'Found {len(results)} result(s) for: "{query.search_term}"\n---\n'
    for r in results:
        # Format trust tier
        tier = "Unverified"
        meta_verified = r.get("verified", [])
        if meta_verified:
            tiers = [v.get("by", "") for v in meta_verified]
            if any(t.startswith("human:") for t in tiers):
                tier = "Human-Reviewed"
            elif any(t.startswith("process:") for t in tiers):
                tier = "Machine-Confirmed"

        output += f"[{tier}] **{r['title']}** ({r['type']})\nID: `{r['id']}`\nTags: {', '.join(r.get('tags', []))}\n\n"

    return output


@mcp.tool()
async def verify_kyo_node(node_id: str, human_actor: str) -> str:
    """Mark a node as human-verified to shift trust tier from 'machine-confirmed' to 'human-reviewed' (OKF §5.3)."""

    # 1. Check if exists (exact ID lookup, not a title search)
    row = get_concept_by_id(node_id)
    if not row:
        return f"Error: Node {node_id} not found in catalogue."

    # Construct new verification event per OKF v0.2 §5.2
    # Note: `by` is stored bare here; update_node_verified applies the "human:" prefix.
    event = {"by": human_actor, "at": now_iso()}

    success = update_node_verified(node_id, event)

    if success:
        # Mark the node as verified in the in-memory graph as a visual cue
        if node_id in G.nodes:
            G.nodes[node_id]["verified"] = True
        return f"Node {node_id} marked as **Human-Reviewed** by human:{human_actor}"
    else:
        return f"Error: Node {node_id} not found in catalogue."


@mcp.tool()
async def get_node_trust_status(node_id: str) -> str:
    """Retrieve the full trust and provenance metadata for a node."""
    r = get_concept_by_id(node_id)

    if not r:
        return f"Node {node_id} not found."

    verified_list = r.get("verified", [])
    tier = "Unverified"
    if verified_list:
        actors = [v.get("by", "") for v in verified_list]
        if any(a.startswith("human:") for a in actors):
            tier = "Human-Reviewed"
        elif any(a.startswith("process:") for a in actors):
            tier = "Machine-Confirmed"

    return f"""
Node: {r['title']} (ID: {node_id})
Trust Tier: {tier}
Status: {r.get('status', 'stable')}
Generated By: {r.get('generated', {}).get('by') if r.get('generated') else 'N/A'}
Sources: {len(r.get('sources', []))} attached.
Stale After: {r.get('stale_after', 'Never')}
    """.strip()


@mcp.tool()
async def sync_to_mnemosyne(node_id: str) -> str:
    """Sync a knowledge node to Mnemosyne for spaced repetition.

    This moves the concept into a spaced repetition system for long-term retention.
    """
    try:
        r = get_concept_by_id(node_id)
        if not r:
            return f"Error: Node {node_id} not found."

        concept = OKFConcept.model_validate(r)
        bridge = BridgeLayer()
        success = await bridge.sync_concept_to_mnemosyne(concept)

        if success:
            return f"✓ Synced {node_id} ({concept.title}) to Mnemosyne for spaced repetition"
        else:
            return f"✗ Failed to sync {node_id} to Mnemosyne"
    except Exception as e:
        return f"Error syncing to Mnemosyne: {e}"


@mcp.tool()
async def sync_to_hindsight(node_id: str) -> str:
    """Queue a knowledge node for Hindsight fact extraction.

    This submits the node asynchronously (Hindsight's own async=true
    retain mode) and returns as soon as it's queued. Fact extraction
    itself can take a long time on a slow or CPU-only LLM backend, so this
    call no longer waits for it to finish. Use check_hindsight_sync_status
    to find out when extraction has actually completed.
    """
    try:
        r = get_concept_by_id(node_id)
        if not r:
            return f"Error: Node {node_id} not found."

        concept = OKFConcept.model_validate(r)
        bridge = BridgeLayer()
        success = await bridge.sync_concept_to_hindsight(concept)

        if success:
            return (
                f"✓ Queued {node_id} ({concept.title}) for Hindsight fact extraction "
                "-- check_hindsight_sync_status to see when it's done"
            )
        else:
            return f"✗ Failed to queue {node_id} for Hindsight"
    except Exception as e:
        return f"Error queuing for Hindsight: {e}"


@mcp.tool()
async def check_hindsight_sync_status(node_id: str) -> str:
    """Check the status of a node's Hindsight fact-extraction sync.

    Call this after sync_to_hindsight to find out whether extraction has
    actually finished, since that call itself only confirms the work was
    queued, not completed.
    """
    try:
        r = get_concept_by_id(node_id)
        if not r:
            return f"Error: Node {node_id} not found."

        bridge = BridgeLayer()
        result = await bridge.check_hindsight_operation(node_id)
        state = result.get("state")

        if state == "no_operation":
            return f"No Hindsight sync currently pending for {node_id}."
        if state == "completed":
            return f"✓ {node_id} finished Hindsight fact extraction."
        if state in ("pending", "processing"):
            return f"⧗ {node_id} is still {state} in Hindsight (operation {result.get('operation_id')})."
        if state in ("failed", "cancelled", "not_found"):
            error = result.get("error")
            suffix = f": {error}" if error else ""
            return f"✗ {node_id}'s Hindsight sync ended in state '{state}'{suffix}. A fresh sync_to_hindsight call will retry."
        return f"Error checking {node_id}'s Hindsight status: {result.get('error')}"
    except Exception as e:
        return f"Error checking Hindsight status: {e}"


@mcp.tool()
async def recall_from_hindsight(query: str, top_k: int = 5) -> str:
    """Recall memories from Hindsight using semantic search.

    Search across all synced concepts using natural language.
    """
    try:
        bridge = BridgeLayer()
        results = await bridge.recall_from_hindsight(query, top_k)

        if not results:
            return f"No results found for: '{query}'"

        output = f"Found {len(results)} result(s) for: '{query}'\n---\n"
        for i, result in enumerate(results, 1):
            content = result.get("content", result.get("text", str(result)))
            output += f"{i}. {content}\n"

        return output
    except Exception as e:
        return f"Error recalling from Hindsight: {e}"


@mcp.tool()
async def trigger_reflection(query: str) -> str:
    """Trigger reflection in Hindsight to generate insights.

    Ask Hindsight to analyze your knowledge base and generate observations.
    """
    try:
        bridge = BridgeLayer()
        result = await bridge.trigger_reflection(query)

        if not result:
            return "No reflections generated."

        output = f"Reflection on: '{query}'\n---\n"

        # Format observations
        observations = result.get("observations", result.get("insights", []))
        if observations:
            for obs in observations:
                if isinstance(obs, dict):
                    output += f"• {obs.get('text', obs.get('content', str(obs)))}\n"
                else:
                    output += f"• {obs}\n"
        else:
            output += str(result)

        return output
    except Exception as e:
        return f"Error triggering reflection: {e}"


@mcp.tool()
async def trigger_consolidation() -> str:
    """Trigger consolidation in Hindsight.

    Strengthen memory associations and organize knowledge.
    """
    try:
        bridge = BridgeLayer()
        success = await bridge.trigger_consolidation()

        if success:
            return "✓ Consolidation triggered successfully"
        else:
            return "✗ Failed to trigger consolidation"
    except Exception as e:
        return f"Error triggering consolidation: {e}"


@mcp.tool()
async def sync_all_to_memory_systems() -> str:
    """Sync all knowledge nodes to Mnemosyne and Hindsight.

    Batch sync all concepts for maximum memory system integration.
    """
    try:
        bridge = BridgeLayer()
        stats = await bridge.sync_all_concepts()

        output = "Sync completed:\n"
        output += f"  Total concepts: {stats['total']}\n"
        output += f"  Synced to Mnemosyne: {stats['mnemosyne_success']}\n"
        output += f"  Synced to Hindsight: {stats['hindsight_success']}\n"

        return output
    except Exception as e:
        return f"Error syncing all concepts: {e}"


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
        "--port",
        type=int,
        default=8000,
        help="Port for streamable-http transport (default: 8000)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("kyo")

    logger.info(f"Starting Kyo Knowledge Catalogue with {args.transport} transport...")
    load_graph_data()
    mcp.run(transport=args.transport, port=args.port)


if __name__ == "__main__":
    main()
