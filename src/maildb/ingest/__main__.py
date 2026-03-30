from __future__ import annotations

import sys
from pathlib import Path

from maildb.config import Settings
from maildb.db import create_pool, init_db
from maildb.ingest.orchestrator import get_status, run_pipeline


def main() -> None:
    settings = Settings()
    args = sys.argv[1:]

    if not args:
        sys.stdout.write("Usage: python -m maildb.ingest <mbox_path> | status\n")
        sys.exit(1)

    command = args[0]

    if command == "status":
        pool = create_pool(settings)
        init_db(pool)
        status = get_status(pool)
        _print_status(status)
        pool.close()
        return

    # Default: treat as mbox path
    mbox_path = Path(command)
    if not mbox_path.exists():
        sys.stderr.write(f"Error: {mbox_path} not found\n")
        sys.exit(1)

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
    )
    _print_status(result)


def _print_status(status: dict) -> None:  # type: ignore[type-arg]
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
    sys.stdout.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
