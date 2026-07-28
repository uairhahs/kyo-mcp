"""
Standard SQLite Persistence Layer for Kyo Knowledge Catalogue.
Handles persistence of OKF concepts via a single `.db` file without external binary wheels.
Aligned with OKF v0.2 requirements for provenance and trust signals.
"""

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "kyo_catalog.db"
conn = None

def get_connection() -> sqlite3.Connection:
    global conn
    if conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=30.0)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA foreign_keys = ON;")
        # Extended schema to match OKF v0.2 structural requirements
        cur.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_concepts (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                title TEXT,
                description TEXT,
                resource TEXT,       -- Stores plain URI/URL as per OKF spec
                status TEXT DEFAULT 'stable',
                tags TEXT,           -- JSON serialized list
                metadata TEXT,       -- JSON serialized dynamic fields (generated, sources, stale_after)
                body_text TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_type ON knowledge_concepts(type);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_status ON knowledge_concepts(status);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_updated_at ON knowledge_concepts(updated_at);")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_links (
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (source_id, target_id, relation_type),
                FOREIGN KEY (source_id) REFERENCES knowledge_concepts(id),
                FOREIGN KEY (target_id) REFERENCES knowledge_concepts(id)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_link_source ON knowledge_links(source_id);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_link_target ON knowledge_links(target_id);")
        conn.commit()
    return conn


def create_concept(
    concept_dict: Dict[str, Any], markdown_body: str = "", update_existing: bool = True
) -> None:
    conn = get_connection()

    # 1. Defensive copy to prevent mutating the original object (e.g., from MCP inputs)
    data = dict(concept_dict)

    # 2. OKF v0.2 Field Extraction
    tags_json = json.dumps(data.get("tags") or [])

    # Handle resource: ensure we store a plain string/URL, not a dict
    res_source = data.get("resource")
    if isinstance(res_source, dict):
        resource_str = res_source.get("uri") or res_source.get("url")
    else:
        resource_str = str(res_source) if res_source else None

    # Extract complex OKF fields for metadata JSON storage
    generated_entry = data.pop("generated", None)
    verified_list = data.pop("verified", [])
    sources_list = data.pop("sources", [])
    stale_after_str = data.pop("stale_after", None)

    # Construct enriched metadata object per OKF v0.2 §5 (Provenance/Trust/Lifecycle)
    metadata_obj = {
        "generated": generated_entry,
        "verified": verified_list,
        "sources": sources_list,
        "stale_after": stale_after_str,
    }

    # Filter out None values to keep JSON clean
    metadata_json = json.dumps({k: v for k, v in metadata_obj.items() if v is not None})

    query = """
        INSERT INTO knowledge_concepts
        (id, type, title, description, resource, status, tags, metadata, body_text, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """

    if update_existing:
        query += (
            " ON CONFLICT(id) DO UPDATE SET\n"
            "title = EXCLUDED.title,\n resource = EXCLUDED.resource,\n type = EXCLUDED.type,\n status = EXCLUDED.status,\n tags = EXCLUDED.tags,\n metadata = EXCLUDED.metadata,\n body_text = EXCLUDED.body_text\n"
        )

    conn.execute(
        query,
        (
            data.get("id"),
            data["type"],
            data.get("title"),
            data.get("description"),
            resource_str,
            data.get("status", "stable"),
            tags_json,
            metadata_json,
            markdown_body,
        ),
    )
    conn.commit()


def query_catalog(
    type_filter: Optional[str] = None, search_term: Optional[str] = None
) -> List[Dict[str, Any]]:
    conn = get_connection()
    sql = "SELECT * FROM knowledge_concepts WHERE 1=1"
    params = []
    if type_filter:
        sql += " AND type = ?"
        params.append(type_filter)
    if search_term:
        sql += " AND title LIKE ?"
        params.append(f"%{search_term}%")

    # Execute and convert to list of dicts
    df = conn.execute(sql, params).fetchall()

    results = []
    for row in df:
        row_dict = dict(row)

        # 1. Parse Tags List
        try:
            if isinstance(row_dict.get("tags"), str) and row_dict["tags"]:
                row_dict["tags"] = json.loads(row_dict["tags"])
            else:
                row_dict["tags"] = []
        except (json.JSONDecodeError, TypeError):
            row_dict["tags"] = []

        # 2. Parse Metadata for Trust Signals/Provenance
        parsed_meta = {}
        if isinstance(row_dict.get("metadata"), str) and row_dict["metadata"]:
            try:
                parsed_meta = json.loads(row_dict["metadata"])
            except (json.JSONDecodeError, TypeError):
                parsed_meta = {}

        # 3. Unpack metadata explicitly for easy access
        row_dict["generated"] = parsed_meta.get("generated")
        row_dict["verified"] = parsed_meta.get("verified", [])
        row_dict["sources"] = parsed_meta.get("sources", [])
        row_dict["stale_after"] = parsed_meta.get("stale_after")

        results.append(row_dict)
    return results


def get_concept_by_id(concept_id: str) -> Optional[Dict[str, Any]]:
    """Fetch a single concept by its exact ID (not a title substring search)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM knowledge_concepts WHERE id = ?", (concept_id,)
    ).fetchone()
    if not row:
        return None

    row_dict = dict(row)

    try:
        if isinstance(row_dict.get("tags"), str) and row_dict["tags"]:
            row_dict["tags"] = json.loads(row_dict["tags"])
        else:
            row_dict["tags"] = []
    except (json.JSONDecodeError, TypeError):
        row_dict["tags"] = []

    parsed_meta = {}
    if isinstance(row_dict.get("metadata"), str) and row_dict["metadata"]:
        try:
            parsed_meta = json.loads(row_dict["metadata"])
        except (json.JSONDecodeError, TypeError):
            parsed_meta = {}

    row_dict["generated"] = parsed_meta.get("generated")
    row_dict["verified"] = parsed_meta.get("verified", [])
    row_dict["sources"] = parsed_meta.get("sources", [])
    row_dict["stale_after"] = parsed_meta.get("stale_after")

    return row_dict


def update_node_verified(node_id: str, new_verified_actor: dict) -> bool:
    """Update the verified list for a node (OKF Trust Tier progression)."""
    conn = get_connection()
    row = conn.execute(
        "SELECT metadata FROM knowledge_concepts WHERE id = ?", (node_id,)
    ).fetchone()
    if not row:
        return False

    meta = json.loads(row[0]) or {}
    verified_list = meta.get("verified", [])

    # Avoid duplicates
    new_entry = {**new_verified_actor, "by": f"human:{new_verified_actor['by']}"}
    if new_entry not in verified_list:
        verified_list.append(new_entry)

    meta["verified"] = verified_list

    conn.execute(
        "UPDATE knowledge_concepts SET metadata = ? WHERE id = ?",
        (json.dumps(meta), node_id),
    )
    conn.commit()
    return True

def create_link(source_id: str, target_id: str, relation_type: str) -> None:
    """Persist a directed edge between two concepts."""
    conn = get_connection()
    conn.execute(
        """
        INSERT OR IGNORE INTO knowledge_links (source_id, target_id, relation_type)
        VALUES (?, ?, ?)
        """,
        (source_id, target_id, relation_type),
    )
    conn.commit()

def get_all_links() -> List[Dict[str, Any]]:
    """Fetch every persisted edge, used to rebuild the in-memory graph on startup."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT source_id, target_id, relation_type FROM knowledge_links"
    ).fetchall()
    return [dict(row) for row in rows]