#!/usr/bin/env node
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";
import catalogDb, { initializeSchema } from "./lib/db.js";

// 1. Initialize the underlying knowledge base on boot
initializeSchema();

// 2. Setup MCP Server instance
const server = new McpServer({
  name: "Knowledge Catalogue",
  version: "0.1.0"
});

// --- Tool Definitions ---

/** store_node: Persist a new research node */
server.registerTool(
  "store_node",
  {
    title: "Store New Node",
    description: "Persist a new knowledge node (finding, note, diagram reference) into the catalogue.",
    inputSchema: {
      id: z.string().describe("Optional unique identifier for the node"),
      content: z.string().describe("The primary text or data content of the finding."),
      type: z.enum(["text", "diagram", "abstract"]).default("text").describe("Type of content being stored."),
    }
  },
  async (params) => {
    const id = params.id || crypto.randomUUID();
    const insertSQL = `INSERT INTO nodes (id, content, type) VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET content = ?, type = ?`;
    const stmt = catalogDb.prepare(insertSQL);
    
    stmt.run(params.id, params.content, params.type, params.content, params.type);
    
    return {
      content: [{ type: "text", text: `Successfully stored node with ID: ${id}` }],
      structuredContent: { id, status: "stored" }
    };
  }
);

/** search: Retrieve relevant nodes using SQLite LIKE queries */
server.registerTool(
  "search",
  {
    title: "Search Knowledge Catalogue",
    description: "Query the knowledge base for nodes matching a keyword.",
    inputSchema: {
      query: z.string().describe("Keyword or partial phrase to search within stored nodes.")
    }
  },
  async (params) => {
    const stmt = catalogDb.prepare(`SELECT id, content, type, created_at FROM nodes WHERE content LIKE ? LIMIT 20`).pluck().all();
    const results = stmt.run(`%${params.query}%`);
    
    return {
      content: [{ 
        type: "text", 
        text: JSON.stringify(results, null, 2) 
      }]
    };
  }
);

/** update_context: Persist the current interaction state or session summary */
server.registerTool(
  "update_context",
  {
    title: "Update Research Context",
    description: "Save a snapshot of the current research session for later retrieval by other agents.",
    inputSchema: {
      session_id: z.string().describe("A unique identifier for this specific research session."),
      context: z.string().describe("The summary or full text of the current context/snapshot.")
    }
  },
  async (params) => {
    const stmt = catalogDb.prepare(`INSERT INTO sessions (session_id, context_snapshot) VALUES (?, ?) ON CONFLICT(session_id) DO UPDATE SET context_snapshot = ?, updated_at = CURRENT_TIMESTAMP`);
    stmt.run(params.session_id, params.context, params.context);
    
    return {
      content: [{ type: "text", text: `Context for session "${params.session_id}" updated successfully.` }]
    };
  }
);

// --- Resource Definitions (URIs) ---

server.server.registerResource("catalog://nodes/root", "Node Root", () => ({
  contents: [{ uri: `catalog://nodes/root`, name: "Root Node", mimeType: "application/json" }],
  icon: "/path/to/icon.svg" // Icon path or base64 if needed by client
}));

/** 
 * Dynamic Resource Handler for catalog://nodes/{id}
 */
server.server.registerResourceTemplate(
  "node_by_id",
  "catalog://nodes/:id",
  async (params) => {
    const node = catalogDb.prepare(`SELECT id, content, type FROM nodes WHERE id = ?`).get(params.id);
    
    if (!node) {
      throw new Error("Node not found");
    }

    return {
      _meta: {},
      contents: [{
        uri: `catalog://nodes/${params.id}`,
        name: `Node ${params.id}`,
        description: `Retrieved from the knowledge catalogue`,
        mimeType: node.type === "diagram" ? "image/svg+xml" : "application/json",
        text: JSON.stringify(node, null, 2)
      }]
    };
  }
);

// --- Start the MCP Server via stdio ---
async function main() {
  const transport = new StdioServerTransport();
  await server.server.connect(transport);
  
  console.error("[Knowledge Catalogue] MCP server listening via stdio. Ready to accept connections.");
}

main().catch(console.error);
