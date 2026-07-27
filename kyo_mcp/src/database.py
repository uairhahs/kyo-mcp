"""
DuckDB Abstraction Layer for Kyō Knowledge Catalogue.
Handles persistence of OKF concepts via a single SQLite-style `.db` file.
Strictly separates the Index (Database) from the Source of Truth (Markdown Files).
"""

import duckdb
import json
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
import copy

# Configuration: Store catalog in the project root for simplicity during dev
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "kyo_catalog.db"

def get_connection() -> duckdb.DuckDBPyConnection:
    """Get or create the database connection."""
    conn = duckdb.connect(str(DB_PATH))
    _ensure_schema(conn)
    return conn

def _ensure_schema(conn: duckdb.DuckDBPyConnection):
    """Create tables if they do not exist, ensuring alignment with OKF schema"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_concepts (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            title TEXT,
            description TEXT,
            resource TEXT, -- Stores the URI directly if present
            status TEXT DEFAULT 'stable',
            tags TEXT,     -- Stored as JSON array
            metadata TEXT, -- Stores `generated` and `verified` dicts as JSON for querying
            body_text TEXT, -- The Markdown content itself
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

def create_concept(
    concept_dict: Dict[str, Any],
    markdown_body: str = "",
    update_existing: bool = True
) -> None:
    """Store or update an OKF Concept in the database"""
    conn = get_connection()
    
    # Normalize tags and metadata to JSON strings for storage
    tags_json = json.dumps(concept_dict.get("tags", []))
    
    # Extract `generated` and `verified` dict structures from §5 of OKF v0.2 SPEC into a single metadata bucket for DB efficiency
    generated_entry = concept_dict.pop("generated", None)
    verified_list = concept_dict.pop("verified", [])
    
    metadata_json = json.dumps({
        "generated": generated_entry,
        "verified": verified_list
    })

    query = """
        INSERT INTO knowledge_concepts 
        (id, type, title, description, resource, status, tags, metadata, body_text, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """
    
    if update_existing:
        query += " ON CONFLICT(id) DO UPDATE SET\n" \
                 "title = EXCLUDED.title,\n" \
                 "resource = EXCLUDED.resource,\n" \
                 "type = EXCLUDED.type,\n" \
                 "status = EXCLUDED.status,\n" \
                 "tags = EXCLUDED.tags,\n" \
                 "metadata = EXCLUDED.metadata,\n" \
                 "body_text = EXCLUDED.body_text,\n" \
                 "updated_at = CURRENT_TIMESTAMP\n"

    # Reconstruct the full concept dict for passing to the query
    full_concept = concept_dict.copy()
    
    conn.execute(query, (
        full_concept.get("id"),
        full_concept["type"],
        full_concept.get("title"),
        full_concept.get("description"),
        full_concept.get("resource"),
        full_concept.get("status", "stable"),
        tags_json,
        metadata_json,
        markdown_body
    ))
    conn.commit()

def query_catalog(
    type_filter: str = None, 
    search_term: str = None
) -> List[Dict[str, Any]]:
    """Scan the knowledge base using DuckDB's optimized SQL engine"""
    conn = get_connection()
    
    sql = "SELECT * FROM knowledge_concepts WHERE 1=1"
    params = []

    if type_filter:
        sql += " AND type = ?"
        params.append(type_filter)
        
    if search_term:
        sql += " AND (title LIKE ? OR description LIKE ?)"
        like_term = f"%{search_term}%"
        params.extend([like_term, like_term])

    df = conn.execute(sql, params).fetchdf()
    
    results = []
    for _, row in df.iterrows():
        # Parse back into OKF structures before returning to LLM tools
        parsed_metadata = json.loads(row["metadata"]) if row["metadata"] else {}
        tags = json.loads(row["tags"]) if row["tags"] else []

        results.append({
            "id": row["id"],
            "type": row["type"],
            "title": row["title"],
            "description": row["description"],
            "resource": row["resource"],
            "status": row["status"],
            "tags": tags,
            "generated": parsed_metadata.get("generated"),
            "verified": parsed_metadata.get("verified", []),
            "updated_at": str(row["updated_at"])
        })
    return results
