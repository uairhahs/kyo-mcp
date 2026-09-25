import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import platformdirs
from kyo_mcp.okf_schema import OKFConcept

# Explicit override for the default database location. Tests and embedders
# may set this directly; otherwise the path comes from the environment (see
# default_db_path()).
DB_PATH: Optional[Path] = None

# One connection per database file, shared across calls. check_same_thread
# is off because the MCP server may call in from worker threads (see
# bridge.py's asyncio.to_thread use), so writes are serialized by _lock.
_connections: Dict[str, sqlite3.Connection] = {}
_lock = threading.RLock()


def default_db_path() -> Path:
    """Resolve the default database path, checked in this order:
    DB_PATH (module override), $KYO_DB_PATH, $KYO_DATA_DIR/kyo_catalog.db,
    then the platform user data directory."""
    if DB_PATH is not None:
        return Path(DB_PATH)
    if os.environ.get("KYO_DB_PATH"):
        return Path(os.environ["KYO_DB_PATH"])
    data_dir = os.environ.get("KYO_DATA_DIR") or platformdirs.user_data_dir(
        "kyo", "kyo"
    )
    return Path(data_dir) / "kyo_catalog.db"


def _add_columns(cursor: sqlite3.Cursor, columns: List[str]) -> None:
    # SQLite has no ADD COLUMN IF NOT EXISTS, and databases created before
    # PRAGMA user_version tracking may already have some of these columns.
    existing = {
        row[1] for row in cursor.execute("PRAGMA table_info(knowledge_concepts)")
    }
    for column in columns:
        if column not in existing:
            cursor.execute(f"ALTER TABLE knowledge_concepts ADD COLUMN {column} TEXT")


def _migrate_v1(cursor: sqlite3.Cursor) -> None:
    """Base schema: concepts, links, and their indexes."""
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


def _migrate_v2(cursor: sqlite3.Cursor) -> None:
    """Content hash last successfully synced to each memory system, so
    bridge.py can skip a resync when nothing has changed. Without it, every
    sync re-ran Hindsight's non-deterministic LLM extraction on unchanged
    concepts, and its text-similarity dedup let near-duplicate facts pile up."""
    _add_columns(cursor, ["hindsight_synced_hash", "mnemosyne_synced_hash"])


def _migrate_v3(cursor: sqlite3.Cursor) -> None:
    """Track an in-flight Hindsight retain submitted with async=true.
    Synchronous retains block on Hindsight's full LLM extraction pipeline
    (observed at 45+ minutes on a CPU-only backend), so sync_to_hindsight
    submits asynchronously and check_hindsight_operation later promotes
    hindsight_pending_hash to hindsight_synced_hash once Hindsight reports
    the operation "completed"."""
    _add_columns(cursor, ["hindsight_operation_id", "hindsight_pending_hash"])


def _migrate_v4(cursor: sqlite3.Cursor) -> None:
    """Full-text search over title, description, tags, and body, kept in
    step with knowledge_concepts by triggers."""
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
            title, description, tags, body_text,
            content='knowledge_concepts', content_rowid='rowid',
            tokenize='porter unicode61'
        )
    """)
    cursor.executescript("""
        CREATE TRIGGER IF NOT EXISTS knowledge_fts_ai AFTER INSERT ON knowledge_concepts BEGIN
            INSERT INTO knowledge_fts(rowid, title, description, tags, body_text)
            VALUES (new.rowid, new.title, new.description, new.tags, new.body_text);
        END;
        CREATE TRIGGER IF NOT EXISTS knowledge_fts_ad AFTER DELETE ON knowledge_concepts BEGIN
            INSERT INTO knowledge_fts(knowledge_fts, rowid, title, description, tags, body_text)
            VALUES ('delete', old.rowid, old.title, old.description, old.tags, old.body_text);
        END;
        CREATE TRIGGER IF NOT EXISTS knowledge_fts_au AFTER UPDATE ON knowledge_concepts BEGIN
            INSERT INTO knowledge_fts(knowledge_fts, rowid, title, description, tags, body_text)
            VALUES ('delete', old.rowid, old.title, old.description, old.tags, old.body_text);
            INSERT INTO knowledge_fts(rowid, title, description, tags, body_text)
            VALUES (new.rowid, new.title, new.description, new.tags, new.body_text);
        END;
    """)
    # Index rows that existed before this migration.
    cursor.execute("INSERT INTO knowledge_fts(knowledge_fts) VALUES ('rebuild')")


def _migrate_v5(cursor: sqlite3.Cursor) -> None:
    """The last Hindsight retain for each node that failed or was cancelled.
    Hindsight replays an operation_id it has already seen, whatever that
    operation's status, so a retry must be submitted under a new id."""
    _add_columns(cursor, ["hindsight_failed_operation_id"])


# Append-only: each entry upgrades the schema from version i to i + 1, and
# PRAGMA user_version records how many have been applied.
MIGRATIONS: List[Callable[[sqlite3.Cursor], None]] = [
    _migrate_v1,
    _migrate_v2,
    _migrate_v3,
    _migrate_v4,
    _migrate_v5,
]


def _initialize_schema(connection: sqlite3.Connection) -> None:
    cursor = connection.cursor()
    version = cursor.execute("PRAGMA user_version").fetchone()[0]
    for target, migration in enumerate(MIGRATIONS[version:], start=version + 1):
        migration(cursor)
        cursor.execute(f"PRAGMA user_version = {target}")
    connection.commit()


def _open_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys = ON")
    _initialize_schema(connection)
    return connection


def get_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Return the shared, initialized connection for the default or
    requested database, opening it on first use."""
    path = Path(db_path) if db_path is not None else default_db_path()
    key = str(path.resolve())
    with _lock:
        if key not in _connections:
            _connections[key] = _open_connection(path)
        return _connections[key]


def close_connections() -> None:
    """Close every cached connection (used between tests and at shutdown)."""
    with _lock:
        for connection in _connections.values():
            connection.close()
        _connections.clear()


def initialize_db(db_path: Path) -> None:
    """Create the catalogue schema at an explicit database path."""
    get_connection(db_path)


class ConceptExistsError(ValueError):
    """Raised by create_concept(update_existing=False) on an id collision."""


def _concept_row(
    validated: OKFConcept, verified: List[Dict[str, Any]]
) -> Dict[str, Any]:
    data = validated.model_dump(mode="json")
    resource = data.get("resource") or {}
    metadata_obj = {
        **(data.get("metadata") or {}),
        "generated": data.get("generated"),
        "verified": verified,
        "sources": data.get("sources") or [],
        "stale_after": data.get("stale_after"),
    }
    return {
        "id": data["id"],
        "type": data["type"],
        "title": data.get("title"),
        "description": data.get("description"),
        "resource": resource.get("uri") or resource.get("url"),
        "status": data.get("status", "stable"),
        "tags": json.dumps(data.get("tags") or []),
        # Drop None values to keep the stored JSON clean.
        "metadata": json.dumps(
            {k: v for k, v in metadata_obj.items() if v is not None}
        ),
    }


def create_concept(
    concept_dict: Dict[str, Any],
    markdown_body: str = "",
    update_existing: bool = True,
    db_path: Optional[Path] = None,
) -> None:
    """Validate and persist a concept to the catalogue.

    With update_existing=False, an existing id raises ConceptExistsError
    instead of being overwritten. With update_existing=True, an existing
    concept is replaced, but its verification history is kept: new
    `verified` entries are appended to the stored ones, not substituted.
    """
    validated = OKFConcept.model_validate(concept_dict)
    if not validated.id:
        raise ValueError("Concept id is required")
    new_verified = [v.model_dump() for v in validated.verified or []]
    connection = get_connection(db_path)

    with _lock, connection:
        existing = connection.execute(
            "SELECT metadata FROM knowledge_concepts WHERE id = ?", (validated.id,)
        ).fetchone()
        if existing and not update_existing:
            raise ConceptExistsError(f"Concept {validated.id} already exists")

        verified = (
            _load_json(existing["metadata"]).get("verified", []) if existing else []
        )
        verified += [v for v in new_verified if v not in verified]
        row = _concept_row(validated, verified)
        row["body_text"] = markdown_body

        if existing:
            connection.execute(
                """
                UPDATE knowledge_concepts SET
                    type = :type, title = :title, description = :description,
                    resource = :resource, status = :status, tags = :tags,
                    metadata = :metadata, body_text = :body_text,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :id
                """,
                row,
            )
        else:
            connection.execute(
                """
                INSERT INTO knowledge_concepts
                (id, type, title, description, resource, status, tags, metadata, body_text)
                VALUES (:id, :type, :title, :description, :resource, :status, :tags,
                        :metadata, :body_text)
                """,
                row,
            )


def update_concept(
    concept_id: str,
    changes: Dict[str, Any],
    markdown_body: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Apply a partial update to a stored concept, re-validating the result.
    Returns the updated concept, or None if it doesn't exist."""
    with _lock:
        current = get_concept_by_id(concept_id, db_path=db_path)
        if current is None:
            return None
        if markdown_body is None:
            markdown_body = current.get("body_text") or ""
        merged = {**current, **changes, "id": concept_id}
        create_concept(merged, markdown_body, update_existing=True, db_path=db_path)
        return get_concept_by_id(concept_id, db_path=db_path)


def delete_concept(concept_id: str, db_path: Optional[Path] = None) -> bool:
    """Delete a concept and every link touching it. Returns False if it
    didn't exist."""
    connection = get_connection(db_path)
    with _lock, connection:
        connection.execute(
            "DELETE FROM knowledge_links WHERE source_id = ? OR target_id = ?",
            (concept_id, concept_id),
        )
        cursor = connection.execute(
            "DELETE FROM knowledge_concepts WHERE id = ?", (concept_id,)
        )
    return cursor.rowcount > 0


def _load_json(raw: Optional[str]) -> Dict[str, Any]:
    try:
        return json.loads(raw or "{}") or {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _deserialize_concept(row: sqlite3.Row) -> Dict[str, Any]:
    row_dict = dict(row)
    try:
        row_dict["tags"] = json.loads(row_dict.get("tags") or "[]")
    except (json.JSONDecodeError, TypeError):
        row_dict["tags"] = []

    metadata = _load_json(row_dict.get("metadata"))
    row_dict["generated"] = metadata.pop("generated", None)
    row_dict["verified"] = metadata.pop("verified", [])
    row_dict["sources"] = metadata.pop("sources", [])
    row_dict["stale_after"] = metadata.pop("stale_after", None)
    # What remains is OKFConcept.metadata: arbitrary extra keys, stored
    # alongside the trust fields above in the same JSON column.
    row_dict["metadata"] = metadata
    row_dict["resource"] = (
        {"uri": row_dict["resource"]} if row_dict.get("resource") else None
    )
    return row_dict


# Dropped from a search query before building the FTS5 MATCH expression.
# search_knowledge's docstring used to say "every word must match", which
# is precise for a deliberate keyword query but returns nothing for a
# conversational one ("what concepts are synced" has no node containing
# all four of those literal words). Stripping stopwords first means the
# strict AND match only has to cover the words that actually carry
# meaning; _fts_terms below still falls back further, to an OR match,
# when even that comes up empty.
_STOPWORDS = frozenset(
    """
    a an the is are was were be been being do does did what which who
    whom this that these those of in on at to for and or but with about
    from as it its we you i me my our us
    """.split()
)


def _fts_terms(search_term: str) -> List[str]:
    """Split free text into search terms, dropping stopwords -- unless
    that would leave nothing to search for, in which case the original
    words are kept so the query still runs (and legitimately finds
    nothing) rather than silently searching for an empty string."""
    words = search_term.split()
    filtered = [w for w in words if w.lower() not in _STOPWORDS]
    return filtered or words


def _fts_match(terms: List[str], match_all: bool) -> str:
    """Build an FTS5 MATCH expression from `terms`, each a quoted prefix
    term so user input can't produce FTS5 query syntax. AND (match_all)
    requires every term to hit; OR requires only one, still bm25-ranked
    so rows matching more terms naturally score above rows matching one."""
    quoted = [f'"{t.replace(chr(34), chr(34) * 2)}"*' for t in terms if t]
    return (" AND " if match_all else " OR ").join(quoted)


def query_catalog(
    type_filter: Optional[str] = None,
    search_term: Optional[str] = None,
    db_path: Optional[Path] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """List concepts, optionally filtered by type and a search over title,
    description, tags, and body, ranked by relevance. A query matching
    every term (as a prefix) is tried first; if that finds nothing and the
    query has more than one word, it's retried requiring only one term to
    match, so a natural-language query still surfaces the closest results
    instead of coming back empty. An empty search_term lists concepts
    newest first."""
    connection = get_connection(db_path)
    terms = _fts_terms(search_term) if search_term else []

    def run(fts: str) -> List[sqlite3.Row]:
        params: List[Any] = []
        if fts:
            sql = (
                "SELECT c.* FROM knowledge_fts f "
                "JOIN knowledge_concepts c ON c.rowid = f.rowid "
                "WHERE knowledge_fts MATCH ?"
            )
            params.append(fts)
        else:
            sql = "SELECT c.* FROM knowledge_concepts c WHERE 1=1"
        if type_filter:
            sql += " AND c.type = ?"
            params.append(type_filter)
        sql += (
            " ORDER BY bm25(knowledge_fts)"
            if fts
            else " ORDER BY c.updated_at DESC, c.id"
        )
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        return connection.execute(sql, params).fetchall()

    rows = run(_fts_match(terms, match_all=True)) if terms else run("")
    if not rows and len(terms) > 1:
        rows = run(_fts_match(terms, match_all=False))
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
    """Record a human verification event. `by` may be given bare ("alice")
    or already prefixed ("human:alice"); either is stored as "human:alice"."""
    conn = get_connection(db_path)
    with _lock, conn:
        row = conn.execute(
            "SELECT metadata FROM knowledge_concepts WHERE id = ?", (node_id,)
        ).fetchone()
        if not row:
            return False

        meta = _load_json(row[0])
        verified_list = meta.get("verified", [])

        actor = new_verified_actor.get("by", "").removeprefix("human:")
        new_entry = {**new_verified_actor, "by": f"human:{actor}"}
        if new_entry not in verified_list:
            verified_list.append(new_entry)
        meta["verified"] = verified_list

        conn.execute(
            "UPDATE knowledge_concepts SET metadata = ? WHERE id = ?",
            (json.dumps(meta), node_id),
        )
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
    with _lock, conn:
        conn.execute(
            f"UPDATE knowledge_concepts SET {column} = ? WHERE id = ?",  # nosec B608
            (content_hash, node_id),
        )


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


def list_pending_hindsight_operations(db_path: Optional[Path] = None) -> List[str]:
    """Return the ids of every node with an in-flight Hindsight retain."""
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT id FROM knowledge_concepts WHERE hindsight_operation_id IS NOT NULL"
    ).fetchall()
    return [row[0] for row in rows]


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
    with _lock, conn:
        conn.execute(
            "UPDATE knowledge_concepts "
            "SET hindsight_operation_id = ?, hindsight_pending_hash = ? "
            "WHERE id = ?",
            (operation_id, pending_hash, node_id),
        )


def clear_hindsight_operation(node_id: str, db_path: Optional[Path] = None) -> None:
    """Clear a node's in-flight Hindsight operation tracking, once it's
    resolved (completed, failed, cancelled, or found stale). Leaving it
    set would make every later sync_to_hindsight call think one is still
    pending and refuse to submit a fresh one."""
    conn = get_connection(db_path)
    with _lock, conn:
        conn.execute(
            "UPDATE knowledge_concepts "
            "SET hindsight_operation_id = NULL, hindsight_pending_hash = NULL "
            "WHERE id = ?",
            (node_id,),
        )


def get_failed_hindsight_operation(
    node_id: str, db_path: Optional[Path] = None
) -> Optional[str]:
    """Return the id of this node's last failed or cancelled Hindsight
    retain, or None."""
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT hindsight_failed_operation_id FROM knowledge_concepts WHERE id = ?",
        (node_id,),
    ).fetchone()
    return row[0] if row else None


def fail_hindsight_operation(
    node_id: str, operation_id: str, db_path: Optional[Path] = None
) -> None:
    """Clear a node's in-flight operation and remember it as failed, so the
    next submission uses a fresh operation_id instead of replaying it."""
    conn = get_connection(db_path)
    with _lock, conn:
        conn.execute(
            "UPDATE knowledge_concepts "
            "SET hindsight_operation_id = NULL, hindsight_pending_hash = NULL, "
            "hindsight_failed_operation_id = ? "
            "WHERE id = ?",
            (operation_id, node_id),
        )


def create_link(
    source_id: str, target_id: str, relation_type: str, db_path: Optional[Path] = None
) -> None:
    conn = get_connection(db_path)
    with _lock, conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO knowledge_links (source_id, target_id, relation_type)
            VALUES (?, ?, ?)
            """,
            (source_id, target_id, relation_type),
        )


def delete_link(
    source_id: str,
    target_id: str,
    relation_type: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> int:
    """Delete links from source to target (only of `relation_type` if
    given). Returns how many were removed."""
    conn = get_connection(db_path)
    sql = "DELETE FROM knowledge_links WHERE source_id = ? AND target_id = ?"
    params: List[Any] = [source_id, target_id]
    if relation_type:
        sql += " AND relation_type = ?"
        params.append(relation_type)
    with _lock, conn:
        return conn.execute(sql, params).rowcount


def get_links(
    node_id: str, direction: str = "both", db_path: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """Links touching a node: "out" (node is the source), "in" (node is the
    target), or "both"."""
    if direction not in ("in", "out", "both"):
        raise ValueError("direction must be 'in', 'out', or 'both'")
    conn = get_connection(db_path)
    clauses = []
    params: List[Any] = []
    if direction in ("out", "both"):
        clauses.append("source_id = ?")
        params.append(node_id)
    if direction in ("in", "both"):
        clauses.append("target_id = ?")
        params.append(node_id)
    rows = conn.execute(
        "SELECT source_id, target_id, relation_type FROM knowledge_links "
        f"WHERE {' OR '.join(clauses)} ORDER BY created_at",  # nosec B608
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def get_all_links(db_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT source_id, target_id, relation_type FROM knowledge_links"
    ).fetchall()
    return [dict(row) for row in rows]
