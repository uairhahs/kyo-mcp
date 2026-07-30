import sys
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request

sys.path.insert(0, str(Path(__file__).parent / "src"))

# Initialize the MCP instance to bootstrap its state manager
from kyo_mcp.mcp_server import mcp as server_instance

@asynccontextmanager
async def lifespan(app: FastAPI):
    # The MCP SDK's 'session_manager.run()' acts as an async context manager that 
    # initializes its internal task group. Without this, requests will arrive but 
    # get a RuntimeError because there's no task group to handle them.
    async with server_instance.session_manager.run():
        yield  # Traffic is served here

# Create the FastAPI app using our lifespan context
app = FastAPI(docs_url="/docs", lifespan=lifespan)

@app.get("/")
def index():
    return {"message": "Kyo Knowledge Catalogue MCP (SQLite) online."}

# Bypassing the 'Slash Redirect' bug by manually routing POST to the ASGI app
mcp_app = server_instance.streamable_http_app() 

@app.api_route("/mcp", methods=["POST"]) 
async def mcp_endpoint(request: Request):
    """
    Directly forwards the POST request to the MCP SDK's ASGI handler.
    This completely bypasses Starlette's automatic slash-redirect middleware that was stripping bodies.
    """
    return await mcp_app(scope=request.scope, receive=request._receive, send=request._send)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("start_http:app", host="127.0.0.1", port=8000, log_level="info")
