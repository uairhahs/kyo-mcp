# kyo-mcp

An MCP (Model Context Protocol) server implementing the [Open Knowledge Format (OKF) v0.2](https://github.com/google-research/open-knowledge-format) specification. It provides a managed knowledge catalogue with strict trust signals, provenance tracking, and graph-based linking between concepts.

## ✨ Key Features

- **OKF v0.2 Compliance**: Strict adherence to the OKF standard for metadata structure, trust tiers, and provenance
- **Knowledge Graph**: Directed graph of interconnected concepts using `networkx`, persisted in SQLite
- **Trust Signals**: First-class support for `generated`, `verified`, `sources`, and `stale_after` fields
- **MCP Tools**: Ready-to-use tools for creating, linking, searching, verifying, and querying knowledge nodes
- **SQLite Persistence**: Lightweight, file-based storage with WAL mode and foreign key enforcement

## 🛠 MCP Tools

| Tool | Description |
|---|---|
| `create_kyo_node` | Create a new OKF concept node with type, title, description, tags, trust signals, and optional resource URI |
| `link_kyo_nodes` | Connect two nodes with a directed edge (supports relation types like `references`, `derives_from`) |
| `search_knowledge` | Search the catalogue by keyword + type filter; returns results with trust tier info |
| `verify_kyo_node` | Mark a node as human-verified, advancing its trust tier to "Human-Reviewed" |
| `get_node_trust_status` | Retrieve full provenance & trust metadata for any node by ID |

## 🏗 Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌──────────────────────┐
│   MCP Clients    │────▶│  mcp_server.py   │────▶│   networkx.DiGraph   │
│ (LLM Agents)    │     │  (FastMCP Tools) │     │  (In-memory Graph)   │
└─────────────────┘     └────────┬─────────┘     └──────────┬───────────┘
                                 │                           │
                          ┌──────▼─────────┐        ┌────────▼──────────┐
                          │  okf_schema.py  │        │   database.py      │
                          │ (Pydantic Types)│        │   SQLite .db       │
                          └─────────────────┘        └────────────────────┘
```

- **In-Memory Graph**: All links and topology live in a `networkx.DiGraph` for fast traversal. Rebuilt from DB on startup.
- **SQLite Backend**: Single `kyo_catalog.db` file stores concepts and edges. Complex OKF fields (`generated`, `verified`, `sources`) are serialized as JSON in the `metadata` column.
- **OKF Schema Enforcement**: Pydantic models strictly validate all inputs against OKF v0.2 requirements before persistence.

## 📁 Project Structure

```
pkg/
├── pyproject.toml            # Hatch metadata & dependencies
├── main.py                   # CLI entry point (runs uvicorn)
├── start_http.py             # HTTP wrapper for FastAPI/MCP
├── test.py                   # Unit tests
├── kyo_catalog.db            # SQLite data store
├── src/kyo_mcp/
│   ├── __init__.py           # Package init
│   ├── mcp_server.py         # Core logic: MCP tools, graph ops, trust tiers
│   ├── okf_schema.py         # OKF v0.2 Pydantic models (OKFConcept, etc.)
│   └── database.py           # SQLite persistence layer
└── .env/                     # uv virtual environment
```

## 🚀 Installation & Usage

### Prerequisites
- Python 3.13+
- `uv` package manager

### Setup
```bash
cd pkg
uv sync    # Installs dependencies into .venv
```

### Run the MCP Server
```python
# Via main.py
python -m main

# Or directly via uvicorn
uv run python start_http.py
```

### Example: Creating a Knowledge Node
```python
from kyo_mcp.mcp_server import create_kyo_node

await create_kyo_node(
    title="Example Dataset",
    description="A sample dataset for testing OKF compatibility.",
    resource_uri="https://example.com/data",
    concept_type="dataset",
    tags=["sample", "okf-v0.2"],
    sources=[
        {"id": "src-001", "resource": "https://example.com/source", "title": "Primary Source"}
    ]
)
# → Node created successfully: kyo-node-0000 (Example Dataset) | Trust Tier: Unverified
```

## 🔐 Trust & Provenance

OKF v0.2 treats trust as a first-class concern. This implementation enforces it via:

- **`generated`**: Always auto-set on creation by `process:kyo-mcp` + ISO timestamp
- **`verified`**: Empty by default; populated via `verify_kyo_node` with `human:<actor>` prefix
- **Trust Tiers**:
  - `Unverified`: New nodes with no verification events
  - `Machine-Confirmed`: Verification by a process/agent (`process:...`)
  - `Human-Reviewed`: Verification by a person (`human:...`)
- **`stale_after`**: Optional lifecycle signal to mark when content should be re-evaluated

## 📦 Dependencies

| Package | Purpose |
|---|---|
| `mcp>=1.28.1` | Model Context Protocol server/runtime |
| `fastapi>=0.140.0` | HTTP framework for MCP transport |
| `networkx>=3.6.1` | Knowledge graph data structure & traversal |
| `pydantic>=2.13.4` | Schema validation for OKF concepts |
| `uvicorn>=0.51.0` | ASGI server |
| `pyyaml>=6.0.3` | YML serialization (concept markdown) |
| `graphviz>=0.21` | Optional graph visualization exports |

## 🔄 OKF Alignment

This project mirrors key concepts from the Google OKF spec:

- ✅ **Progressive Disclosure**: Each concept is a standalone, queryable node
- ✅ **Trust Signals**: `generated`, `verified`, `sources`, and `stale_after` match v0.2 frontmatter requirements
- ✅ **Graph-Shaped Metadata**: Links in the catalogue reflect markdown cross-references from OKF bundles
- ✅ **Vendor-Neutral Storage**: SQLite + JSON is the simplest possible transport; easily exportable to Git/OKF bundles

## 📄 License

Private / Confidential — Kyo Project © 2025
