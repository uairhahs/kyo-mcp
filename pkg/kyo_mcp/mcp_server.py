"""
Kyo MCP Server: Core Logic & Tool Implementation.
Orchestrates the semantic metadata graph aligned with Google OKF v0.2.
Specifically enforces strict `generated`, `verified`, and `sources` structures defined in §5.2/§5.1.
"""

import datetime
import logging
from typing import Dict, List, Optional

import networkx as nx
from kyo_mcp.database import (
    create_concept,
    create_link,
    get_all_links,
    get_concept_by_id,
    query_catalog,
    update_node_verified,
)
from kyo_mcp.okf_schema import GeneratedInfo, OKFConcept, ProvenanceSource
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

# Global State (In-memory graph + persistent DB sync)
G = nx.DiGraph()  # Directed Graph for Knowledge Links
mcp = FastMCP("Kyo Catalogue Manager")


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


def load_graph_data():
    # Rebuilds in-memory graph from persistent DB index on startup
    try:
        nodes = query_catalog()
        for node in nodes:
            G.add_node(node["id"], label=node["title"], type=node["type"])

        links = get_all_links()
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


def main():
    """Entry point for the Kyo MCP service."""
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("kyo")

    logger.info("Starting Kyo Knowledge Catalogue...")
    load_graph_data()
    mcp.run()


if __name__ == "__main__":
    main()
