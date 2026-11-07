# Kyo - Knowledge Graph MCP Server

**MCP 2026-07-28 | Mnemosyne | Hindsight | OKF v0.2**

Kyo (経) is a knowledge graph MCP server whose catalogue is a directory of Google's Open Knowledge Format (OKF) v0.2 files, with integrated spaced repetition (Mnemosyne) and AI-powered fact extraction (Hindsight). It runs as a stateless MCP server using the new Anthropic MCP 2026-07-28 protocol.

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

- **OKF v0.2 bundles**: Exports and imports conformant OKF v0.2 bundles (directories of markdown files), with trust, provenance, and freshness metadata. See [OKF v0.2 conformance](#okf-v02-conformance)
- **MCP 2026-07-28**: Stateless protocol, MRTR, Streamable HTTP transport
- **Mnemosyne Integration**: Spaced repetition for long-term knowledge retention
- **Hindsight Integration**: AI-powered fact extraction and semantic search via a local LLM
- **Full-Text Search**: SQLite FTS5 over titles, descriptions, tags, and bodies, ranked by relevance
- **Ontology Layer**: SKOS/DCMI type mappings with Turtle RDF export (`get_kyo_node` with `format="turtle"`)
- **Files first**: the catalogue is a directory of OKF markdown files you can edit, diff, and sync; SQLite + NetworkX index it for search and path finding

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
│   │   ├── database.py    # SQLite index: schema, FTS5 search, sync state
│   │   ├── okf_schema.py  # OKF v0.2 Pydantic models
│   │   ├── store.py       # The bundle store: files are the source of truth
│   │   ├── bundle_format.py # OKF paths, ids, and links
│   │   ├── bundle.py      # OKF bundle export, import, validation
│   │   ├── ontology.py    # SKOS/Dublin Core + RDF export
│   │   ├── bridge.py      # Mnemosyne + Hindsight sync
│   │   └── __init__.py
│   ├── tests/             # Test suite
│   ├── pyproject.toml
│   └── README.md
├── docs/
│   ├── OKF.md             # OKF overview (reference agent README)
│   ├── SPEC.md            # OKF v0.2 specification, vendored from upstream
│   ├── THIRD_PARTY.md     # Source and license of vendored files
│   └── LICENSE.knowledge-catalog
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

Kyo's catalogue **is** an [OKF v0.2](docs/SPEC.md) bundle (SPEC.md as of
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
| `KYO_REFLECT_TIMEOUT`    | Seconds to wait for a Hindsight reflect (default: `1200`); keep it above the server's `HINDSIGHT_API_REFLECT_WALL_TIMEOUT`        |

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
