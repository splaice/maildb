"""Entry point for running the MCP server: python -m maildb"""

from __future__ import annotations

import logging
import sys

import structlog

from maildb.server import mcp


def _configure_logging() -> None:
    """Route all logging to stderr so stdout stays clean for MCP stdio transport."""
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )


def main() -> None:
    """Run the MailDB MCP server."""
    _configure_logging()
    mcp.run()


if __name__ == "__main__":
    main()
