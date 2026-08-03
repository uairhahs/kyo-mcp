from contextlib import asynccontextmanager

import uvicorn
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


def main():
    uvicorn.run(
        "start_http:app",
        host="127.0.0.1",
        port=8000,
        loop="uvloop",  # High-performance event loop
        http="httptools",  # Faster HTTP request parsing
    )


if __name__ == "__main__":
    main()
