"""Entry point for running the MCP server: python -m maildb"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import structlog

from maildb.config import Settings
from maildb.pii import scrub_pii
from maildb.server import mcp


def _configure_logging(settings: Settings | None = None) -> None:
    """Set up dual-sink logging: stderr (INFO+) and debug log file (DEBUG+).

    PII scrubbing is applied before events reach either sink.
    """
    settings = settings or Settings()
    log_path = Path(settings.debug_log)

    # Ensure parent directory exists
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Truncate if oversized
    if log_path.exists() and log_path.stat().st_size > settings.debug_log_max_bytes:
        log_path.write_text("")

    # --- stdlib logging setup (sinks) ---
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(logging.DEBUG)

    # Shared formatter using structlog's ConsoleRenderer
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(),
        ],
    )

    # Sink 1: stderr at INFO+
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.INFO)
    stderr_handler.setFormatter(formatter)
    root_logger.addHandler(stderr_handler)

    # Sink 2: debug log file at configurable level
    file_level = getattr(logging, settings.debug_log_level.upper(), logging.DEBUG)
    file_handler = logging.FileHandler(str(log_path))
    file_handler.setLevel(file_level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # --- structlog setup (shared processors) ---
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            scrub_pii,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )


def main() -> None:
    """Run the MailDB MCP server."""
    _configure_logging()
    mcp.run()


if __name__ == "__main__":
    main()
