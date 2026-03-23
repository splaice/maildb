"""Entry point for running the MCP server: python -m maildb.server"""

from __future__ import annotations

from maildb.server import mcp


def main() -> None:
    """Run the MailDB MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
