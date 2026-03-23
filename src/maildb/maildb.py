# src/maildb/maildb.py
from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING, Any

import structlog
from psycopg.rows import dict_row

from maildb.config import Settings
from maildb.db import create_pool, init_db
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
    attachments, labels, in_reply_to, "references", embedding, created_at
"""


def _query_dicts(
    pool: ConnectionPool,
    sql: str,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Execute a query and return rows as dicts."""
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


def _query_one_dict(
    pool: ConnectionPool,
    sql: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Execute a query and return a single row as dict, or None."""
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None


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
    ) -> tuple[list[str], dict[str, Any]]:
        """Build WHERE-clause conditions and params from common filter kwargs."""
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

        return conditions, params

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
        order: str = "date DESC",
    ) -> list[Email]:
        """Structured query with dynamic WHERE clauses."""
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
        )

        where = " AND ".join(conditions) if conditions else "TRUE"
        query = f"SELECT {SELECT_COLS} FROM emails WHERE {where} ORDER BY {order} LIMIT %(limit)s"
        params["limit"] = limit

        rows = _query_dicts(self._pool, query, params)
        return [Email.from_row(row) for row in rows]

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
    ) -> list[SearchResult]:
        """Semantic search with optional structured filters."""
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
        )
        conditions.insert(0, "embedding IS NOT NULL")
        params["query_embedding"] = str(query_embedding)

        where = " AND ".join(conditions)
        sql = f"""
            SELECT {SELECT_COLS},
                   1 - (embedding <=> %(query_embedding)s::vector) AS similarity
            FROM emails
            WHERE {where}
            ORDER BY embedding <=> %(query_embedding)s::vector
            LIMIT %(limit)s
        """
        params["limit"] = limit

        rows = _query_dicts(self._pool, sql, params)
        return [
            SearchResult(
                email=Email.from_row(row),
                similarity=row["similarity"],
            )
            for row in rows
        ]

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
        direction: str = "both",
    ) -> list[dict[str, Any]]:
        """Most frequent correspondents via GROUP BY aggregation."""
        user_email = self._require_user_email()

        conditions: list[str] = []
        params: dict[str, Any] = {"user_email": user_email, "limit": limit}

        if direction == "inbound":
            conditions.append("sender_address != %(user_email)s")
            group_col = "sender_address"
        elif direction == "outbound":
            conditions.append("sender_address = %(user_email)s")
            # For outbound, group by recipient — need a different query
            if period:
                period_cond = "AND date >= %(period_start)s"
                params["period_start"] = period
            else:
                period_cond = ""

            sql = f"""
                SELECT r.addr AS address, count(*) AS count
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
                GROUP BY r.addr
                ORDER BY count DESC
                LIMIT %(limit)s
            """
            return _query_dicts(self._pool, sql, params)
        else:  # both
            if period:
                period_cond_inbound = "AND date >= %(period_start)s"
                period_cond_outbound = "AND date >= %(period_start)s"
                params["period_start"] = period
            else:
                period_cond_inbound = ""
                period_cond_outbound = ""

            sql = f"""
                SELECT address, sum(count) AS count
                FROM (
                    SELECT sender_address AS address, count(*) AS count
                    FROM emails
                    WHERE sender_address != %(user_email)s
                      {period_cond_inbound}
                    GROUP BY sender_address

                    UNION ALL

                    SELECT r.addr AS address, count(*) AS count
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
                      {period_cond_outbound}
                    GROUP BY r.addr
                ) AS combined
                GROUP BY address
                ORDER BY count DESC
                LIMIT %(limit)s
            """
            return _query_dicts(self._pool, sql, params)

        if period:
            conditions.append("date >= %(period_start)s")
            params["period_start"] = period

        where = " AND ".join(conditions) if conditions else "TRUE"
        sql = f"""
            SELECT {group_col} AS address, count(*) AS count
            FROM emails
            WHERE {where}
            GROUP BY {group_col}
            ORDER BY count DESC
            LIMIT %(limit)s
        """

        return _query_dicts(self._pool, sql, params)

    def topics_with(
        self,
        *,
        sender: str | None = None,
        sender_domain: str | None = None,
        limit: int = 5,
    ) -> list[Email]:
        """Representative emails spanning different topics with a contact.

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
            return []

        emails = [Email.from_row(row) for row in rows]

        # Greedy farthest-point selection
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
                # Min distance to any already-selected email
                min_dist = float("inf")
                for sel in selected:
                    if sel.embedding is None:
                        continue
                    dist = self._cosine_distance(candidate.embedding, sel.embedding)
                    min_dist = min(min_dist, dist)
                if min_dist > best_dist:
                    best_dist = min_dist
                    best_idx = i

            if best_idx < 0:
                break
            selected.append(remaining.pop(best_idx))

        return selected

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
        after: str | None = None,
        before: str | None = None,
        sender: str | None = None,
        sender_domain: str | None = None,
        limit: int = 100,
    ) -> list[Email]:
        """Inbound messages with no outbound reply in the same thread."""
        user_email = self._require_user_email()

        conditions: list[str] = [
            "e.sender_address != %(user_email)s",
        ]
        params: dict[str, Any] = {"user_email": user_email}

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

        select_cols_aliased = """
            e.id, e.message_id, e.thread_id, e.subject, e.sender_name, e.sender_address,
            e.sender_domain, e.recipients, e.date, e.body_text, e.body_html, e.has_attachment,
            e.attachments, e.labels, e.in_reply_to, e."references", e.embedding, e.created_at
        """

        params["limit"] = limit
        sql = f"""
            SELECT {select_cols_aliased}
            FROM emails e
            WHERE {where}
              AND NOT EXISTS (
                  SELECT 1 FROM emails reply
                  WHERE reply.thread_id = e.thread_id
                    AND reply.sender_address = %(user_email)s
                    AND reply.date > e.date
              )
            ORDER BY e.date DESC
            LIMIT %(limit)s
        """

        rows = _query_dicts(self._pool, sql, params)
        return [Email.from_row(row) for row in rows]

    def long_threads(
        self,
        *,
        min_messages: int = 5,
        after: str | None = None,
    ) -> list[dict[str, Any]]:
        """Threads exceeding a message count threshold."""
        conditions: list[str] = []
        params: dict[str, Any] = {"min_messages": min_messages}

        if after:
            conditions.append("date >= %(after)s")
            params["after"] = after

        where = " AND ".join(conditions) if conditions else "TRUE"

        sql = f"""
            SELECT thread_id,
                   count(*) AS message_count,
                   min(date) AS first_date,
                   max(date) AS last_date,
                   array_agg(DISTINCT sender_address) AS participants
            FROM emails
            WHERE {where}
            GROUP BY thread_id
            HAVING count(*) >= %(min_messages)s
            ORDER BY count(*) DESC
        """

        return _query_dicts(self._pool, sql, params)
