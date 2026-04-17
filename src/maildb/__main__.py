"""Entry point for `python -m maildb`. Defaults to the MCP server (`serve`)."""

from __future__ import annotations

import sys

from maildb.cli import app


def main() -> None:
    # If invoked with no subcommand, default to `serve` to preserve
    # the historical `python -m maildb` behavior.
    if len(sys.argv) == 1:
        sys.argv.append("serve")
    app()


if __name__ == "__main__":
    main()
