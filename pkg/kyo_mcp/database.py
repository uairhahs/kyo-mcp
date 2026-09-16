import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

import appdirs
from kyo_mcp.okf_schema import OKFConcept

DATA_DIR = Path(appdirs.user_data_dir("kyo", "kyo"))
DB_PATH = DATA_DIR / "kyo_catalog.db"
conn: Optional[sqlite3.Connection] = None


def _ensure_data_dir_exists(db_path: Path) -> None:
    """Ensures the parent directories for the DB file exist."""
    db_path.parent.mkdir(parents=True, exist_ok=True)


def _initialize_schema(connection: sqlite3.Connection) -> None:
    cursor = connection.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_concepts (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            title TEXT,
            description TEXT,
            resource TEXT,
            status TEXT DEFAULT 'stable',
            tags TEXT,
            metadata TEXT,
            body_text TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Lightweight migration, no formal migration framework in this project:
    # CREATE TABLE IF NOT EXISTS above is a no-op on an existing table, so a
    # new column needs its own ALTER TABLE, guarded against re-running on a
    # database that already has it (SQLite has no ADD COLUMN IF NOT EXISTS).
    # These track the content hash last successfully synced to each memory
    # system, so bridge.py can skip a resync when nothing has changed
    # (2026-09-09: sync_to_hindsight/sync_to_mnemosyne had no such check and
    # resynced every node on every call, and Hindsight's own dedup only
    # merges near-identical text, so repeated non-deterministic LLM
    # extractions of the same unchanged concept kept landing as fresh,
    # mostly-unmerged noise instead of being recognized as repeat syncs).
    existing_columns = {
        row[1] for row in cursor.execute("PRAGMA table_info(knowledge_concepts)")
    }
    for column in ("hindsight_synced_hash", "mnemosyne_synced_hash"):
        if column not in existing_columns:
            cursor.execute(f"ALTER TABLE knowledge_concepts ADD COLUMN {column} TEXT")
    # Track a Hindsight retain submitted with async=true (2026-09-15: every
    # prior sync_to_hindsight call blocked on Hindsight's full fact-extraction
    # pipeline synchronously, which stalls for as long as the underlying LLM
    # call takes -- confirmed to reach 45+ minutes once, well past bridge.py's
    # own 180s requests timeout, so every such call reliably "failed" client-
    # side regardless of whether Hindsight would have eventually succeeded.
    # Hindsight's own /memories endpoint already supports async=true +
    # operation_id; hindsight_operation_id/hindsight_pending_hash record what
    # was submitted so a later check_hindsight_operation call can poll
    # GET .../operations/{operation_id} and only promote hindsight_pending_hash
    # to hindsight_synced_hash once Hindsight actually reports "completed".
    for column in ("hindsight_operation_id", "hindsight_pending_hash"):
        if column not in existing_columns:
            cursor.execute(f"ALTER TABLE knowledge_concepts ADD COLUMN {column} TEXT")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_type ON knowledge_concepts(type)")
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_status ON knowledge_concepts(status)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_updated_at ON knowledge_concepts(updated_at)"
    )
    cursor.execute("""
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
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_link_source ON knowledge_links(source_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_link_target ON knowledge_links(target_id)"
    )
    connection.commit()


def _open_connection(db_path: Path) -> sqlite3.Connection:
    _ensure_data_dir_exists(db_path)
    connection = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys = ON")
    _initialize_schema(connection)
    return connection


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Return an initialized connection for the default or requested database."""
    global conn
    if db_path is not None:
        return _open_connection(Path(db_path))
    if conn is None:
        conn = _open_connection(Path(DB_PATH))
    return conn


def initialize_db(db_path: Path) -> None:
    """Create the catalogue schema at an explicit database path."""
    connection = get_connection(db_path)
    connection.close()


def create_concept(
    concept_dict: Dict[str, Any],
    markdown_body: str = "",
    update_existing: bool = True,
    db_path: Optional[Path] = None,
) -> None:
    """Validate and persist a concept to the catalogue."""
    validated_concept = OKFConcept.model_validate(concept_dict)
    connection = get_connection(db_path)

    data = validated_concept.model_dump(mode="json")
    tags_json = json.dumps(data.get("tags") or [])

    # Handle resource: ensure we store a plain string/URL
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
            "title = EXCLUDED.title,\n resource = EXCLUDED.resource,\n type = EXCLUDED.type,\n status = EXCLUDED.status,\n tags = EXCLUDED.tags,\n metadata = EXCLUDED.metadata,\n body_text = EXCLUDED.body_text,\n description = EXCLUDED.description\n"
        )

    connection.execute(
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
    connection.commit()


def _deserialize_concept(row: sqlite3.Row) -> Dict[str, Any]:
    row_dict = dict(row)
    try:
        row_dict["tags"] = json.loads(row_dict.get("tags") or "[]")
    except (json.JSONDecodeError, TypeError):
        row_dict["tags"] = []

    try:
        metadata = json.loads(row_dict.get("metadata") or "{}")
    except (json.JSONDecodeError, TypeError):
        metadata = {}

    row_dict["generated"] = metadata.get("generated")
    row_dict["verified"] = metadata.get("verified", [])
    row_dict["sources"] = metadata.get("sources", [])
    row_dict["stale_after"] = metadata.get("stale_after")
    # OKFConcept.metadata is a genuine Dict[str, Any] field, separate from
    # generated/verified/sources/stale_after above. It must not be left as
    # the raw SQLite TEXT column, or OKFConcept.model_validate() fails with
    # "Input should be a valid dictionary" on every row.
    row_dict["metadata"] = metadata
    return row_dict


def query_catalog(
    type_filter: Optional[str] = None,
    search_term: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    connection = get_connection(db_path)
    sql = "SELECT * FROM knowledge_concepts WHERE 1=1"
    params = []
    if type_filter:
        sql += " AND type = ?"
        params.append(type_filter)
    if search_term:
        sql += " AND title LIKE ?"
        params.append(f"%{search_term}%")

    rows = connection.execute(sql, params).fetchall()
    return [_deserialize_concept(row) for row in rows]


def get_concept_by_id(
    concept_id: str, db_path: Optional[Path] = None
) -> Optional[Dict[str, Any]]:
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT * FROM knowledge_concepts WHERE id = ?", (concept_id,)
    ).fetchone()
    if not row:
        return None

    return _deserialize_concept(row)


def update_node_verified(
    node_id: str, new_verified_actor: dict, db_path: Optional[Path] = None
) -> bool:
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT metadata FROM knowledge_concepts WHERE id = ?", (node_id,)
    ).fetchone()
    if not row:
        return False

    try:
        meta = json.loads(row[0]) or {}
    except json.JSONDecodeError:
        meta = {}

    verified_list = meta.get("verified", [])

    # Avoid duplicates
    new_entry = {
        **new_verified_actor,
        "by": f"human:{new_verified_actor.get('by', '')}",
    }
    if new_entry not in verified_list:
        verified_list.append(new_entry)

    meta["verified"] = verified_list

    conn.execute(
        "UPDATE knowledge_concepts SET metadata = ? WHERE id = ?",
        (json.dumps(meta), node_id),
    )
    conn.commit()
    return True


_SYNC_HASH_COLUMNS = {
    "hindsight": "hindsight_synced_hash",
    "mnemosyne": "mnemosyne_synced_hash",
}


def get_sync_hash(
    node_id: str, system: str, db_path: Optional[Path] = None
) -> Optional[str]:
    """Return the content hash last successfully synced to `system`
    ("hindsight" or "mnemosyne") for this node, or None if it has never
    been synced (or the node doesn't exist)."""
    # column is whitelisted in _SYNC_HASH_COLUMNS, cannot be user-controlled
    column = _SYNC_HASH_COLUMNS[system]
    conn = get_connection(db_path)
    row = conn.execute(
        f"SELECT {column} FROM knowledge_concepts WHERE id = ?",  # nosec B608
        (node_id,),
    ).fetchone()
    return row[0] if row else None


def set_sync_hash(
    node_id: str, system: str, content_hash: str, db_path: Optional[Path] = None
) -> None:
    """Record that `content_hash` is what's currently synced to `system`
    for this node, so a future sync with the same hash can be skipped."""
    # column is whitelisted in _SYNC_HASH_COLUMNS, cannot be user-controlled
    column = _SYNC_HASH_COLUMNS[system]
    conn = get_connection(db_path)
    conn.execute(
        f"UPDATE knowledge_concepts SET {column} = ? WHERE id = ?",  # nosec B608
        (content_hash, node_id),
    )
    conn.commit()


def get_hindsight_operation(
    node_id: str, db_path: Optional[Path] = None
) -> Optional[tuple[str, str]]:
    """Return (operation_id, pending_content_hash) for an in-flight async
    Hindsight retain submitted for this node, or None if there isn't one."""
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT hindsight_operation_id, hindsight_pending_hash "
        "FROM knowledge_concepts WHERE id = ?",
        (node_id,),
    ).fetchone()
    if not row or not row[0]:
        return None
    return (row[0], row[1])


def set_hindsight_operation(
    node_id: str,
    operation_id: str,
    pending_hash: str,
    db_path: Optional[Path] = None,
) -> None:
    """Record that `operation_id` is the in-flight async Hindsight retain
    for this node's `pending_hash` content, so a later status check knows
    what to poll for and what to promote to hindsight_synced_hash on
    completion."""
    conn = get_connection(db_path)
    conn.execute(
        "UPDATE knowledge_concepts "
        "SET hindsight_operation_id = ?, hindsight_pending_hash = ? "
        "WHERE id = ?",
        (operation_id, pending_hash, node_id),
    )
    conn.commit()


def clear_hindsight_operation(node_id: str, db_path: Optional[Path] = None) -> None:
    """Clear a node's in-flight Hindsight operation tracking, once it's
    resolved (completed, failed, cancelled, or found stale) -- leaving it
    set would make every later sync_to_hindsight call think one is still
    pending and refuse to submit a fresh one."""
    conn = get_connection(db_path)
    conn.execute(
        "UPDATE knowledge_concepts "
        "SET hindsight_operation_id = NULL, hindsight_pending_hash = NULL "
        "WHERE id = ?",
        (node_id,),
    )
    conn.commit()


def create_link(
    source_id: str, target_id: str, relation_type: str, db_path: Optional[Path] = None
) -> None:
    conn = get_connection(db_path)
    conn.execute(
        """
        INSERT OR IGNORE INTO knowledge_links (source_id, target_id, relation_type)
        VALUES (?, ?, ?)
        """,
        (source_id, target_id, relation_type),
    )
    conn.commit()


def get_all_links(db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT source_id, target_id, relation_type FROM knowledge_links"
    ).fetchall()
    return [dict(row) for row in rows]
