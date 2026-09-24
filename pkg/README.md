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

## OKF v0.2 Compliance

- **Trust Signals**: `generated` (always), optional `verified`, `sources`
- **Lifecycle**: `status` (`draft`, `stable`, `deprecated`)
- **Freshness**: `stale_after`, reported as Fresh or Stale
- **Provenance**: Tracking of node creation and verification
- **Schema**: Pydantic models for OKF concepts, exported as OKF markdown bundle files

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

## CLI

The `kyo-cli` command works on the same database and prints JSON:

```bash
uv run kyo-cli create_concept "Title" "Summary" --content "# Markdown body"
uv run kyo-cli search_concepts some words --limit 5
uv run kyo-cli get_concept kyo-0123456789ab
uv run kyo-cli sync_all
```

`python kyo_cli.py ...` still works as a compatibility shim.

## Project Structure

```text
pkg/
├── kyo_mcp/
│   ├── mcp_server.py       # MCP 2026-07-28 server (MCPServer)
│   ├── cli.py              # kyo-cli command
│   ├── database.py         # SQLite schema, migrations, FTS5 search
│   ├── okf_schema.py       # OKF v0.2 Pydantic models
│   ├── ontology.py         # SKOS/Dublin Core + RDF export
│   ├── bridge.py           # Mnemosyne + Hindsight sync
│   └── __init__.py
├── tests/                  # Test suite
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
