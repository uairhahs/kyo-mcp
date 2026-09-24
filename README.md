# Kyo - Knowledge Graph MCP Server

**MCP 2026-07-28 | Mnemosyne | Hindsight | OKF v0.2**

Kyo (経) is a knowledge graph MCP server that implements Google's Open Knowledge Format (OKF) v0.2 with integrated spaced repetition (Mnemosyne) and AI-powered fact extraction (Hindsight). It runs as a stateless MCP server using the new Anthropic MCP 2026-07-28 protocol.

## Architecture

```ascii
┌─────────────────────────────────────────────────────────┐
│                       MCP Client                          │
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

- **OKF v0.2 Compliance**: Structured knowledge concepts with trust signals, provenance, and freshness metadata
- **MCP 2026-07-28**: Stateless protocol, MRTR, Streamable HTTP transport
- **Mnemosyne Integration**: Spaced repetition for long-term knowledge retention
- **Hindsight Integration**: AI-powered fact extraction and semantic search via a local LLM
- **Full-Text Search**: SQLite FTS5 over titles, descriptions, tags, and bodies, ranked by relevance
- **Ontology Layer**: SKOS/DCMI type mappings with Turtle RDF export (`get_kyo_node` with `format="turtle"`)
- **SQLite + NetworkX**: Lightweight graph storage and path finding without external services

## Quick Start

```bash
# Run tests
cd pkg && uv run pytest tests/ -v

# Start MCP server (stdio)
uv run python -m kyo_mcp.mcp_server

# Start MCP server (streamable-http)
uv run python -m kyo_mcp.mcp_server --transport streamable-http --port 8000
```

### Run without cloning, via uvx

```bash
uvx --from "git+https://github.com/uairhahs/kyo-mcp#subdirectory=pkg" kyo-mcp
uvx --from "git+https://github.com/uairhahs/kyo-mcp#subdirectory=pkg" kyo-mcp --transport streamable-http --port 8000
```

This assumes Hindsight is reachable at `http://localhost:8888`. If it's
running elsewhere, set `HINDSIGHT_API_BASE_URL` before the `uvx` call --
see [Hindsight Setup](#hindsight-setup) below.

## Project Structure

```ascii
kyo/
├── pkg/
│   ├── kyo_mcp/           # Core package
│   │   ├── mcp_server.py  # MCP 2.0 server (MCPServer)
│   │   ├── cli.py         # kyo-cli command
│   │   ├── database.py    # SQLite schema, migrations, FTS5 search
│   │   ├── okf_schema.py  # OKF v0.2 Pydantic models
│   │   ├── ontology.py    # SKOS/Dublin Core + RDF export
│   │   ├── bridge.py      # Mnemosyne + Hindsight sync
│   │   └── __init__.py
│   ├── tests/             # Test suite
│   ├── pyproject.toml
│   └── README.md
├── docs/
│   └── OKF.md             # OKF v0.2 specification
├── README.md
└── .codebase-memory/      # Codebase knowledge graph config
```

## MCP Tools

| Tool                          | Description                                                      |
| ----------------------------- | ---------------------------------------------------------------- |
| `create_kyo_node`             | Create an OKF node (status, `stale_after`, sources, body)        |
| `get_kyo_node`                | Get a node as an OKF markdown bundle file or as Turtle RDF       |
| `update_kyo_node`             | Update some fields of a node, keeping its verification history   |
| `delete_kyo_node`             | Delete a node and its links                                      |
| `link_kyo_nodes`              | Connect two nodes with a directed, typed edge                    |
| `unlink_kyo_nodes`            | Remove links between two nodes                                   |
| `get_node_links`              | List a node's incoming and/or outgoing links                     |
| `find_path`                   | Shortest chain of links between two nodes                        |
| `search_knowledge`            | Full-text search over title, description, tags, and body         |
| `verify_kyo_node`             | Mark a node as human-reviewed                                    |
| `get_node_trust_status`       | Trust tier, freshness, and provenance metadata                   |
| `sync_to_mnemosyne`           | Sync a node to the spaced repetition system                      |
| `sync_to_hindsight`           | Queue a node for Hindsight fact extraction                       |
| `check_hindsight_sync_status` | Check whether a queued Hindsight extraction has finished         |
| `recall_from_hindsight`       | Semantic search across synced concepts                           |
| `trigger_reflection`          | Generate insights from the knowledge base                        |
| `trigger_consolidation`       | Strengthen memory associations                                   |
| `sync_all_to_memory_systems`  | Sync every changed node, and check pending Hindsight extractions |

Failures are returned as MCP tool errors (`isError: true`), not as
successful results containing error text.

## Configuration

| Variable                 | Purpose                                                                                                                           |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------- |
| `KYO_DB_PATH`            | Full path of the SQLite database file                                                                                             |
| `KYO_DATA_DIR`           | Directory for `kyo_catalog.db`, used when `KYO_DB_PATH` is unset (default: the platform user data dir, e.g. `~/.local/share/kyo`) |
| `HINDSIGHT_API_BASE_URL` | Hindsight API URL (default: `http://localhost:8888`)                                                                              |
| `HINDSIGHT_API_KEY`      | Bearer token for a Hindsight instance that requires auth                                                                          |
| `HINDSIGHT_NAMESPACE`    | Hindsight namespace (default: `default`)                                                                                          |
| `HINDSIGHT_BANK`         | Hindsight memory bank (default: `kyo`)                                                                                            |

Every server process and the CLI can share one database; nothing is cached
in memory, so writes from one are visible to the others immediately. The
schema is migrated automatically on first open.

## Dependencies

- `mcp>=2.0.0`: MCP SDK with Streamable HTTP support
- `networkx>=3.0`: Graph algorithms
- `mnemosyne-memory>=3.0`: Spaced repetition
- `pydantic>=2.0`: Data validation
- `httpx`: Async HTTP for Hindsight (no Hindsight client library needed)
- `platformdirs`, `pyyaml`

## Running as a Service

No system packaging ships with this repo. Build or run `pkg/` with uv
(`uvx`, or `uv tool install` from the repo) and wrap it in whatever your
system uses, e.g. a systemd unit running
`kyo-mcp --transport streamable-http --host 127.0.0.1 --port 8000` with
`KYO_DATA_DIR` pointing at a writable state directory. Note that nixpkgs'
`mcp` is 1.x, too old for this server, so Nix users should build from
`pkg/uv.lock` (e.g. with uv2nix) rather than from nixpkgs.

## Hindsight Setup

Deploy Hindsight however suits your environment (Docker, Kubernetes, bare
metal, or a hosted service like Hindsight Cloud). By default `BridgeLayer`
looks for it at `http://localhost:8888`; if it runs elsewhere, set
`HINDSIGHT_API_BASE_URL` to point at it:

```bash
export HINDSIGHT_API_BASE_URL=http://your-hindsight-host:8888

# Verify
curl "$HINDSIGHT_API_BASE_URL/v1/default/banks" | jq '.[0]'
```

If your Hindsight instance requires authentication (e.g. a hosted service
using API keys), set `HINDSIGHT_API_KEY`:

```bash
export HINDSIGHT_API_BASE_URL=https://your-hosted-hindsight
export HINDSIGHT_API_KEY=your-api-key
```

Every request then carries `Authorization: Bearer <key>`. A self-hosted
Hindsight with no auth of its own doesn't need this set at all: leaving it
unset sends no Authorization header, same as before this option existed.

Hindsight is entirely optional. If `HINDSIGHT_API_BASE_URL` points nowhere
reachable (or you never set it up), the knowledge-graph tools
(`create_kyo_node`, `search_knowledge`, etc.) and Mnemosyne sync work
completely normally, and only the Hindsight-specific tools
(`sync_to_hindsight`, `recall_from_hindsight`, `trigger_reflection`,
`trigger_consolidation`) will fail with a clear error instead of the
server refusing to start.

## License

MIT
