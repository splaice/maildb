"""Attachment extraction worker.

Claims pending rows from attachment_contents via SKIP LOCKED, runs
extract_markdown, chunks, embeds each chunk, writes markdown to disk,
and transitions status. Idempotent per-attachment; safe to crash
mid-run (watchdog reclaims 'extracting' rows older than the threshold).
"""

from __future__ import annotations

import signal
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from psycopg_pool import ConnectionPool

from maildb.config import Settings
from maildb.embeddings import EmbeddingClient
from maildb.ingest.chunking import chunk_markdown
from maildb.ingest.extraction import ExtractionFailedError, extract_markdown

if TYPE_CHECKING:
    from collections.abc import Callable


class ExtractionTimeoutError(Exception):
    """Raised when extract_markdown exceeds its wall-clock budget."""


def _run_with_timeout[R](seconds: int, fn: Callable[[], R]) -> R:
    """Run ``fn`` and raise ExtractionTimeoutError if it doesn't return within ``seconds``.

    Uses SIGALRM — must be called from the main thread of the calling process.
    Because each subprocess worker is single-threaded here, that's fine. Passing
    ``seconds <= 0`` disables the timeout.
    """
    if seconds <= 0:
        return fn()

    def _handler(_signum: int, _frame: object) -> None:
        msg = f"timed out after {seconds}s"
        raise ExtractionTimeoutError(msg)

    old_handler = signal.signal(signal.SIGALRM, _handler)
    signal.alarm(seconds)
    try:
        return fn()
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


logger = structlog.get_logger()

WATCHDOG_STALE_SECONDS = 3600  # 1 hour


def ensure_pending_rows(pool: ConnectionPool) -> int:
    """Insert a 'pending' row into attachment_contents for every attachment that
    doesn't already have one. Returns count of newly inserted rows.
    """
    with pool.connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO attachment_contents (attachment_id, status)
            SELECT a.id, 'pending'
              FROM attachments a
              LEFT JOIN attachment_contents c ON c.attachment_id = a.id
             WHERE c.attachment_id IS NULL
            """
        )
        conn.commit()
        return cur.rowcount


def _reclaim_stale(pool: ConnectionPool) -> int:
    """Reset 'extracting' rows that haven't been updated in a while to 'pending'."""
    with pool.connection() as conn:
        cur = conn.execute(
            """
            UPDATE attachment_contents
               SET status = 'pending', extracted_at = NULL
             WHERE status = 'extracting'
               AND (extracted_at IS NULL OR extracted_at < now() - (%s || ' seconds')::interval)
            """,
            (WATCHDOG_STALE_SECONDS,),
        )
        conn.commit()
        return cur.rowcount


def _load_attachment(pool: ConnectionPool, attachment_id: int) -> dict[str, Any]:
    with pool.connection() as conn:
        cur = conn.execute(
            "SELECT id, sha256, filename, content_type, storage_path "
            "FROM attachments WHERE id = %s",
            (attachment_id,),
        )
        row = cur.fetchone()
    if row is None:
        msg = f"attachment {attachment_id} not found"
        raise LookupError(msg)
    return {
        "id": row[0],
        "sha256": row[1],
        "filename": row[2],
        "content_type": row[3],
        "storage_path": row[4],
    }


def _write_markdown_mirror(attachment_dir: Path, storage_path: str, markdown: str) -> None:
    mirror = attachment_dir / f"{storage_path}.md"
    mirror.parent.mkdir(parents=True, exist_ok=True)
    mirror.write_text(markdown, encoding="utf-8")


_EMBED_BATCH_SIZE = 50


def _build_embedding_client() -> EmbeddingClient:
    settings = Settings()
    return EmbeddingClient(
        ollama_url=settings.ollama_url,
        model_name=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
    )


def _embed_chunks(pool: ConnectionPool, chunks: list[dict[str, Any]]) -> None:
    """Embed chunks in batches and write vectors back to the DB.

    On per-batch error, falls back to single-row embedding. Rows that
    still fail get a zero-vector sentinel (same pattern the email embed
    worker uses).
    """
    if not chunks:
        return

    client = _build_embedding_client()

    # Resolve chunk row IDs from DB — tests pass dicts built from Chunk objects
    # that don't yet carry the bigserial id.
    attachment_id = chunks[0]["attachment_id"]
    with pool.connection() as conn:
        cur = conn.execute(
            "SELECT id, chunk_index, text FROM attachment_chunks "
            "WHERE attachment_id = %s ORDER BY chunk_index",
            (attachment_id,),
        )
        rows = cur.fetchall()

    for start in range(0, len(rows), _EMBED_BATCH_SIZE):
        batch = rows[start : start + _EMBED_BATCH_SIZE]
        ids = [r[0] for r in batch]
        texts = [r[2] for r in batch]
        try:
            vectors = client.embed_batch(texts)
        except Exception:
            vectors = []
            for t in texts:
                try:
                    vectors.append(client.embed(t))
                except Exception:
                    vectors.append([0.0] * client._dimensions)

        with pool.connection() as conn:
            for cid, vec in zip(ids, vectors, strict=True):
                conn.execute(
                    "UPDATE attachment_chunks SET embedding = %s WHERE id = %s",
                    (str(vec), cid),
                )
            conn.commit()


def _set_status(
    pool: ConnectionPool,
    attachment_id: int,
    *,
    status: str,
    reason: str | None = None,
    markdown: str | None = None,
    markdown_bytes: int | None = None,
    extraction_ms: int | None = None,
    extractor_version: str | None = None,
) -> None:
    with pool.connection() as conn:
        conn.execute(
            """
            UPDATE attachment_contents
               SET status = %(status)s,
                   reason = %(reason)s,
                   markdown = %(markdown)s,
                   markdown_bytes = %(markdown_bytes)s,
                   extracted_at = CASE
                        WHEN %(status)s IN ('extracted','failed','skipped') THEN now()
                        ELSE extracted_at
                   END,
                   extraction_ms = COALESCE(%(extraction_ms)s, extraction_ms),
                   extractor_version = COALESCE(%(extractor_version)s, extractor_version)
             WHERE attachment_id = %(attachment_id)s
            """,
            {
                "attachment_id": attachment_id,
                "status": status,
                "reason": reason,
                "markdown": markdown,
                "markdown_bytes": markdown_bytes,
                "extraction_ms": extraction_ms,
                "extractor_version": extractor_version,
            },
        )
        conn.commit()


def _claim_row(
    pool: ConnectionPool,
    *,
    retry_failed: bool,
    selector_sql: str = "",
    selector_params: dict[str, Any] | None = None,
) -> int | None:
    """Atomically move one row to 'extracting' and return its attachment_id."""
    states = "('pending','failed')" if retry_failed else "('pending')"
    sql = f"""
        WITH claimed AS (
            SELECT attachment_id FROM attachment_contents
             WHERE status IN {states}
               {selector_sql}
             ORDER BY attachment_id
             LIMIT 1
             FOR UPDATE SKIP LOCKED
        )
        UPDATE attachment_contents
           SET status = 'extracting', extracted_at = now(), reason = NULL
         WHERE attachment_id IN (SELECT attachment_id FROM claimed)
        RETURNING attachment_id
    """
    with pool.connection() as conn:
        cur = conn.execute(sql, selector_params or {})
        row = cur.fetchone()
        conn.commit()
        return row[0] if row else None


def process_one(
    pool: ConnectionPool,
    attachment_id: int,
    *,
    attachment_dir: Path,
    extract_timeout_s: int = 0,
) -> None:
    """Extract → chunk → embed → status for a single attachment row.

    ``extract_timeout_s`` caps the wall-clock time spent inside extract_markdown
    per attachment; when exceeded the row is marked failed with a reason that
    starts with ``"timed out after"`` so timeouts can be queried and retried
    independently of other failures.
    """
    att = _load_attachment(pool, attachment_id)
    file_path = attachment_dir / Path(att["storage_path"])
    t0 = time.monotonic()
    try:
        result = _run_with_timeout(
            extract_timeout_s,
            lambda: extract_markdown(file_path, content_type=att["content_type"]),
        )
    except ExtractionTimeoutError as exc:
        _set_status(pool, attachment_id, status="failed", reason=str(exc))
        return
    except ExtractionFailedError as exc:
        # Unsupported types are skipped; Marker errors are failures.
        if "not supported" in str(exc).lower() or "requires LibreOffice" in str(exc):
            _set_status(pool, attachment_id, status="skipped", reason=str(exc))
        else:
            _set_status(pool, attachment_id, status="failed", reason=str(exc))
        return
    except Exception as exc:
        _set_status(pool, attachment_id, status="failed", reason=f"{type(exc).__name__}: {exc}")
        return

    elapsed_ms = int((time.monotonic() - t0) * 1000)

    # Drop any prior chunks (re-run safety)
    with pool.connection() as conn:
        conn.execute("DELETE FROM attachment_chunks WHERE attachment_id = %s", (attachment_id,))
        conn.commit()

    chunks = chunk_markdown(result.markdown)
    chunk_rows: list[dict[str, Any]] = []
    with pool.connection() as conn:
        for c in chunks:
            conn.execute(
                """INSERT INTO attachment_chunks
                       (attachment_id, chunk_index, heading_path, page_number, token_count, text)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (
                    attachment_id,
                    c.chunk_index,
                    c.heading_path,
                    c.page_number,
                    c.token_count,
                    c.text,
                ),
            )
            chunk_rows.append({"attachment_id": attachment_id, **c.__dict__})
        conn.commit()

    # Embed chunks via Ollama; tests monkeypatch _embed_chunks or _build_embedding_client.
    _embed_chunks(pool, chunk_rows)

    # Write the on-disk markdown mirror.
    _write_markdown_mirror(attachment_dir, att["storage_path"], result.markdown)

    _set_status(
        pool,
        attachment_id,
        status="extracted",
        markdown=result.markdown,
        markdown_bytes=len(result.markdown.encode("utf-8")),
        extraction_ms=elapsed_ms,
        extractor_version=result.extractor_version,
    )


def _claim_and_process_loop(
    pool: ConnectionPool,
    *,
    attachment_dir: Path,
    retry_failed: bool,
    selector_sql: str,
    selector_params: dict[str, Any] | None,
    extract_timeout_s: int = 0,
) -> None:
    """Claim rows one at a time via SKIP LOCKED and process until none remain."""
    while True:
        attachment_id = _claim_row(
            pool,
            retry_failed=retry_failed,
            selector_sql=selector_sql,
            selector_params=selector_params,
        )
        if attachment_id is None:
            return
        try:
            process_one(
                pool,
                attachment_id,
                attachment_dir=attachment_dir,
                extract_timeout_s=extract_timeout_s,
            )
        except Exception as exc:
            _set_status(pool, attachment_id, status="failed", reason=str(exc))


def _subprocess_worker(
    *,
    database_url: str,
    attachment_dir: Path,
    retry_failed: bool,
    selector_sql: str,
    selector_params: dict[str, Any] | None,
    extract_timeout_s: int = 0,
) -> None:
    """Subprocess entrypoint: build a fresh pool and run the claim loop.

    Each subprocess owns its own ConnectionPool and PyTorch runtime. The isolation
    is required because Marker's model init is not thread-safe — multiple threads
    hitting `create_model_dict()` concurrently produce meta-tensor errors. Process-
    level parallelism avoids that entirely at the cost of per-process startup time.
    """
    pool = ConnectionPool(conninfo=database_url, min_size=1, max_size=2, open=True)
    try:
        _claim_and_process_loop(
            pool,
            attachment_dir=attachment_dir,
            retry_failed=retry_failed,
            selector_sql=selector_sql,
            selector_params=selector_params,
            extract_timeout_s=extract_timeout_s,
        )
    finally:
        pool.close()


def run(
    pool: ConnectionPool,
    *,
    attachment_dir: Path,
    workers: int = 1,
    retry_failed: bool = True,
    selector_sql: str = "",
    selector_params: dict[str, Any] | None = None,
    database_url: str | None = None,
    extract_timeout_s: int = 0,
) -> dict[str, int]:
    """Process all matching pending/failed rows.

    - ``workers == 1`` runs in-process on the provided pool.
    - ``workers > 1`` spawns subprocesses via ``ProcessPoolExecutor``; each loads
      Marker and builds its own pool. Pass ``database_url`` so the children can
      reconnect; it's required when workers > 1.

    ``extract_timeout_s`` caps wall-clock time per attachment inside
    extract_markdown; 0 disables the cap. Timed-out rows are marked ``failed``
    with a reason prefixed ``"timed out after "`` so they can be queried and
    retried as a distinct group.
    """
    ensure_pending_rows(pool)
    _reclaim_stale(pool)

    counts = {"extracted": 0, "failed": 0, "skipped": 0}

    if workers <= 1:
        _claim_and_process_loop(
            pool,
            attachment_dir=attachment_dir,
            retry_failed=retry_failed,
            selector_sql=selector_sql,
            selector_params=selector_params,
            extract_timeout_s=extract_timeout_s,
        )
    else:
        if database_url is None:
            msg = "database_url is required when workers > 1 (subprocesses need it to reconnect)"
            raise ValueError(msg)
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(
                    _subprocess_worker,
                    database_url=database_url,
                    attachment_dir=attachment_dir,
                    retry_failed=retry_failed,
                    selector_sql=selector_sql,
                    selector_params=selector_params,
                    extract_timeout_s=extract_timeout_s,
                )
                for _ in range(workers)
            ]
            for f in futures:
                f.result()

    with pool.connection() as conn:
        cur = conn.execute("SELECT status, count(*) FROM attachment_contents GROUP BY status")
        for status, n in cur.fetchall():
            if status in counts:
                counts[status] = n
    return counts
