import os

import uvicorn


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
