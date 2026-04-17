# src/maildb/maildb.py
from __future__ import annotations

import json
import math
import time
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

import structlog
from psycopg.rows import dict_row

from maildb.config import Settings
from maildb.db import create_pool, init_db
from maildb.dsl import build_where_clause, parse_query
from maildb.embeddings import EmbeddingClient
from maildb.models import Email, SearchResult

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

logger = structlog.get_logger()

VALID_ORDERS = {
    "date DESC",
    "date ASC",
    "sender_address ASC",
    "sender_address DESC",
}

SELECT_COLS = """
    id, message_id, thread_id, subject, sender_name, sender_address,
    sender_domain, recipients, date, body_text, body_html, has_attachment,
    attachments, labels, in_reply_to, "references", embedding,
    source_account, import_id, created_at
"""


def _query_dicts(
    pool: ConnectionPool,
    sql: str,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Execute a query and return rows as dicts."""
    logger.debug("sql_execute", sql=sql, params=params)
    t0 = time.monotonic()
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        rows = [dict(row) for row in cur.fetchall()]
    elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
    logger.debug("sql_complete", rows=len(rows), elapsed_ms=elapsed_ms)
    return rows


def _query_one_dict(
    pool: ConnectionPool,
    sql: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Execute a query and return a single row as dict, or None."""
    logger.debug("sql_execute", sql=sql, params=params)
    t0 = time.monotonic()
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
    result = dict(row) if row else None
    logger.debug("sql_complete", rows=1 if result else 0, elapsed_ms=elapsed_ms)
    return result


class MailDB:
    """Primary public interface for querying the email database."""

    def __init__(self, config: Settings | None = None) -> None:
        self._config = config or Settings()
        self._pool = create_pool(self._config)
        self._embedding_client = EmbeddingClient(
            ollama_url=self._config.ollama_url,
            model_name=self._config.embedding_model,
            dimensions=self._config.embedding_dimensions,
        )

    @classmethod
    def _from_pool(
        cls,
        pool: ConnectionPool,
        config: Settings | None = None,
        embedding_client: EmbeddingClient | None = None,
    ) -> MailDB:
        """Create a MailDB instance from an existing pool (for testing)."""
        instance = object.__new__(cls)
        instance._config = config or Settings(_env_file=None)  # type: ignore[call-arg]
        instance._pool = pool
        instance._embedding_client = embedding_client or EmbeddingClient(
            ollama_url=instance._config.ollama_url,
            model_name=instance._config.embedding_model,
            dimensions=instance._config.embedding_dimensions,
        )
        return instance

    def init_db(self) -> None:
        """Initialize the database schema."""
        init_db(self._pool)

    def close(self) -> None:
        """Close the connection pool."""
        self._pool.close()

    def __enter__(self) -> MailDB:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    @staticmethod
    def _build_filters(
        *,
        sender: str | None = None,
        sender_domain: str | None = None,
        recipient: str | None = None,
        after: str | None = None,
        before: str | None = None,
        has_attachment: bool | None = None,
        subject_contains: str | None = None,
        labels: list[str] | None = None,
        max_to: int | None = None,
        max_cc: int | None = None,
        max_recipients: int | None = None,
        direct_only: bool = False,
        account: str | None = None,
    ) -> tuple[list[str], dict[str, Any]]:
        """Build WHERE-clause conditions and params from common filter kwargs."""
        if direct_only and (max_to is not None or max_cc is not None):
            msg = "Cannot combine direct_only with max_to or max_cc"
            raise ValueError(msg)

        if direct_only:
            max_to = 1
            max_cc = 0

        conditions: list[str] = []
        params: dict[str, Any] = {}

        if sender is not None:
            conditions.append("sender_address = %(sender)s")
            params["sender"] = sender
        if sender_domain is not None:
            conditions.append("sender_domain = %(sender_domain)s")
            params["sender_domain"] = sender_domain
        if recipient is not None:
            conditions.append(
                "(recipients->'to' @> %(recipient_json)s "
                "OR recipients->'cc' @> %(recipient_json)s "
                "OR recipients->'bcc' @> %(recipient_json)s)"
            )
            params["recipient_json"] = json.dumps([recipient])
        if after is not None:
            conditions.append("date >= %(after)s")
            params["after"] = after
        if before is not None:
            conditions.append("date < %(before)s")
            params["before"] = before
        if has_attachment is not None:
            conditions.append("has_attachment = %(has_attachment)s")
            params["has_attachment"] = has_attachment
        if subject_contains is not None:
            conditions.append("subject ILIKE %(subject_pattern)s ESCAPE '\\'")
            escaped = (
                subject_contains.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            params["subject_pattern"] = f"%{escaped}%"
        if labels is not None:
            conditions.append("labels @> %(labels)s")
            params["labels"] = labels
        if max_to is not None:
            conditions.append(
                "jsonb_array_length(COALESCE(recipients->'to', '[]'::jsonb)) <= %(max_to)s"
            )
            params["max_to"] = max_to
        if max_cc is not None:
            conditions.append(
                "jsonb_array_length(COALESCE(recipients->'cc', '[]'::jsonb)) <= %(max_cc)s"
            )
            params["max_cc"] = max_cc
        if max_recipients is not None:
            conditions.append(
                "(jsonb_array_length(COALESCE(recipients->'to', '[]'::jsonb))"
                " + jsonb_array_length(COALESCE(recipients->'cc', '[]'::jsonb))"
                " + jsonb_array_length(COALESCE(recipients->'bcc', '[]'::jsonb))"
                ") <= %(max_recipients)s"
            )
            params["max_recipients"] = max_recipients
        if account is not None:
            conditions.append("source_account = %(account)s")
            params["account"] = account

        return conditions, params

    def get_emails(self, message_ids: list[str]) -> list[Email]:
        """Fetch full email objects by message_id, preserving input order."""
        if not message_ids:
            return []
        placeholders = ", ".join(f"%(mid_{i})s" for i in range(len(message_ids)))
        params: dict[str, Any] = {f"mid_{i}": mid for i, mid in enumerate(message_ids)}
        sql = f"SELECT {SELECT_COLS} FROM emails WHERE message_id IN ({placeholders})"
        rows = _query_dicts(self._pool, sql, params)
        emails_by_id: dict[str, Email] = {}
        for row in rows:
            email = Email.from_row(row)
            emails_by_id[email.message_id] = email
        return [emails_by_id[mid] for mid in message_ids if mid in emails_by_id]

    def find(
        self,
        *,
        sender: str | None = None,
        sender_domain: str | None = None,
        recipient: str | None = None,
        after: str | None = None,
        before: str | None = None,
        has_attachment: bool | None = None,
        subject_contains: str | None = None,
        labels: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
        order: str = "date DESC",
        max_to: int | None = None,
        max_cc: int | None = None,
        max_recipients: int | None = None,
        direct_only: bool = False,
        account: str | None = None,
    ) -> tuple[list[Email], int]:
        """Structured query with dynamic WHERE clauses. Returns (emails, total_count)."""
        if order not in VALID_ORDERS:
            msg = f"Invalid order '{order}'. Must be one of: {', '.join(sorted(VALID_ORDERS))}"
            raise ValueError(msg)

        conditions, params = self._build_filters(
            sender=sender,
            sender_domain=sender_domain,
            recipient=recipient,
            after=after,
            before=before,
            has_attachment=has_attachment,
            subject_contains=subject_contains,
            labels=labels,
            max_to=max_to,
            max_cc=max_cc,
            max_recipients=max_recipients,
            direct_only=direct_only,
            account=account,
        )

        where = " AND ".join(conditions) if conditions else "TRUE"
        query = f"SELECT {SELECT_COLS}, COUNT(*) OVER() AS _total FROM emails WHERE {where} ORDER BY {order} LIMIT %(limit)s OFFSET %(offset)s"
        params["limit"] = limit
        params["offset"] = offset

        rows = _query_dicts(self._pool, query, params)
        total = rows[0]["_total"] if rows else 0
        for row in rows:
            row.pop("_total", None)
        return [Email.from_row(row) for row in rows], total

    def search(
        self,
        query: str,
        *,
        sender: str | None = None,
        sender_domain: str | None = None,
        recipient: str | None = None,
        after: str | None = None,
        before: str | None = None,
        has_attachment: bool | None = None,
        subject_contains: str | None = None,
        labels: list[str] | None = None,
        limit: int = 20,
        offset: int = 0,
        max_to: int | None = None,
        max_cc: int | None = None,
        max_recipients: int | None = None,
        direct_only: bool = False,
        account: str | None = None,
    ) -> tuple[list[SearchResult], int]:
        """Semantic search with optional structured filters. Returns (results, total_count)."""
        query_embedding = self._embedding_client.embed(query)

        conditions, params = self._build_filters(
            sender=sender,
            sender_domain=sender_domain,
            recipient=recipient,
            after=after,
            before=before,
            has_attachment=has_attachment,
            subject_contains=subject_contains,
            labels=labels,
            max_to=max_to,
            max_cc=max_cc,
            max_recipients=max_recipients,
            direct_only=direct_only,
            account=account,
        )
        conditions.insert(0, "embedding IS NOT NULL AND vector_norm(embedding) > 0")
        params["query_embedding"] = str(query_embedding)

        where = " AND ".join(conditions)
        sql = f"""
            SELECT {SELECT_COLS},
                   1 - (embedding <=> %(query_embedding)s::vector) AS similarity,
                   COUNT(*) OVER() AS _total
            FROM emails
            WHERE {where}
            ORDER BY embedding <=> %(query_embedding)s::vector
            LIMIT %(limit)s OFFSET %(offset)s
        """
        params["limit"] = limit
        params["offset"] = offset

        rows = _query_dicts(self._pool, sql, params)
        total = rows[0]["_total"] if rows else 0
        for row in rows:
            row.pop("_total", None)
        return [
            SearchResult(
                email=Email.from_row(row),
                similarity=row["similarity"],
            )
            for row in rows
        ], total

    def get_thread(self, thread_id: str) -> list[Email]:
        """Retrieve all messages in a thread, ordered by date."""
        sql = f"""
            SELECT {SELECT_COLS}
            FROM emails
            WHERE thread_id = %(thread_id)s
            ORDER BY date ASC
        """
        rows = _query_dicts(self._pool, sql, {"thread_id": thread_id})
        return [Email.from_row(row) for row in rows]

    def get_thread_for(self, message_id: str) -> list[Email]:
        """Find the thread containing a specific message and return the full thread."""
        sql = """SELECT thread_id FROM emails WHERE message_id = %(message_id)s"""
        row = _query_one_dict(self._pool, sql, {"message_id": message_id})
        if row is None:
            return []
        return self.get_thread(row["thread_id"])

    # --- Advanced query methods ---

    def _require_user_email(self) -> str:
        if not self._config.user_email:
            msg = "user_email must be set in config for this method"
            raise ValueError(msg)
        return self._config.user_email

    def top_contacts(
        self,
        *,
        period: str | None = None,
        limit: int = 10,
        offset: int = 0,
        direction: str = "both",
        group_by: str = "address",
        exclude_domains: list[str] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Most frequent correspondents via GROUP BY aggregation.

        Args:
            period: Only count messages after this date (ISO format).
            limit: Max results to return.
            direction: 'inbound', 'outbound', or 'both'.
            group_by: 'address' (default) or 'domain' to aggregate by domain.
            exclude_domains: List of domains to exclude from results.
        """
        if group_by not in ("address", "domain"):
            msg = f"group_by must be 'address' or 'domain', got {group_by!r}"
            raise ValueError(msg)

        user_email = self._require_user_email()
        params: dict[str, Any] = {"user_email": user_email, "limit": limit, "offset": offset}

        if period:
            period_cond = "AND date >= %(period_start)s"
            params["period_start"] = period
        else:
            period_cond = ""

        if exclude_domains:
            params["exclude_domains"] = exclude_domains
            exclude_inbound = "AND sender_domain != ALL(%(exclude_domains)s)"
            exclude_outbound = "AND split_part(r.addr, '@', 2) != ALL(%(exclude_domains)s)"
        else:
            exclude_inbound = ""
            exclude_outbound = ""

        # Column aliases depend on group_by mode
        label = group_by  # "address" or "domain"

        if group_by == "domain":
            inbound_col = "sender_domain"
            outbound_col = "split_part(r.addr, '@', 2)"
        else:
            inbound_col = "sender_address"
            outbound_col = "r.addr"

        if direction == "inbound":
            sql = f"""
                SELECT {inbound_col} AS {label}, count(*) AS count, COUNT(*) OVER() AS _total
                FROM emails
                WHERE sender_address != %(user_email)s
                  {period_cond}
                  {exclude_inbound}
                GROUP BY {inbound_col}
                ORDER BY count DESC
                LIMIT %(limit)s OFFSET %(offset)s
            """
            rows = _query_dicts(self._pool, sql, params)
            total = rows[0]["_total"] if rows else 0
            for row in rows:
                row.pop("_total", None)
            return rows, total

        if direction == "outbound":
            sql = f"""
                SELECT {outbound_col} AS {label}, count(*) AS count, COUNT(*) OVER() AS _total
                FROM emails,
                     LATERAL (
                         SELECT jsonb_array_elements_text(recipients->'to') AS addr
                         UNION ALL
                         SELECT jsonb_array_elements_text(recipients->'cc')
                         UNION ALL
                         SELECT jsonb_array_elements_text(recipients->'bcc')
                     ) AS r(addr)
                WHERE sender_address = %(user_email)s
                  AND r.addr != %(user_email)s
                  {period_cond}
                  {exclude_outbound}
                GROUP BY {outbound_col}
                ORDER BY count DESC
                LIMIT %(limit)s OFFSET %(offset)s
            """
            rows = _query_dicts(self._pool, sql, params)
            total = rows[0]["_total"] if rows else 0
            for row in rows:
                row.pop("_total", None)
            return rows, total

        sql = f"""
            SELECT {label}, sum(count) AS count, COUNT(*) OVER() AS _total
            FROM (
                SELECT {inbound_col} AS {label}, count(*) AS count
                FROM emails
                WHERE sender_address != %(user_email)s
                  {period_cond}
                  {exclude_inbound}
                GROUP BY {inbound_col}

                UNION ALL

                SELECT {outbound_col} AS {label}, count(*) AS count
                FROM emails,
                     LATERAL (
                         SELECT jsonb_array_elements_text(recipients->'to') AS addr
                         UNION ALL
                         SELECT jsonb_array_elements_text(recipients->'cc')
                         UNION ALL
                         SELECT jsonb_array_elements_text(recipients->'bcc')
                     ) AS r(addr)
                WHERE sender_address = %(user_email)s
                  AND r.addr != %(user_email)s
                  {period_cond}
                  {exclude_outbound}
                GROUP BY {outbound_col}
            ) AS combined
            GROUP BY {label}
            ORDER BY count DESC
            LIMIT %(limit)s OFFSET %(offset)s
        """
        rows = _query_dicts(self._pool, sql, params)
        total = rows[0]["_total"] if rows else 0
        for row in rows:
            row.pop("_total", None)
        return rows, total

    def topics_with(
        self,
        *,
        sender: str | None = None,
        sender_domain: str | None = None,
        limit: int = 5,
        offset: int = 0,
    ) -> tuple[list[Email], int]:
        """Representative emails spanning different topics with a contact. Returns (emails, total_count).

        Uses greedy farthest-point selection on embeddings.
        """
        conditions: list[str] = ["embedding IS NOT NULL"]
        params: dict[str, Any] = {}

        if sender:
            conditions.append("sender_address = %(sender)s")
            params["sender"] = sender
        elif sender_domain:
            conditions.append("sender_domain = %(sender_domain)s")
            params["sender_domain"] = sender_domain
        else:
            msg = "Either sender or sender_domain must be provided"
            raise ValueError(msg)

        where = " AND ".join(conditions)
        sql = f"SELECT {SELECT_COLS} FROM emails WHERE {where} ORDER BY date DESC LIMIT 500"

        rows = _query_dicts(self._pool, sql, params)
        if not rows:
            return [], 0

        total = len(rows)
        emails = [Email.from_row(row) for row in rows]
        selected = self._farthest_point_select(emails, limit + offset)
        return selected[offset:], total

    @staticmethod
    def _farthest_point_select(emails: list[Email], limit: int) -> list[Email]:
        """Greedy farthest-point selection on embeddings for diverse topic extraction."""
        if len(emails) <= limit:
            return emails
        selected: list[Email] = [emails[0]]
        remaining = list(emails[1:])
        while len(selected) < limit and remaining:
            best_idx = -1
            best_dist = -1.0
            for i, candidate in enumerate(remaining):
                if candidate.embedding is None:
                    continue
                min_dist = float("inf")
                for sel in selected:
                    if sel.embedding is None:
                        continue
                    dist = MailDB._cosine_distance(candidate.embedding, sel.embedding)
                    min_dist = min(min_dist, dist)
                if min_dist > best_dist:
                    best_dist = min_dist
                    best_idx = i
            if best_idx < 0:
                break
            selected.append(remaining.pop(best_idx))
        return selected

    def cluster(
        self,
        *,
        where: dict[str, Any] | None = None,
        message_ids: list[str] | None = None,
        limit: int = 5,
        offset: int = 0,
    ) -> tuple[list[Email], int]:
        """Diverse topic extraction from arbitrary email subsets. Returns (emails, total_count).

        Provide either where (DSL filter) or message_ids (explicit list), not both.
        """
        if where is None and message_ids is None:
            msg = "Either where or message_ids must be provided"
            raise ValueError(msg)
        if where is not None and message_ids is not None:
            msg = "Provide either where or message_ids, not both"
            raise ValueError(msg)

        if message_ids is not None:
            if not message_ids:
                return [], 0
            placeholders = ", ".join(f"%(mid_{i})s" for i in range(len(message_ids)))
            params: dict[str, Any] = {f"mid_{i}": mid for i, mid in enumerate(message_ids)}
            sql = f"""
                SELECT {SELECT_COLS} FROM emails
                WHERE message_id IN ({placeholders})
                  AND embedding IS NOT NULL
                ORDER BY date DESC
            """
            rows = _query_dicts(self._pool, sql, params)
        else:
            where_sql, params = build_where_clause(where, source="emails")  # type: ignore[arg-type]
            sql = f"""
                SELECT {SELECT_COLS} FROM emails
                WHERE {where_sql} AND embedding IS NOT NULL
                ORDER BY date DESC LIMIT 500
            """
            rows = _query_dicts(self._pool, sql, params)

        total = len(rows)
        emails = [Email.from_row(row) for row in rows]
        if not emails:
            return [], 0
        selected = self._farthest_point_select(emails, limit + offset)
        return selected[offset:], total

    @staticmethod
    def _cosine_distance(a: list[float], b: list[float]) -> float:
        """Compute cosine distance between two vectors."""
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(x * x for x in b))
        if norm_a == 0 or norm_b == 0:
            return 1.0
        return 1.0 - dot / (norm_a * norm_b)

    def unreplied(
        self,
        *,
        direction: Literal["inbound", "outbound"] = "inbound",
        recipient: str | None = None,
        after: str | None = None,
        before: str | None = None,
        sender: str | None = None,
        sender_domain: str | None = None,
        limit: int = 100,
        offset: int = 0,
        max_to: int | None = None,
        max_cc: int | None = None,
        max_recipients: int | None = None,
        direct_only: bool = False,
    ) -> tuple[list[Email], int]:
        """Messages with no reply in the same thread.

        Args:
            direction: "inbound" (default) — messages FROM others where user never
                replied. "outbound" — messages FROM user where recipient (or anyone)
                never replied.
            recipient: For outbound only — filter to messages sent to this address
                (To/CC/BCC) and check that *this* recipient never replied.
            after: Only include messages on or after this date (ISO 8601).
            before: Only include messages before this date (ISO 8601).
            sender: For inbound — filter to messages from this sender.
            sender_domain: For inbound — filter to messages from this domain.
            limit: Maximum number of results (default 100).
        """
        if direction not in ("inbound", "outbound"):
            msg = f"Invalid direction: {direction!r}. Must be 'inbound' or 'outbound'."
            raise ValueError(msg)

        if direction == "inbound" and recipient is not None:
            raise ValueError("'recipient' is only valid for direction='outbound'")
        if direction == "outbound" and (sender is not None or sender_domain is not None):
            raise ValueError("'sender'/'sender_domain' are only valid for direction='inbound'")

        user_email = self._require_user_email()
        params: dict[str, Any] = {"user_email": user_email}

        select_cols_aliased = """
            e.id, e.message_id, e.thread_id, e.subject, e.sender_name, e.sender_address,
            e.sender_domain, e.recipients, e.date, e.body_text, e.body_html, e.has_attachment,
            e.attachments, e.labels, e.in_reply_to, e."references", e.embedding,
            e.source_account, e.import_id, e.created_at
        """

        if direction == "inbound":
            conditions: list[str] = [
                "e.sender_address != %(user_email)s",
                "e.date IS NOT NULL",
            ]
            if after:
                conditions.append("e.date >= %(after)s")
                params["after"] = after
            if before:
                conditions.append("e.date < %(before)s")
                params["before"] = before
            if sender:
                conditions.append("e.sender_address = %(sender)s")
                params["sender"] = sender
            if sender_domain:
                conditions.append("e.sender_domain = %(sender_domain)s")
                params["sender_domain"] = sender_domain

            where = " AND ".join(conditions)
            params["limit"] = limit
            params["offset"] = offset
            sql = f"""
                SELECT {select_cols_aliased}, COUNT(*) OVER() AS _total
                FROM emails e
                WHERE {where}
                  AND NOT EXISTS (
                      SELECT 1 FROM emails reply
                      WHERE reply.thread_id = e.thread_id
                        AND reply.sender_address = %(user_email)s
                        AND reply.date > e.date
                  )
                ORDER BY e.date DESC
                LIMIT %(limit)s OFFSET %(offset)s
            """
        else:
            # Outbound: messages FROM user where recipients never replied
            conditions = [
                "e.sender_address = %(user_email)s",
                "e.date IS NOT NULL",
            ]
            if after:
                conditions.append("e.date >= %(after)s")
                params["after"] = after
            if before:
                conditions.append("e.date < %(before)s")
                params["before"] = before

            if recipient:
                recipient_json = json.dumps([recipient])
                conditions.append(
                    "(e.recipients->'to' @> %(recipient_json)s"
                    " OR e.recipients->'cc' @> %(recipient_json)s"
                    " OR e.recipients->'bcc' @> %(recipient_json)s)"
                )
                params["recipient_json"] = recipient_json
                not_exists = """
                    AND NOT EXISTS (
                        SELECT 1 FROM emails reply
                        WHERE reply.thread_id = e.thread_id
                          AND reply.sender_address = %(recipient)s
                          AND reply.date > e.date
                    )
                """
                params["recipient"] = recipient
            else:
                not_exists = """
                    AND NOT EXISTS (
                        SELECT 1 FROM emails reply
                        WHERE reply.thread_id = e.thread_id
                          AND reply.sender_address != %(user_email)s
                          AND reply.date > e.date
                    )
                """

            where = " AND ".join(conditions)
            params["limit"] = limit
            params["offset"] = offset
            sql = f"""
                SELECT {select_cols_aliased}, COUNT(*) OVER() AS _total
                FROM emails e
                WHERE {where}
                  {not_exists}
                ORDER BY e.date DESC
                LIMIT %(limit)s OFFSET %(offset)s
            """

        rows = _query_dicts(self._pool, sql, params)
        total = rows[0]["_total"] if rows else 0
        for row in rows:
            row.pop("_total", None)
        return [Email.from_row(row) for row in rows], total

    def correspondence(
        self,
        *,
        address: str,
        after: str | None = None,
        before: str | None = None,
        limit: int = 500,
        offset: int = 0,
        order: str = "date ASC",
    ) -> tuple[list[Email], int]:
        """All emails exchanged with a specific person. Returns (emails, total_count).
        Returns emails where address is sender OR is in recipients (to/cc/bcc).
        Default chronological order, higher limit than find().
        """
        if order not in VALID_ORDERS:
            msg = f"Invalid order '{order}'. Must be one of: {', '.join(sorted(VALID_ORDERS))}"
            raise ValueError(msg)

        conditions: list[str] = [
            "(sender_address = %(address)s "
            "OR recipients->'to' @> %(address_json)s "
            "OR recipients->'cc' @> %(address_json)s "
            "OR recipients->'bcc' @> %(address_json)s)"
        ]
        params: dict[str, Any] = {
            "address": address,
            "address_json": json.dumps([address]),
            "limit": limit,
            "offset": offset,
        }

        if after:
            conditions.append("date >= %(after)s")
            params["after"] = after
        if before:
            conditions.append("date < %(before)s")
            params["before"] = before

        where = " AND ".join(conditions)
        sql = f"SELECT {SELECT_COLS}, COUNT(*) OVER() AS _total FROM emails WHERE {where} ORDER BY {order} LIMIT %(limit)s OFFSET %(offset)s"

        rows = _query_dicts(self._pool, sql, params)
        total = rows[0]["_total"] if rows else 0
        for row in rows:
            row.pop("_total", None)
        return [Email.from_row(row) for row in rows], total

    def mention_search(
        self,
        *,
        text: str,
        sender: str | None = None,
        sender_domain: str | None = None,
        after: str | None = None,
        before: str | None = None,
        limit: int = 50,
        offset: int = 0,
        max_to: int | None = None,
        max_cc: int | None = None,
        max_recipients: int | None = None,
        direct_only: bool = False,
        account: str | None = None,
    ) -> tuple[list[Email], int]:
        """Case-insensitive keyword search in body_text and subject. Returns (emails, total_count).
        Unlike search(), uses ILIKE (substring match) and does not require Ollama.
        """
        escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        conditions: list[str] = [
            "(body_text ILIKE %(pattern)s ESCAPE '\\' OR subject ILIKE %(pattern)s ESCAPE '\\')"
        ]
        params: dict[str, Any] = {"pattern": pattern, "limit": limit, "offset": offset}
        if sender:
            conditions.append("sender_address = %(sender)s")
            params["sender"] = sender
        if sender_domain:
            conditions.append("sender_domain = %(sender_domain)s")
            params["sender_domain"] = sender_domain
        if after:
            conditions.append("date >= %(after)s")
            params["after"] = after
        if before:
            conditions.append("date < %(before)s")
            params["before"] = before
        if account is not None:
            conditions.append("source_account = %(account)s")
            params["account"] = account
        # Recipient count filters
        rcpt_conditions, rcpt_params = self._build_filters(
            max_to=max_to,
            max_cc=max_cc,
            max_recipients=max_recipients,
            direct_only=direct_only,
        )
        conditions.extend(rcpt_conditions)
        params.update(rcpt_params)
        where = " AND ".join(conditions)
        sql = f"SELECT {SELECT_COLS}, COUNT(*) OVER() AS _total FROM emails WHERE {where} ORDER BY date DESC LIMIT %(limit)s OFFSET %(offset)s"
        rows = _query_dicts(self._pool, sql, params)
        total = rows[0]["_total"] if rows else 0
        for row in rows:
            row.pop("_total", None)
        return [Email.from_row(row) for row in rows], total

    def query(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Execute a Tier 2 DSL query and return results as dicts.
        Accepts a DSL specification dict. See dsl.py for full schema.
        Enforces 5s statement timeout and 1000-row hard cap.
        """
        sql, params = parse_query(spec)
        with self._pool.connection() as conn:
            conn.execute("SET LOCAL statement_timeout = '5s'")
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params)
                rows = [dict(row) for row in cur.fetchall()]
            conn.commit()  # releases the SET LOCAL
        return self._serialize_query_results(rows)

    @staticmethod
    def _serialize_query_results(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Make query results JSON-serializable."""

        def _convert(v: Any) -> Any:
            if isinstance(v, UUID):
                return str(v)
            if isinstance(v, (datetime, date)):
                return v.isoformat()
            if isinstance(v, Decimal):
                return float(v)
            return v

        return [{k: _convert(v) for k, v in row.items()} for row in rows]

    def long_threads(
        self,
        *,
        participant: str | None = None,
        min_messages: int = 5,
        after: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Threads exceeding a message count threshold. Returns (threads, total_count).
        participant: only threads where this address appears as sender.
        """
        conditions: list[str] = []
        params: dict[str, Any] = {"min_messages": min_messages, "limit": limit, "offset": offset}
        if after:
            conditions.append("date >= %(after)s")
            params["after"] = after
        where = " AND ".join(conditions) if conditions else "TRUE"
        having_participant = ""
        if participant:
            having_participant = "AND %(participant)s = ANY(array_agg(sender_address))"
            params["participant"] = participant
        sql = f"""
            SELECT thread_id, count(*) AS message_count,
                   min(date) AS first_date, max(date) AS last_date,
                   array_agg(DISTINCT sender_address) AS participants,
                   COUNT(*) OVER() AS _total
            FROM emails WHERE {where}
            GROUP BY thread_id
            HAVING count(*) >= %(min_messages)s {having_participant}
            ORDER BY count(*) DESC
            LIMIT %(limit)s OFFSET %(offset)s
        """
        rows = _query_dicts(self._pool, sql, params)
        total = rows[0]["_total"] if rows else 0
        for row in rows:
            row.pop("_total", None)
        return rows, total
