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
| `materialize_from_hindsight`  | Pull recalled Hindsight memories back in as catalogue nodes      |
| `trigger_reflection`          | Generate insights from the knowledge base                        |
| `trigger_consolidation`       | Strengthen memory associations                                   |
| `sync_all_to_memory_systems`  | Sync every changed node, and check pending Hindsight extractions |
| `export_okf_bundle`           | Write the catalogue as an OKF v0.2 bundle directory              |
| `import_okf_bundle`           | Load an OKF bundle directory into the catalogue                  |
| `validate_okf_bundle`         | Check a directory against OKF v0.2 conformance (§11)             |

Failures are returned as MCP tool errors (`isError: true`), not as
successful results containing error text.

## OKF v0.2 conformance

Kyo reads and writes [OKF v0.2](../docs/SPEC.md) bundles (SPEC.md as of
upstream commit `62432a0`). Bundles it exports are conformant under §11,
and it consumes any conformant bundle. This is tested against Google's own
sample bundles (`pkg/tests/fixtures/okf/`): each one is imported and
exported again, and every concept file comes back at the same path with
the same body and the same frontmatter keys and values.

- **Bundles**: `export_okf_bundle` / `kyo-cli export_bundle DIR` writes one
  markdown file per node plus an `index.md` per directory (§8), with
  `okf_version: "0.2"` at the root. A node's file path is its OKF concept
  ID (§2) and stays the same across exports. `import_okf_bundle` /
  `kyo-cli import_bundle DIR` loads one, tolerating unknown types and keys,
  missing index files, and broken links (§11). `validate_okf_bundle` /
  `kyo-cli validate_bundle DIR` checks the §11 rules.
- **Links**: links between concepts are standard markdown links (§6.1).
  OKF links are untyped, so Kyo's typed links are kept in a `kyo_links`
  extension key, and also written as ordinary links in a generated
  `## Links` section so plain OKF consumers see the edge.
- **Trust, provenance, lifecycle (§5)**: `generated`, `verified` (a bare
  mapping is read as one entry), trust tiers derived per §5.3, `sources`
  with their credibility signals, `status`, and `stale_after`. Timestamps
  are ISO 8601 with an explicit offset.
- **Extensions (§4.1)**: unknown frontmatter keys are preserved through
  storage and export. Kyo adds `kyo_id` (its node id), `kyo_links`, and
  `ontology`.

Limits:

- **Attested Computations (§10)** are stored and round-tripped, but Kyo
  doesn't run executors or attesters, so it can't surface a failing
  attestation (§11 asks consumers to).
- **Semantically lossless, not byte-identical**: keys and values survive,
  but YAML layout and key order are rewritten, an absent `status` is
  written as `stable`, and a bare `verified` mapping as a one-entry list.
- **`index.md` and `log.md` aren't imported**: on export, Kyo generates
  its own index files and writes no `log.md` (optional, §9).
- **The SQLite catalogue is the store**: bundles are exported from and
  imported into it, rather than being the store themselves.

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
uv run kyo-cli export_bundle ~/kyo-bundle      # --overwrite to refresh it
uv run kyo-cli import_bundle path/to/bundle
uv run kyo-cli validate_bundle path/to/bundle  # exits 1 if not conformant
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
│   ├── bundle.py           # OKF bundle export, import, validation
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
