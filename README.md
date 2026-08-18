# Kyo 2.0 — Knowledge Graph MCP Server

**MCP 2026-07-28 | Mnemosyne | Hindsight | OKF v0.2**

Kyo (経) is a knowledge graph MCP server that implements Google's Open Knowledge Format (OKF) v0.2 with integrated spaced repetition (Mnemosyne) and AI-powered fact extraction (Hindsight). It runs as a stateless MCP server using the new Anthropic MCP 2026-07-28 protocol.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     MCP Client                      │
│                    (MCP Client)                          │
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

## Features

- **OKF v0.2 Compliance** — Structured knowledge concepts with trust signals, provenance, and freshness metadata
- **MCP 2026-07-28** — Stateless protocol, MRTR, Streamable HTTP transport
- **Mnemosyne Integration** — Spaced repetition for long-term knowledge retention
- **Hindsight Integration** — AI-powered fact extraction and semantic search via local LLM (Ornith-9B on the-hindsight-host)
- **Ontology Layer** — SKOS/Dublin Core mappings with Turtle RDF export
- **SQLite + NetworkX** — Lightweight graph storage without external dependencies

## Quick Start

```bash
# Run tests
cd pkg && uv run pytest tests/ -v

# Start MCP server (stdio)
uv run python -m kyo_mcp.mcp_server

# Start MCP server (streamable-http)
uv run python -m kyo_mcp.mcp_server --transport streamable-http --port 8000
```

## Project Structure

```
kyo/
├── pkg/
│   ├── kyo_mcp/           # Core package
│   │   ├── mcp_server.py  # MCP 2.0 server (MCPServer)
│   │   ├── database.py    # SQLite schema + NetworkX graph
│   │   ├── okf_schema.py  # OKF v0.2 Pydantic models
│   │   ├── ontology.py    # SKOS/Dublin Core + RDF export
│   │   ├── bridge.py      # Mnemosyne + Hindsight sync
│   │   └── __init__.py
│   ├── tests/             # 106 tests (103 passing)
│   ├── pyproject.toml
│   └── README.md
├── docs/
│   └── OKF.md             # OKF v0.2 specification
├── README.md
└── .codebase-memory/      # Codebase knowledge graph config
```

## MCP Tools

| Tool                         | Description                            |
| ---------------------------- | -------------------------------------- |
| `create_kyo_node`            | Create OKF-compliant knowledge node    |
| `link_kyo_nodes`             | Connect nodes with directed edges      |
| `search_knowledge`           | Semantic search across catalogue       |
| `verify_kyo_node`            | Mark node as human-reviewed            |
| `get_node_trust_status`      | Get trust tier + provenance metadata   |
| `sync_to_mnemosyne`          | Sync node to spaced repetition system  |
| `sync_to_hindsight`          | Extract facts via local LLM            |
| `recall_from_hindsight`      | Semantic search across synced concepts |
| `trigger_reflection`         | Generate insights from knowledge base  |
| `trigger_consolidation`      | Strengthen memory associations         |
| `sync_all_to_memory_systems` | Batch sync all concepts                |

## Dependencies

- `mcp>=2.0.0` — MCP SDK with Streamable HTTP support
- `networkx>=3.0` — Graph algorithms
- `mnemosyne-memory>=3.0` — Spaced repetition
- `hindsight-api>=0.9.0` — AI fact extraction
- `pydantic>=2.0` — Data validation

## Hindsight Setup

Hindsight runs in local mode on the-hindsight-host with Ornith-9B via llama.cpp:

```bash
# Clone and deploy
cd /path/to/hindsight-deployment
docker compose up -d

# Verify
curl http://localhost:8888/v1/default/banks | jq '.[0]'
```

## License

MIT
