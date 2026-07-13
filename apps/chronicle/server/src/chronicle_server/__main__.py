# src/chronicle_server/__main__.py
from __future__ import annotations

import uvicorn

from chronicle_server.app import create_app


def main() -> None:
    uvicorn.run(create_app(), host="127.0.0.1", port=8400)


if __name__ == "__main__":
    main()
