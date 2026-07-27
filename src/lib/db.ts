import Database from "better-sqlite3";
import path from "path";
import { mkdirSync } from "fs";

const DB_DIR = path.join(process.env.HOME, ".local", "share", "knowledge-catalog");
const DB_FILE = path.join(DB_DIR, "catalog.db");

// Ensure data directory exists on import
mkdirSync(DB_DIR, { recursive: true });

const db = new Database(DB_FILE);

// Enable WAL mode for better concurrency performance
db.pragma("journal_mode = WAL");
db.pragma("foreign_keys = ON");

export const catalogDb = db;

/**
 * Initializes the knowledge schema if tables do not exist.
 */
export function initializeSchema() {
  catalogDb.exec(`
    CREATE TABLE IF NOT EXISTS nodes (
      id TEXT PRIMARY KEY,
      content TEXT NOT NULL,
      type TEXT DEFAULT 'text',
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS edges (
      from_id TEXT NOT NULL REFERENCES nodes(id),
      to_id TEXT NOT NULL REFERENCES nodes(id),
      relationship TEXT CHECK(relationship IN ('references', 'updates', 'contradicts')),
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (from_id, to_id)
    );

    CREATE TABLE IF NOT EXISTS sessions (
      session_id TEXT PRIMARY KEY,
      context_snapshot TEXT NOT NULL,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    -- Performance index for content search
    CREATE INDEX IF NOT EXISTS idx_nodes_content ON nodes (content);
    CREATE INDEX IF NOT EXISTS idx_edges_from ON edges (from_id);
  `);
  
  console.log("[Knowledge Catalogue] Schema checked/initialized successfully.");
}

// Export the database handle for direct use in MCP tool handlers
export default catalogDb;
