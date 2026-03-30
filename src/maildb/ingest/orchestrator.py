from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any

import structlog
from psycopg_pool import ConnectionPool

if TYPE_CHECKING:
    from pathlib import Path

from maildb.ingest.embed import embed_worker
from maildb.ingest.index import create_hnsw_index, drop_non_unique_indexes, run_index_phase
from maildb.ingest.parse import process_chunk
from maildb.ingest.split import split_mbox
from maildb.ingest.tasks import complete_task, create_task, get_phase_status, reset_failed_tasks

logger = structlog.get_logger()


def _get_pool(database_url: str) -> ConnectionPool:
    return ConnectionPool(conninfo=database_url, min_size=1, max_size=5, open=True)


def run_pipeline(
    *,
    mbox_path: Path | str,
    database_url: str,
    attachment_dir: Path | str,
    tmp_dir: Path | str,
    chunk_size_bytes: int = 50 * 1024 * 1024,
    parse_workers: int = -1,
    embed_workers: int = 4,
    embed_batch_size: int = 50,
    ollama_url: str = "http://localhost:11434",
    embedding_model: str = "nomic-embed-text",
    embedding_dimensions: int = 768,
    skip_embed: bool = False,
) -> dict[str, Any]:
    """Run the full ingest pipeline. Restartable."""
    if parse_workers == -1:
        parse_workers = max(1, (os.cpu_count() or 2) - 1)

    pool = _get_pool(database_url)

    try:
        # Phase 1: Split
        split_status = get_phase_status(pool, "split")
        if split_status["total"] == 0:
            logger.info("phase_start", phase="split")
            split_task = create_task(pool, phase="split")
            chunks = split_mbox(mbox_path, output_dir=tmp_dir, chunk_size_bytes=chunk_size_bytes)
            for chunk_path in chunks:
                create_task(pool, phase="parse", chunk_path=str(chunk_path))
            complete_task(pool, split_task["id"], messages_total=len(chunks))
            logger.info("phase_complete", phase="split", chunks=len(chunks))
        elif split_status["completed"] == 0:
            logger.info("split_incomplete_restarting")
            with pool.connection() as conn:
                conn.execute("DELETE FROM ingest_tasks WHERE phase IN ('split', 'parse')")
                conn.commit()
            pool.close()
            return run_pipeline(
                mbox_path=mbox_path,
                database_url=database_url,
                attachment_dir=attachment_dir,
                tmp_dir=tmp_dir,
                chunk_size_bytes=chunk_size_bytes,
                parse_workers=parse_workers,
                embed_workers=embed_workers,
                embed_batch_size=embed_batch_size,
                ollama_url=ollama_url,
                embedding_model=embedding_model,
                embedding_dimensions=embedding_dimensions,
                skip_embed=skip_embed,
            )

        # Phase 2: Parse
        reset_failed_tasks(pool, phase="parse")
        parse_status = get_phase_status(pool, "parse")
        if parse_status["pending"] > 0 or parse_status["in_progress"] > 0:
            logger.info("phase_start", phase="parse", pending=parse_status["pending"])
            drop_non_unique_indexes(pool)

            with ProcessPoolExecutor(max_workers=parse_workers) as executor:
                futures = [
                    executor.submit(
                        process_chunk,
                        database_url=database_url,
                        attachment_dir=attachment_dir,
                    )
                    for _ in range(parse_workers)
                ]
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception:
                        logger.exception("parse_worker_crashed")

            logger.info("phase_complete", phase="parse")

        parse_status = get_phase_status(pool, "parse")
        if parse_status["failed"] > 0:
            logger.error("parse_phase_has_permanent_failures", failed=parse_status["failed"])
            msg = (
                f"Parse phase has {parse_status['failed']} permanently failed tasks. "
                "Fix errors and retry."
            )
            raise RuntimeError(msg)

        # Phase 3: Index
        index_status = get_phase_status(pool, "index")
        if index_status["completed"] == 0:
            logger.info("phase_start", phase="index")
            index_task = create_task(pool, phase="index")
            run_index_phase(pool, include_hnsw=False)
            complete_task(pool, index_task["id"])
            logger.info("phase_complete", phase="index")

        # Phase 4: Embed
        if not skip_embed:
            embed_status = get_phase_status(pool, "embed")
            if embed_status["completed"] == 0:
                logger.info("phase_start", phase="embed")
                embed_task = create_task(pool, phase="embed")

                with ProcessPoolExecutor(max_workers=embed_workers) as executor:
                    futures = [
                        executor.submit(
                            embed_worker,
                            database_url=database_url,
                            ollama_url=ollama_url,
                            embedding_model=embedding_model,
                            embedding_dimensions=embedding_dimensions,
                            batch_size=embed_batch_size,
                        )
                        for _ in range(embed_workers)
                    ]
                    total_embedded = sum(f.result() for f in futures)

                complete_task(pool, embed_task["id"], messages_total=total_embedded)
                create_hnsw_index(pool)
                logger.info("phase_complete", phase="embed", total=total_embedded)

    finally:
        pool.close()

    pool = _get_pool(database_url)
    try:
        return get_status(pool)
    finally:
        pool.close()


_PHASE_CASCADE = {
    "parse": ["parse", "index", "embed"],
    "index": ["index", "embed"],
    "embed": ["embed"],
}


def reset_pipeline(pool: ConnectionPool, *, phase: str | None) -> None:
    """Reset pipeline state. If phase is None, full reset."""
    with pool.connection() as conn:
        if phase is None:
            conn.execute("DELETE FROM email_attachments")
            conn.execute("DELETE FROM attachments")
            conn.execute("DELETE FROM emails")
            conn.execute("DELETE FROM ingest_tasks")
        else:
            phases_to_clear = _PHASE_CASCADE.get(phase)
            if phases_to_clear is None:
                msg = f"Unknown phase: {phase}. Must be one of: parse, index, embed"
                raise ValueError(msg)
            conn.execute(
                "DELETE FROM ingest_tasks WHERE phase = ANY(%(phases)s)",
                {"phases": phases_to_clear},
            )
            if "parse" in phases_to_clear:
                conn.execute("DELETE FROM email_attachments")
                conn.execute("DELETE FROM attachments")
                conn.execute("DELETE FROM emails")
            elif "embed" in phases_to_clear:
                conn.execute("UPDATE emails SET embedding = NULL")
        conn.commit()
    logger.info("pipeline_reset", phase=phase or "all")


def get_status(pool: ConnectionPool) -> dict[str, Any]:
    """Get status for all phases."""
    result: dict[str, Any] = {}
    for phase in ("split", "parse", "index", "embed"):
        result[phase] = get_phase_status(pool, phase)

    with pool.connection() as conn:
        cur = conn.execute("SELECT count(*) FROM emails")
        result["total_emails"] = cur.fetchone()[0]  # type: ignore[index]
        cur = conn.execute("SELECT count(*) FROM emails WHERE embedding IS NOT NULL")
        result["total_embedded"] = cur.fetchone()[0]  # type: ignore[index]
        cur = conn.execute("SELECT count(*) FROM attachments")
        result["total_attachments_unique"] = cur.fetchone()[0]  # type: ignore[index]
        cur = conn.execute("SELECT count(*) FROM email_attachments")
        result["total_attachments"] = cur.fetchone()[0]  # type: ignore[index]

    return result
