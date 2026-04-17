from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog
from psycopg_pool import ConnectionPool

from maildb.ingest.attachments import hash_attachment, store_attachment
from maildb.ingest.tasks import claim_task, complete_task, fail_task
from maildb.parsing import parse_mbox

logger = structlog.get_logger()

INSERT_EMAIL_SQL = """
INSERT INTO emails (
    id, message_id, thread_id, subject, sender_name, sender_address, sender_domain,
    recipients, date, body_text, body_html, has_attachment, attachments,
    labels, in_reply_to, "references", source_account, import_id
) VALUES (
    %(id)s, %(message_id)s, %(thread_id)s, %(subject)s, %(sender_name)s, %(sender_address)s,
    %(sender_domain)s, %(recipients)s, %(date)s, %(body_text)s, %(body_html)s,
    %(has_attachment)s, %(attachments)s, %(labels)s, %(in_reply_to)s, %(references)s,
    %(source_account)s, %(import_id)s
) ON CONFLICT (message_id) DO UPDATE SET thread_id = emails.thread_id
RETURNING id
"""

INSERT_EMAIL_ACCOUNT_SQL = """
INSERT INTO email_accounts (email_id, source_account, import_id)
VALUES (%(email_id)s, %(source_account)s, %(import_id)s)
ON CONFLICT (email_id, source_account) DO NOTHING
"""

INSERT_ATTACHMENT_SQL = """
INSERT INTO attachments (sha256, filename, content_type, size, storage_path)
VALUES (%(sha256)s, %(filename)s, %(content_type)s, %(size)s, %(storage_path)s)
ON CONFLICT (sha256) DO NOTHING
"""

INSERT_EMAIL_ATTACHMENT_SQL = """
INSERT INTO email_attachments (email_id, attachment_id, filename)
VALUES (%(email_id)s, %(attachment_id)s, %(filename)s)
ON CONFLICT DO NOTHING
"""


def process_chunk(
    *,
    database_url: str,
    attachment_dir: Path | str,
) -> int:
    """Claim and process chunks in a loop until no work remains. Returns chunks processed."""
    attachment_dir = Path(attachment_dir)
    pool = ConnectionPool(conninfo=database_url, min_size=1, max_size=1, open=True)
    worker_id = str(os.getpid())
    chunks_processed = 0
    # Cache source_account lookups per import_id to avoid redundant SELECTs
    # on the imports table: every chunk from one import shares the same account.
    account_cache: dict[Any, str] = {}

    try:
        while True:
            claimed = claim_task(pool, phase="parse", worker_id=worker_id)
            if claimed is None:
                break
            task_id = claimed["id"]
            chunk_path = claimed["chunk_path"]
            import_id = claimed["import_id"]
            if import_id not in account_cache:
                account_cache[import_id] = _lookup_source_account(pool, import_id)
            source_account = account_cache[import_id]
            try:
                _process_single_chunk(
                    pool,
                    task_id,
                    chunk_path,
                    attachment_dir,
                    import_id=import_id,
                    source_account=source_account,
                )
                chunks_processed += 1
            except Exception as exc:
                logger.exception("chunk_failed", task_id=task_id)
                try:
                    fail_task(pool, task_id, error=str(exc))
                except Exception:
                    logger.exception("failed_to_update_task", task_id=task_id)
    finally:
        pool.close()

    return chunks_processed


def _lookup_source_account(pool: ConnectionPool, import_id: Any) -> str:
    with pool.connection() as conn:
        cur = conn.execute(
            "SELECT source_account FROM imports WHERE id = %(id)s",
            {"id": import_id},
        )
        row = cur.fetchone()
        if row is None:
            msg = f"No imports row for id {import_id}"
            raise RuntimeError(msg)
        return row[0]  # type: ignore[no-any-return]


def _process_single_chunk(
    pool: ConnectionPool,
    task_id: int,
    chunk_path: str,
    attachment_dir: Path,
    *,
    import_id: Any,
    source_account: str,
) -> None:
    """Process a single chunk file: parse, extract attachments, insert into DB."""
    messages = list(parse_mbox(chunk_path))
    email_rows: list[dict] = []
    attachment_meta: list[dict] = []  # {email_id, sha256, filename}
    unique_hashes: dict[str, dict] = {}  # sha256 -> attachment row data

    for msg in messages:
        email_id = uuid4()

        email_rows.append(
            {
                "id": email_id,
                "message_id": msg["message_id"],
                "thread_id": msg["thread_id"],
                "subject": msg["subject"],
                "sender_name": msg["sender_name"],
                "sender_address": msg["sender_address"],
                "sender_domain": msg["sender_domain"],
                "recipients": json.dumps(msg["recipients"]) if msg["recipients"] else None,
                "date": msg["date"],
                "body_text": msg["body_text"],
                "body_html": msg["body_html"],
                "has_attachment": msg["has_attachment"],
                "attachments": json.dumps(msg["attachments"]) if msg["attachments"] else None,
                "labels": msg["labels"] or None,
                "in_reply_to": msg["in_reply_to"],
                "references": msg["references"] or None,
                "source_account": source_account,
                "import_id": import_id,
            }
        )

        # Extract and store attachments to disk
        for att in msg.get("_attachments_with_data", []):
            data = att["data"]
            sha = hash_attachment(data)
            rel_path = store_attachment(data, sha, att["filename"], base_dir=attachment_dir)

            if sha not in unique_hashes:
                unique_hashes[sha] = {
                    "sha256": sha,
                    "filename": att["filename"],
                    "content_type": att["content_type"],
                    "size": att["size"],
                    "storage_path": str(rel_path),
                }

            attachment_meta.append(
                {
                    "email_id": email_id,
                    "sha256": sha,
                    "filename": att["filename"],
                }
            )

    # Insert rows individually so one bad row doesn't kill the chunk.
    # Use savepoints to isolate failures — rollback to the savepoint keeps
    # all previously inserted rows intact in the outer transaction.
    inserted = 0
    skipped = 0
    errored = 0
    inserted_email_ids: set[object] = set()
    with pool.connection() as conn:
        for row in email_rows:
            try:
                conn.execute("SAVEPOINT row_insert")
                cur = conn.execute(INSERT_EMAIL_SQL, row)
                result = cur.fetchone()
                # ON CONFLICT DO UPDATE is a no-op write that always returns
                # the id — the existing row's id on conflict, or the newly
                # inserted id otherwise.
                existing_id = result[0] if result else row["id"]
                if existing_id == row["id"]:
                    inserted += 1
                    inserted_email_ids.add(row["id"])
                else:
                    skipped += 1
                # Tag with this ingest's account, idempotent per (email, account).
                if source_account is not None and import_id is not None:
                    conn.execute(
                        INSERT_EMAIL_ACCOUNT_SQL,
                        {
                            "email_id": existing_id,
                            "source_account": source_account,
                            "import_id": import_id,
                        },
                    )
                conn.execute("RELEASE SAVEPOINT row_insert")
            except Exception:
                conn.execute("ROLLBACK TO SAVEPOINT row_insert")
                logger.warning(
                    "row_insert_failed",
                    message_id=row.get("message_id"),
                    task_id=task_id,
                )
                errored += 1
                continue

        for att_row in unique_hashes.values():
            conn.execute(INSERT_ATTACHMENT_SQL, att_row)

        valid_meta = [m for m in attachment_meta if m["email_id"] in inserted_email_ids]
        if valid_meta:
            all_hashes = list({m["sha256"] for m in valid_meta})
            cur = conn.execute(
                "SELECT id, sha256 FROM attachments WHERE sha256 = ANY(%(hashes)s)",
                {"hashes": all_hashes},
            )
            hash_to_id = {row[1]: row[0] for row in cur.fetchall()}

            for meta in valid_meta:
                att_id = hash_to_id.get(meta["sha256"])
                if att_id:
                    conn.execute(
                        INSERT_EMAIL_ATTACHMENT_SQL,
                        {
                            "email_id": meta["email_id"],
                            "attachment_id": att_id,
                            "filename": meta["filename"],
                        },
                    )

        conn.commit()

    complete_task(
        pool,
        task_id,
        messages_total=len(messages),
        messages_inserted=inserted,
        messages_skipped=skipped + errored,
        attachments_extracted=len(unique_hashes),
    )
    logger.info(
        "chunk_processed",
        task_id=task_id,
        inserted=inserted,
        skipped=skipped,
        errored=errored,
    )
