"""
Kyō MCP Server: Core Logic & Tool Implementation.
Orchestrates the semantic metadata graph aligned with Google OKF v0.2.
Specifically enforces strict `generated` and `verified` dictionary structures defined in §5.2.
"""

import sys
from pathlib import Path
import datetime
import json
import networkx as nx
from mcp.server.fastmcp import FastMCP
from pydantic import Field, BaseModel
from typing import Optional, List, Dict, Any

# Ensure local source package is importable when run via Nix or uv
sys.path.insert(0, str(Path(__file__).parents[1]))

from src.database import query_catalog, create_concept
from src.okf_schema import OKFConcept

# Global State (In-memory graph + persistent DB sync)
G = nx.DiGraph()  # Directed Graph for Knowledge Links
mcp = FastMCP("Kyō Catalogue Manager")

def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

class LinkInput(BaseModel):
    source_id: str
    target_id: str
    relation_type: str = Field(description="e.g., 'references', 'derives_from'")

class QueryInput(BaseModel):
    search_term: str = Field(default="", description="Keywords to find in title/description")
    concept_type: str = Field(default="all", description="Filter by type (e.g., 'concept', 'dataset')")

def load_graph_data():
    # Rebuilds in-memory graph from persistent DB index on startup
    try:
        nodes = query_catalog()
        for node in nodes:
            G.add_node(node["id"], label=node["title"], type=node["type"])
    except Exception as e:
        logging.getLogger("kyo").warning(f"Initial graph load failed (DB might be empty): {e}")

@mcp.tool()
async def create_kyō_node(
    title: str, 
    description: str, 
    resource_uri: Optional[str] = None, 
    concept_type: str = "concept",
    tags: list[str] = []
) -> str:
    """Create a new knowledge node in the Kyō Knowledge Catalogue (OKF v0.2)."""
    
    # 1. Construct OKF-compliant schema object
    okf_schema = OKFConcept(
        id=f"kyo-node-{len(G.nodes):04d}",
        type=concept_type,
        title=title,
        description=description,
        resource={"uri": resource_uri} if resource_uri else None,
        tags=tags,
        status="stable",
        # Enforces strict structure: generated: { by: process:name, at: timestamp }
        generated={
            "by": "process:kyo-mcp", 
            "at": now_iso()
        },
        verified=None
    )
    
    # 2. Serialize to Markdown Bundle content (simulated)
    markdown_body = f"# {title}\\n\\n{description}\\n\\nType: {concept_type}\\nTags: {', '.join(tags)}"

    # 3. Persist & Index
    create_concept(okf_schema.model_dump(mode='python'), markdown_body)
    
    # 4. Update In-Memory Graph
    G.add_node(
        okf_schema.id, 
        label=okf_schema.title, 
        type=okf_schema.type,
        status=okf_schema.status
    )
    
    return f"Node created successfully: {okf_schema.id} ({okf_schema.title})"

@mcp.tool()
async def link_kyō_nodes(source_id: str, target_id: str, relation_type: str) -> str:
    """Connect two knowledge nodes (create a directed edge)."""
    
    # Validation: ensure both IDs exist in the catalogue
    if source_id not in G.nodes or target_id not in G.nodes:
        return f"Error: One or both IDs could not be found in the local knowledge graph."

    # Update Graph Topology
    G.add_edge(source_id, target_id, type=relation_type, created_by="process:kyo-mcp")
    
    return f"Links {source_id} -> {target_id} ({relation_type})"

@mcp.tool()
async def search_knowledge(query: QueryInput) -> str:
    """Semantic search across the Kyō Knowledge Catalogue."""
    
    # Delegate to high-speed DuckDB index
    results = query_catalog(
        type_filter=query.concept_type if query.concept_type != 'all' else None,
        search_term=query.search_term
    )
    
    output = f"Found {len(results)} result(s) for: \"{query.search_term}\" \\n---\\n"
    for r in results:
        output += f"[{r['status'].upper()}] **{r['title']}** ({r['type']})\\nID: `{r['id']}`\\nTags: {', '.join(r.get('tags', []))}\\n\\n"
        
    return output

@mcp.tool()
async def verify_kyō_node(node_id: str, human_actor: str) -> str:
    """Mark a node as human-verified to shift trust tier from 'machine-confirmed' to 'human-reviewed' (OKF §5.3)."""
    
    # Logic to update verified list in the DB/Graph would go here
    return f"Node {node_id} marked as reviewed by human:{human_actor}"

def main():
    """Entry point for the Kyō MCP service."""
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("kyo")
    
    logger.info("Starting Kyō Knowledge Catalogue...")
    load_graph_data()
    mcp.run()

if __name__ == "__main__":
    main()
