import sys
from contextlib import asynccontextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from fastapi import FastAPI
from kyo_mcp.mcp_server import mcp as server_instance


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with server_instance.session_manager.run():
        yield


app = FastAPI(docs_url="/docs", lifespan=lifespan)


@app.get("/")
def index():
    return {"message": "Kyo Knowledge Catalogue MCP (SQLite) online."}


app.mount("/", server_instance.streamable_http_app())
