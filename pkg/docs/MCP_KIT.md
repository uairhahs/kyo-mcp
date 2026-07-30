# Kyo MCP Architecture & Runtime Kit

## 1. Core Runtime Fix: The Lifespan Trap
When embedding `FastMCP` inside a larger ASGI application (like FastAPI), the server does not automatically start its internal task group like it would in a standalone script (`if __name__ == "__main__": mcp.run()`).

**The Fix:** You must manually invoke `(server_instance.session_manager.run())` within your application's lifespan context.

```python
from asyncio import AsyncExitStack
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request

@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(mcp_lifespan)
```

---

## 2. Routing Fix: The Redirect Loop (307 -> 404)
By default, Starlette's `mount()` implementation automatically redirects requests from a path without a trailing slash to one with it (`/mcp` -> `/mcp/`). 

**The Problem:** This redirect is performed on the *outer* app. The server drops the request body during the 307 handshake, and the inner MCP app returns a 404 because it's waiting for the "initialize" payload that never arrived.

**The Fix:** Use `@app.api_route` to explicitly handle POST requests and forward the raw ASGI scope to the inner app:
```python
@app.post("/mcp")
async def mcp_endpoint(request: Request):
    # Forward the raw scope (headers + body) without modification
    return await request.app.router.middleware_stack(scope=request.scope, receive=request._receive, send=request._send)
```

---

## 3. StreamableHTTP Negotiation (Client-Side)
The `StreamableHTTP` transport is strict about HTTP headers to negotiate the upgrade from a simple request/response loop into an SSE (Server-Sent Events) stream.

**Required Initialization Payload:**
```json
{
  "jsonrpc": "2.0",
  "method": "initialize",
  "params": {
    "protocolVersion": "2024-11-05",
    "capabilities": {},
    "clientInfo": {"name": "your-client", "version": "1.0"}
  },
  "id": 1
}
```

**Required Headers:**
*   `Accept: application/json, text/event-stream` (Crucial for the 200/101 upgrade)
*   `Connection: upgrade`
*   `Mcp-Session-Id: <token>` (Must be passed in all subsequent JSON-RPC calls to maintain state)

---

## 4. Sandbox & Dependency Context
*   **Runner:** Always use `uv run python main.py`. Direct `python` commands will fail because the project relies on isolated venv dependencies not present in the host system's global Python.
*   **Network Visibility:** In environments like Nix-sandboxes, port 8000 is often mapped but inaccessible from within the container via `localhost`. Always validate routing logic by inspecting source code rather than relying on `curl` if network is blocked.
