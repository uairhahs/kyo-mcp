# Kyo MCP

A FastAPI-based MCP (Model Context Protocol) server that implements the [Open Knowledge Format (OKF) v0.2 specification](https://openknowledge.network/). It persists a knowledge graph in SQLite and exposes it via MCP tools and a web API.

## Quick Start

```bash
python -m uvicorn start_http:app --host 0.0.0.0 --port 8000
```

Or use the package entry point:

```bash
pip install -e .
kyo-mcp
```

## Architecture

- **Package**: `src/kyo_mcp/` — core modules
- **Entry point**: `main.py` — runs uvicorn with `start_http:app`
- **App**: `start_http.py` — mounts the MCP server on the FastAPI app at `/mcp/`
- **Database**: SQLite with tables for concepts and links

## MCP Tools

| Tool                    | Description                                      |
| ----------------------- | ------------------------------------------------ |
| `create_kyo_node`       | Create a new concept node in the knowledge graph |
| `link_kyo_nodes`        | Create a relationship between two nodes          |
| `search_knowledge`      | Search concepts by keyword                       |
| `verify_kyo_node`       | Mark a node as human-verified                    |
| `get_node_trust_status` | Get verification status of a node                |

## Web API

The server exposes a FastAPI app with the following endpoints:

- `GET /` — Index page
- `POST /mcp/` — MCP protocol endpoint

## Configuration

Set via environment variables:

- `KYO_DATA_DIR` — Directory for SQLite database (default: `/var/lib/kyo`)
- `DATABASE_URL` — Database connection string (default: `sqlite:///kyo.db` relative to data dir)

## OKF Compliance

Implements the OKF v0.2 specification with:

- Concept nodes with metadata, tags, and verification status
- Link relationships between concepts
- Trust signals (human-verified status)
- Provenance tracking
- Resource URIs

## Project Structure

```
pkg/
├── main.py                 # Uvicorn entry point
├── start_http.py           # FastAPI app with MCP server
├── src/
│   └── kyo_mcp/
│       ├── __init__.py     # Graph instance & data dir setup
│       ├── database.py     # SQLite persistence layer
│       ├── okf_schema.py   # OKF v0.2 schema models
│       └── mcp_server.py   # MCP tool implementations
└── tests/                  # Test suite
```

## Development

```bash
# Run tests
pytest pkg/tests/

# Run with auto-reload
uvicorn start_http:app --reload
```
