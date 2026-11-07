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
| `refresh_okf_bundle`          | Re-read the store's files and report edits, errors, conflicts    |

Failures are returned as MCP tool errors (`isError: true`), not as
successful results containing error text.

## OKF v0.2 conformance

Kyo's catalogue **is** an [OKF v0.2](../docs/SPEC.md) bundle (SPEC.md as of
upstream commit `62432a0`): a directory of markdown concept files. SQLite is
only an index derived from it, for search, the link graph, and Kyo's own
sync state. Bundles Kyo writes are conformant under §11, and it reads any
conformant bundle. This is tested against Google's own sample bundles
(`pkg/tests/fixtures/okf/`), which import and export byte for byte.

- **The bundle is the store**: every node is a file at
  `$KYO_BUNDLE_DIR/<type>/<title>.md` (default: a `bundle` directory next to
  the database). Its path is its OKF concept ID (§2) and doesn't change
  when the node is edited. Kyo writes the file first and then indexes it.
- **Edit the files directly**: in an editor, with git, or through a sync
  tool. Every MCP tool and CLI command first re-reads changed files
  (`refresh_okf_bundle` / `kyo-cli refresh` shows the report). A file that
  doesn't parse keeps its last indexed version until it's fixed. Deleting a
  file deletes the node. A new file needs no id: one is derived from its
  path, so every device indexing the same bundle agrees on it. Kyo writes
  `kyo_id` into a file when it next changes it, so the node keeps its id if
  the file is moved later.
- **Bundles**: `export_okf_bundle` / `kyo-cli export_bundle DIR` writes a
  standalone copy, with the concept files copied byte for byte plus an
  `index.md` per directory (§8) and `okf_version: "0.2"` at the root.
  `import_okf_bundle` / `kyo-cli import_bundle DIR` copies a bundle's files
  into the store byte for byte, tolerating unknown types and keys, missing
  index files, and broken links (§11). `validate_okf_bundle` /
  `kyo-cli validate_bundle DIR` checks the §11 rules.
- **Links**: links between concepts are standard markdown links (§6.1),
  which become `references` edges. OKF links are untyped, so Kyo's typed
  links are kept in a `kyo_links` extension key, and also written as
  relative links in a generated `## Links` section so plain OKF consumers
  see the edge.
- **Trust, provenance, lifecycle (§5)**: `generated`, `verified` (a bare
  mapping is read as one entry), trust tiers derived per §5.3, `sources`
  with their credibility signals, `status`, and `stale_after`. Timestamps
  are ISO 8601 with an explicit offset.
- **Extensions (§4.1)**: unknown frontmatter keys are kept. Kyo adds
  `kyo_id`, `kyo_links`, and `ontology`.

Limits:

- **Attested Computations (§10)** are stored and round-tripped, but Kyo
  doesn't run executors or attesters, so it can't surface a failing
  attestation (§11 asks consumers to).
- **Kyo rewrites a file it changes**: a file edited through Kyo's tools is
  re-serialised (keys and values are kept, but YAML layout and key order
  are Kyo's, an absent `status` becomes `stable`, and a bare `verified`
  mapping becomes a one-entry list). Files Kyo doesn't change are left
  exactly as written.
- **Index files**: `index.md` and `log.md` in the store aren't read as
  concepts. Kyo generates `index.md` files on export only, and writes no
  `log.md` (optional, §9).
- **Concurrent edits** to one file from two devices are left to the sync
  tool. If it produces a conflict copy, the copy is indexed as a separate
  node, with a warning if it repeats a `kyo_id`.

## Configuration

| Variable                 | Purpose                                                                                                                           |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------- |
| `KYO_DB_PATH`            | Full path of the SQLite database file                                                                                             |
| `KYO_DATA_DIR`           | Directory for `kyo_catalog.db`, used when `KYO_DB_PATH` is unset (default: the platform user data dir, e.g. `~/.local/share/kyo`) |
| `KYO_BUNDLE_DIR`         | The bundle that is the store, for the default database (default: `bundle/` next to the database)                                  |
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
uv run kyo-cli refresh                         # re-read edited files, show the report
```

`python kyo_cli.py ...` still works as a compatibility shim.

## Project Structure

```text
pkg/
├── kyo_mcp/
│   ├── mcp_server.py       # MCP 2026-07-28 server (MCPServer)
│   ├── cli.py              # kyo-cli command
│   ├── database.py         # SQLite index: schema, FTS5 search, sync state
│   ├── okf_schema.py       # OKF v0.2 Pydantic models
│   ├── store.py            # The bundle store: files are the source of truth
│   ├── bundle_format.py    # OKF paths, ids, and links
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
