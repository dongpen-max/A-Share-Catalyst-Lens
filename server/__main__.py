from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "server.app:app",
        host=os.getenv("CATALYST_HOST", "127.0.0.1"),
        port=int(os.getenv("CATALYST_PORT", "8000")),
        reload=False,
    )


if __name__ == "__main__":
    main()
