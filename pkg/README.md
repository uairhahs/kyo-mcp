# Kyo MCP Server

A knowledge graph MCP server implementing the [Open Knowledge Format (OKF) v0.2](https://openknowledge.network/) specification with integrated spaced repetition (Mnemosyne) and AI-powered fact extraction (Hindsight).

## Architecture

```text
┌─────────────────────────────────────────────────────────┐
│                     MCP Client                           │
└──────────────────────┬──────────────────────────────────┘
                       │ stdio / streamable-http
                       ▼
┌─────────────────────────────────────────────────────────┐
│                   Kyo MCP Server                         │
│  ┌───────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐  │
│  │  Bridge   │ │ Database │ │  Ontology│ │  OKF     │  │
│  │ (Sync)    │ │ (SQLite) │ │ (RDF)    │ │ Schema   │  │
│  └─────┬─────┘ └────┬─────┘ └──────────┘ └──────────┘  │
│        │            │                                     │
│        ▼            ▼                                     │
│  ┌──────────┐  ┌──────────┐                               │
│  │ Mnemosyne│  │Hindsight │                               │
│  │(SRS)     │  │(LLM)     │                               │
│  └──────────┘  └──────────┘                               │
└─────────────────────────────────────────────────────────┘
```

## Quick Start

```bash
# Run tests
cd pkg && uv run pytest tests/ -v

# Start MCP server (stdio - default)
uv run python -m kyo_mcp.mcp_server

# Start MCP server (streamable HTTP on port 8000)
uv run python -m kyo_mcp.mcp_server --transport streamable-http --port 8000

# Start MCP server (SSE)
uv run python -m kyo_mcp.mcp_server --transport sse --port 8000

# Or run directly with uvx, no clone needed
uvx --from "git+https://github.com/uairhahs/kyo-mcp#subdirectory=pkg" kyo-mcp
```

Assumes Hindsight is reachable at `http://localhost:8888`; set
`HINDSIGHT_API_BASE_URL` first if it's deployed elsewhere (see
[Integration](#integration) below).

## Transport Options

| Transport         | Use Case                  | Command                                                                       |
| ----------------- | ------------------------- | ----------------------------------------------------------------------------- |
| `stdio`           | Local development         | `uv run python -m kyo_mcp.mcp_server`                                         |
| `streamable-http` | Remote access, production | `uv run python -m kyo_mcp.mcp_server --transport streamable-http --port 8000` |
| `sse`             | Server-Sent Events        | `uv run python -m kyo_mcp.mcp_server --transport sse --port 8000`             |

## MCP Tools

| Tool                         | Description                               |
| ---------------------------- | ----------------------------------------- |
| `create_kyo_node`            | Create a new OKF-compliant knowledge node |
| `link_kyo_nodes`             | Connect nodes with directed edges         |
| `search_knowledge`           | Semantic search across the catalogue      |
| `verify_kyo_node`            | Mark a node as human-verified             |
| `get_node_trust_status`      | Get trust/provenance metadata             |
| `sync_to_mnemosyne`          | Sync to spaced repetition system          |
| `sync_to_hindsight`          | Sync for AI-powered fact extraction       |
| `recall_from_hindsight`      | Semantic search via Hindsight             |
| `trigger_reflection`         | Generate insights via Hindsight           |
| `trigger_consolidation`      | Strengthen memory associations            |
| `sync_all_to_memory_systems` | Batch sync all concepts                   |

## OKF v0.2 Compliance

- **Trust Signals**: `generated` (always), optional `verified`, `sources`
- **Freshness**: `stale_after` metadata
- **Provenance**: Tracking of node creation and verification
- **Schema**: Pydantic models for OKF concepts

## Configuration

Environment variables:

- `KYO_DATA_DIR`: Directory for SQLite database (default: current directory)
- `DATABASE_URL`: Database connection string (default: `sqlite:///kyo.db`)

## Project Structure

```text
pkg/
├── kyo_mcp/
│   ├── mcp_server.py       # MCP 2026-07-28 server (MCPServer)
│   ├── database.py         # SQLite schema + NetworkX graph
│   ├── okf_schema.py       # OKF v0.2 Pydantic models
│   ├── ontology.py         # SKOS/Dublin Core + RDF export
│   ├── bridge.py           # Mnemosyne + Hindsight sync
│   └── __init__.py
├── tests/                  # Test suite (103 passing)
├── pyproject.toml
└── README.md
```

## Development

```bash
# Run tests
uv run pytest tests/ -v

# Run linting
trunk check --fix
```

## Integration

### Hindsight

Runs on `localhost:8888` (API) and `localhost:9999` (UI) by default for semantic search and
reflection. Set `HINDSIGHT_API_BASE_URL` if it's deployed elsewhere, and
`HINDSIGHT_API_KEY` if it requires authentication (e.g. a hosted service
like Hindsight Cloud), sent as `Authorization: Bearer <key>` when set and
omitted entirely otherwise. See the root README's "Hindsight Setup"
section for details, including running without Hindsight at all.

## License

MIT
