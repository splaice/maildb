"""Unified maildb CLI — `serve`, `ingest run/status/reset/migrate`."""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
import typer

from maildb.config import Settings
from maildb.db import create_pool, init_db
from maildb.ingest.orchestrator import (
    backfill_source_account,
    get_status,
    reset_pipeline,
    run_pipeline,
)
from maildb.ingest.process_attachments import run as pa_run
from maildb.pii import scrub_pii
from maildb.server import mcp

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

app = typer.Typer(
    name="maildb",
    help="Personal email database with semantic search.",
    no_args_is_help=True,
)

ingest_app = typer.Typer(name="ingest", help="Ingest pipeline commands.", no_args_is_help=True)
app.add_typer(ingest_app, name="ingest")


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _validate_account(account: str) -> str:
    if not _EMAIL_RE.match(account):
        raise typer.BadParameter(f"--account {account!r} is not a valid email address")
    return account


def _configure_logging(settings: Settings | None = None) -> None:
    """Set up dual-sink logging: stderr (INFO+) and debug log file (DEBUG+).

    PII scrubbing is applied before events reach either sink.
    """
    settings = settings or Settings()
    log_path = Path(settings.debug_log)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists() and log_path.stat().st_size > settings.debug_log_max_bytes:
        log_path.write_text("")

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(logging.DEBUG)

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.dev.ConsoleRenderer(),
        ],
    )

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.INFO)
    stderr_handler.setFormatter(formatter)
    root_logger.addHandler(stderr_handler)

    file_level = getattr(logging, settings.debug_log_level.upper(), logging.DEBUG)
    file_handler = logging.FileHandler(str(log_path))
    file_handler.setLevel(file_level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

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


@app.command()
def serve() -> None:
    """Run the MailDB MCP server (stdio transport)."""
    _configure_logging()
    mcp.run()


@ingest_app.command("run")
def ingest_run(
    mbox_path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),  # noqa: B008
    account: str = typer.Option(..., "--account", help="Email address of the source account."),
    skip_embed: bool = typer.Option(False, "--skip-embed", help="Skip the embedding phase."),
    force_new_import: bool = typer.Option(
        False,
        "--force-new-import",
        help=(
            "Always allocate a new imports row. By default, an in-progress "
            "import for the same account+file is resumed."
        ),
    ),
) -> None:
    """Run the full ingest pipeline for an mbox file."""
    _validate_account(account)
    settings = Settings()

    pool = create_pool(settings)
    init_db(pool)
    pool.close()

    result = run_pipeline(
        mbox_path=mbox_path,
        database_url=settings.database_url,
        attachment_dir=settings.attachment_dir,
        tmp_dir=settings.ingest_tmp_dir,
        chunk_size_bytes=settings.ingest_chunk_size_mb * 1024 * 1024,
        parse_workers=settings.ingest_workers,
        embed_workers=settings.embed_workers,
        embed_batch_size=settings.embed_batch_size,
        ollama_url=settings.ollama_url,
        embedding_model=settings.embedding_model,
        embedding_dimensions=settings.embedding_dimensions,
        skip_embed=skip_embed,
        source_account=account,
        force_new_import=force_new_import,
    )
    _print_status_dict(result)


def _print_status_dict(status: dict) -> None:  # type: ignore[type-arg]
    """Format and print pipeline status summary to stdout."""
    lines = [
        f"{'Phase':<10} {'Total':>6} {'Done':>6} {'Failed':>7} {'In Progress':>12}",
    ]
    for phase in ("split", "parse", "index", "embed"):
        s = status.get(phase, {})
        lines.append(
            f"{phase:<10} {s.get('total', 0):>6} {s.get('completed', 0):>6} "
            f"{s.get('failed', 0):>7} {s.get('in_progress', 0):>12}"
        )
    lines.append("")
    lines.append(f"Messages: {status.get('total_emails', 0):,}")
    real = status.get("total_embedded_real", status.get("total_embedded", 0))
    skipped = status.get("total_embedded_skipped", 0)
    total = status.get("total_emails", 0)
    if skipped > 0:
        lines.append(f"Embeddings: {real:,} real + {skipped:,} skipped / {total:,}")
    else:
        lines.append(f"Embeddings: {real:,} / {total:,}")
    lines.append(
        f"Attachments: {status.get('total_attachments', 0):,} "
        f"({status.get('total_attachments_unique', 0):,} unique)"
    )
    typer.echo("\n".join(lines))


@ingest_app.command("status")
def ingest_status(
    account: str | None = typer.Option(
        None,
        "--account",
        help="Filter to one source account.",
    ),
) -> None:
    """Print pipeline phase counts and per-import breakdown."""
    settings = Settings()
    pool = create_pool(settings)
    init_db(pool)
    try:
        status = get_status(pool)
        _print_status_dict(status)
        _print_imports_summary(pool, account)
    finally:
        pool.close()


def _print_imports_summary(pool, account: str | None) -> None:  # type: ignore[no-untyped-def]
    """Print a per-import breakdown to stdout."""
    sql = (
        "SELECT started_at, source_account, status, messages_inserted, messages_skipped "
        "FROM imports "
    )
    params: dict = {}
    if account is not None:
        sql += "WHERE source_account = %(account)s "
        params["account"] = account
    sql += "ORDER BY started_at DESC LIMIT 20"
    with pool.connection() as conn:
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
    if not rows:
        return
    typer.echo("\nImports")
    for started, acct, status, inserted, skipped in rows:
        ts = started.strftime("%Y-%m-%d %H:%M") if started else "?"
        typer.echo(
            f"  {ts}  {acct:<24} {status:<10} "
            f"{inserted or 0:>10,} inserted   {skipped or 0:>4} skipped"
        )


@ingest_app.command("reset")
def ingest_reset(
    phase: str | None = typer.Option(
        None,
        "--phase",
        help="Reset only one phase: parse, index, or embed.",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
) -> None:
    """Delete pipeline state. Without --phase, performs a full reset."""
    settings = Settings()
    target = phase or "all phases"
    if not yes and not typer.confirm(f"This will reset {target}. Continue?", default=False):
        typer.echo("Aborted.")
        raise typer.Exit(code=1)

    pool = create_pool(settings)
    init_db(pool)
    try:
        reset_pipeline(pool, phase=phase)
    finally:
        pool.close()
    typer.echo(f"Reset complete ({phase or 'full'}).")


@ingest_app.command("migrate")
def ingest_migrate(
    account: str = typer.Option(
        ...,
        "--account",
        help="Email address to tag legacy rows with.",
    ),
) -> None:
    """Backfill source_account/import_id on rows that lack them."""
    _validate_account(account)
    settings = Settings()
    pool = create_pool(settings)
    init_db(pool)
    try:
        result = backfill_source_account(pool, account=account)
    finally:
        pool.close()
    typer.echo(f"Backfilled {result['rows_updated']} rows with source_account={account}")


process_app = typer.Typer(
    name="process_attachments",
    help="Extract + embed attachment contents for semantic search.",
    no_args_is_help=True,
)
app.add_typer(process_app, name="process_attachments")


def _build_process_pool() -> "ConnectionPool":  # noqa: UP037
    settings = Settings()
    pool = create_pool(settings)
    init_db(pool)
    return pool


def _close_pool(pool: object) -> None:
    close = getattr(pool, "close", None)
    if close is not None:
        close()


def _count_selected(
    pool: "ConnectionPool",  # noqa: UP037
    *,
    retry_failed: bool,
    selector_sql: str,
    selector_params: dict,  # type: ignore[type-arg]
) -> int:
    states = "('pending','failed')" if retry_failed else "('pending')"
    sql = f"""
        SELECT count(*) FROM attachment_contents
         WHERE status IN {states} {selector_sql}
    """  # noqa: S608
    with pool.connection() as conn:
        cur = conn.execute(sql, selector_params)
        row = cur.fetchone()
        return int(row[0]) if row else 0


_BUCKET_TO_CONTENT_TYPES: dict[str, list[str]] = {
    "pdf": ["application/pdf"],
    "docx": [
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/msword",
    ],
    "xlsx": [
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
    ],
    "pptx": [
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ],
    "image": [
        "image/png",
        "image/jpeg",
        "image/jpg",
        "image/gif",
        "image/tiff",
        "image/webp",
    ],
    "text": ["text/plain"],
    "html": ["text/html"],
}


@process_app.command("run")
def process_run(
    workers: int = typer.Option(1, "--workers", help="Parallel workers."),
    retry_failed: bool = typer.Option(
        True, "--retry-failed/--no-retry-failed", help="Re-process rows with status='failed'."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report selection count without processing."
    ),
    limit: int | None = typer.Option(None, "--limit", help="Process at most N rows."),
    sample: int | None = typer.Option(
        None, "--sample", help="Random sample of N rows (wins over --limit)."
    ),
    only: str | None = typer.Option(
        None, "--only", help=f"Filter bucket: {', '.join(_BUCKET_TO_CONTENT_TYPES)}."
    ),
    ids: str | None = typer.Option(None, "--ids", help="Comma-separated attachment_ids."),
    min_size: int | None = typer.Option(None, "--min-size"),
    max_size: int | None = typer.Option(None, "--max-size"),
) -> None:
    """Process pending (and optionally failed) attachments."""
    selector_sql_parts: list[str] = []
    selector_params: dict[str, object] = {}
    if only is not None:
        if only not in _BUCKET_TO_CONTENT_TYPES:
            raise typer.BadParameter(f"--only must be one of {list(_BUCKET_TO_CONTENT_TYPES)}")
        selector_sql_parts.append(
            "AND attachment_id IN (SELECT id FROM attachments "
            "WHERE content_type = ANY(%(content_types)s))"
        )
        selector_params["content_types"] = _BUCKET_TO_CONTENT_TYPES[only]
    if ids is not None:
        id_list = [int(x) for x in ids.split(",") if x.strip()]
        selector_sql_parts.append("AND attachment_id = ANY(%(ids)s)")
        selector_params["ids"] = id_list
    if min_size is not None:
        selector_sql_parts.append(
            "AND attachment_id IN (SELECT id FROM attachments WHERE size >= %(min_size)s)"
        )
        selector_params["min_size"] = min_size
    if max_size is not None:
        selector_sql_parts.append(
            "AND attachment_id IN (SELECT id FROM attachments WHERE size <= %(max_size)s)"
        )
        selector_params["max_size"] = max_size
    if sample is not None:
        selector_sql_parts.append(
            "AND attachment_id IN ("
            "SELECT attachment_id FROM attachment_contents "
            "ORDER BY random() LIMIT %(sample)s)"
        )
        selector_params["sample"] = sample
    elif limit is not None:
        selector_sql_parts.append(
            "AND attachment_id IN ("
            "SELECT attachment_id FROM attachment_contents "
            "ORDER BY attachment_id LIMIT %(limit)s)"
        )
        selector_params["limit"] = limit

    selector_sql = " ".join(selector_sql_parts)

    pool = _build_process_pool()
    try:
        if dry_run:
            n = _count_selected(
                pool,
                retry_failed=retry_failed,
                selector_sql=selector_sql,
                selector_params=selector_params,
            )
            typer.echo(f"Would process {n} attachment(s). (--dry-run)")
            return
        settings = Settings()
        counts = pa_run(
            pool,
            attachment_dir=Path(settings.attachment_dir),
            workers=workers,
            retry_failed=retry_failed,
            selector_sql=selector_sql,
            selector_params=selector_params,
        )
        typer.echo(
            "Done. extracted={extracted} failed={failed} skipped={skipped}".format(**counts)
        )
    finally:
        _close_pool(pool)


@process_app.command("status")
def process_status() -> None:
    """Summary of extraction state: pending / extracted / failed / skipped."""
    pool = _build_process_pool()
    try:
        with pool.connection() as conn:
            cur = conn.execute(
                "SELECT status, count(*) FROM attachment_contents GROUP BY status ORDER BY status"
            )
            rows = cur.fetchall()
        typer.echo("Attachment extraction status")
        for status, n in rows:
            typer.echo(f"  {status:<10} {n:>7,}")

        with pool.connection() as conn:
            cur = conn.execute(
                "SELECT reason, count(*) FROM attachment_contents "
                "WHERE status = 'failed' GROUP BY reason ORDER BY count(*) DESC LIMIT 10"
            )
            rows = cur.fetchall()
        if rows:
            typer.echo("\nTop failure reasons")
            for reason, n in rows:
                typer.echo(f"  {n:>4,}  {reason[:120] if reason else ''}")
    finally:
        _close_pool(pool)


@process_app.command("retry")
def process_retry(
    workers: int = typer.Option(1, "--workers"),
) -> None:
    """Re-process only rows with status='failed'."""
    pool = _build_process_pool()
    try:
        settings = Settings()
        counts = pa_run(
            pool,
            attachment_dir=Path(settings.attachment_dir),
            workers=workers,
            retry_failed=True,
            selector_sql="AND status = 'failed'",
            selector_params={},
        )
        typer.echo(
            "Retry done. extracted={extracted} failed={failed} skipped={skipped}".format(**counts)
        )
    finally:
        _close_pool(pool)


if __name__ == "__main__":
    app()
