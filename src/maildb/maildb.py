# src/maildb/maildb.py
from __future__ import annotations

import json
import time
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

import numpy as np
import structlog
from psycopg.rows import dict_row

from maildb.config import Settings
from maildb.db import create_pool, init_db
from maildb.dsl import build_where_clause, parse_query
from maildb.embeddings import EmbeddingClient
from maildb.models import (
    AccountSummary,
    AttachmentChunk,
    AttachmentSearchResult,
    Email,
    ImportRecord,
    SearchResult,
    UnifiedSearchResult,
)

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

logger = structlog.get_logger()

VALID_ORDERS = {
    "date DESC",
    "date ASC",
    "sender_address ASC",
    "sender_address DESC",
}

VALID_CONTACT_KINDS = frozenset({"human", "organization", "automated", "mailing_list", "unknown"})

# Standard reciprocal-rank-fusion constant; higher values flatten the rank penalty.
RRF_K = 60

SELECT_COLS = """
    id, message_id, thread_id, subject, sender_name, sender_address,
    sender_domain, recipients, date, body_text, body_html, has_attachment,
    attachments, labels, in_reply_to, "references", embedding,
    source_account, import_id, created_at
"""

LIST_COLS = """
    id, message_id, thread_id, subject, sender_name, sender_address,
    sender_domain, recipients, date, body_text, has_attachment,
    attachments, labels, in_reply_to, "references",
    source_account, import_id, created_at
"""


def _order_clause(order: str, *, qualifier: str = "") -> str:
    """Return the deterministic SQL ORDER BY clause for a validated public order."""
    if order not in VALID_ORDERS:
        msg = f"Invalid order '{order}'. Must be one of: {', '.join(sorted(VALID_ORDERS))}"
        raise ValueError(msg)

    prefix = f"{qualifier}." if qualifier else ""
    if order == "date DESC":
        return f"{prefix}date DESC NULLS LAST, {prefix}id"
    if order == "date ASC":
        return f"{prefix}date ASC NULLS FIRST, {prefix}id"
    if order == "sender_address DESC":
        return f"{prefix}sender_address DESC, {prefix}id"
    return f"{prefix}sender_address ASC, {prefix}id"


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


def _count(pool: ConnectionPool, base_sql: str, params: dict[str, Any]) -> int:
    """Exact row count for a query body (no ORDER BY/LIMIT/OFFSET)."""
    sql = f"SELECT COUNT(*) AS n FROM ({base_sql}) AS _count_sub"
    row = _query_one_dict(pool, sql, params)
    return int(row["n"]) if row is not None else 0


def _query_dicts_with_hnsw_ef_search(
    pool: ConnectionPool,
    sql: str,
    params: dict[str, Any],
    *,
    ef_search: str,
) -> list[dict[str, Any]]:
    """Execute a vector query after raising transaction-local HNSW search breadth."""
    logger.debug("sql_execute", sql=sql, params=params, ef_search=ef_search)
    t0 = time.monotonic()
    with pool.connection() as conn, conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT set_config('hnsw.ef_search', %(ef_search)s, true)", {"ef_search": ef_search}
        )
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
        column_prefix: str = "",
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
            conditions.append(f"{column_prefix}sender_address = %(sender)s")
            params["sender"] = sender
        if sender_domain is not None:
            conditions.append(f"{column_prefix}sender_domain = %(sender_domain)s")
            params["sender_domain"] = sender_domain
        if recipient is not None:
            conditions.append(
                f"({column_prefix}recipients @> jsonb_build_object('to', %(recipient_arr)s::jsonb) "
                f"OR {column_prefix}recipients @> jsonb_build_object('cc', %(recipient_arr)s::jsonb) "
                f"OR {column_prefix}recipients @> jsonb_build_object('bcc', %(recipient_arr)s::jsonb))"
            )
            params["recipient_arr"] = json.dumps([recipient])
        if after is not None:
            conditions.append(f"{column_prefix}date >= %(after)s")
            params["after"] = after
        if before is not None:
            conditions.append(f"{column_prefix}date < %(before)s")
            params["before"] = before
        if has_attachment is not None:
            conditions.append(f"{column_prefix}has_attachment = %(has_attachment)s")
            params["has_attachment"] = has_attachment
        if subject_contains is not None:
            conditions.append(f"{column_prefix}subject ILIKE %(subject_pattern)s ESCAPE '\\'")
            escaped = (
                subject_contains.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            params["subject_pattern"] = f"%{escaped}%"
        if labels is not None:
            conditions.append(f"{column_prefix}labels @> %(labels)s")
            params["labels"] = labels
        if max_to is not None:
            conditions.append(
                f"jsonb_array_length(COALESCE({column_prefix}recipients->'to', '[]'::jsonb)) <= %(max_to)s"
            )
            params["max_to"] = max_to
        if max_cc is not None:
            conditions.append(
                f"jsonb_array_length(COALESCE({column_prefix}recipients->'cc', '[]'::jsonb)) <= %(max_cc)s"
            )
            params["max_cc"] = max_cc
        if max_recipients is not None:
            conditions.append(
                f"(jsonb_array_length(COALESCE({column_prefix}recipients->'to', '[]'::jsonb))"
                f" + jsonb_array_length(COALESCE({column_prefix}recipients->'cc', '[]'::jsonb))"
                f" + jsonb_array_length(COALESCE({column_prefix}recipients->'bcc', '[]'::jsonb))"
                ") <= %(max_recipients)s"
            )
            params["max_recipients"] = max_recipients
        if account is not None:
            email_id_ref = f"{column_prefix}id" if column_prefix else "emails.id"
            conditions.append(
                "EXISTS (SELECT 1 FROM email_accounts ea "
                f"WHERE ea.email_id = {email_id_ref} AND ea.source_account = %(account)s)"
            )
            params["account"] = account

        return conditions, params

    def get_emails(self, message_ids: list[str]) -> list[Email]:
        """Fetch full email objects by message_id, preserving input order."""
        if not message_ids:
            return []
        placeholders = ", ".join(f"%(mid_{i})s" for i in range(len(message_ids)))
        params: dict[str, Any] = {f"mid_{i}": mid for i, mid in enumerate(message_ids)}
        sql = f"SELECT {LIST_COLS} FROM emails WHERE message_id IN ({placeholders})"
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
        include_total: bool = False,
    ) -> tuple[list[Email], int | None]:
        """Structured query with dynamic WHERE clauses.

        Returns (emails, total) where total is None unless include_total=True;
        when present it is the exact count of matching rows regardless of offset.
        """
        order_sql = _order_clause(order)

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
        query = (
            f"SELECT {LIST_COLS} FROM emails WHERE {where} "
            f"ORDER BY {order_sql} LIMIT %(limit)s OFFSET %(offset)s"
        )
        params["limit"] = limit
        params["offset"] = offset

        rows = _query_dicts(self._pool, query, params)
        results = [Email.from_row(row) for row in rows]
        if not include_total:
            return results, None
        return results, _count(self._pool, f"SELECT 1 FROM emails WHERE {where}", params)

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
        """Semantic search with optional structured filters.

        Returns total as an approximate count of results seen so far
        (offset + rows returned), not an exact match count.
        """
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
            SELECT {LIST_COLS},
                   1 - (embedding <=> %(query_embedding)s::vector) AS similarity
            FROM emails
            WHERE {where}
            ORDER BY embedding <=> %(query_embedding)s::vector
            LIMIT %(limit)s OFFSET %(offset)s
        """
        params["limit"] = limit
        params["offset"] = offset

        rows = _query_dicts_with_hnsw_ef_search(
            self._pool,
            sql,
            params,
            ef_search=str(max(40, limit + offset)),
        )
        total = offset + len(rows)
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
            SELECT {LIST_COLS}
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

    def _effective_user_emails(self) -> list[str]:
        """Merge configured user_emails with every account we've ingested.

        Configured addresses keep their relative order (env-first).
        Ingested accounts from `imports` fill in anything the config missed.
        Deduplicated.
        """
        with self._pool.connection() as conn:
            cur = conn.execute("SELECT DISTINCT source_account FROM imports")
            ingested = [r[0] for r in cur.fetchall() if r[0]]
        seen: set[str] = set()
        merged: list[str] = []
        for addr in (*self._config.user_emails, *ingested):
            if addr and addr not in seen:
                seen.add(addr)
                merged.append(addr)
        return merged

    def _identity_addresses(self, account: str | None) -> list[str]:
        """Return the addresses that represent 'you' for identity-aware queries.

        If `account` is provided, returns just that single address.
        Otherwise returns the effective user_emails list (config + imports).
        Raises if neither config nor imports yields anything.
        """
        if account is not None:
            return [account]
        identities = self._effective_user_emails()
        if identities:
            return identities
        msg = "user_emails must be configured (or pass account=...) for this method"
        raise ValueError(msg)

    def top_contacts(
        self,
        *,
        period: str | None = None,
        limit: int = 10,
        offset: int = 0,
        direction: str = "both",
        group_by: str = "address",
        exclude_domains: list[str] | None = None,
        account: str | None = None,
        include_total: bool = False,
    ) -> tuple[list[dict[str, Any]], int | None]:
        """Most frequent correspondents via GROUP BY aggregation.

        Returns (results, total) where total is None unless include_total=True;
        when present it is the exact count of matching rows regardless of offset.

        Args:
            period: Only count messages after this date (ISO format).
            limit: Max results to return.
            direction: 'inbound', 'outbound', or 'both'.
            group_by: 'address' (default) or 'domain' to aggregate by domain.
            exclude_domains: List of domains to exclude from results.
            account: Scope to a single source_account. When provided,
                "you" = that account. When omitted, "you" = any configured user_emails.
            include_total: When True, compute an exact total via a separate count query.
        """
        if group_by not in ("address", "domain"):
            msg = f"group_by must be 'address' or 'domain', got {group_by!r}"
            raise ValueError(msg)

        identities = self._identity_addresses(account)
        params: dict[str, Any] = {
            "user_emails": identities,
            "limit": limit,
            "offset": offset,
        }

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

        if account is not None:
            account_cond = (
                "AND EXISTS (SELECT 1 FROM email_accounts ea "
                "WHERE ea.email_id = emails.id AND ea.source_account = %(account)s)"
            )
            params["account"] = account
        else:
            account_cond = ""

        label = group_by
        if group_by == "domain":
            inbound_col = "sender_domain"
            outbound_col = "split_part(r.addr, '@', 2)"
        else:
            inbound_col = "sender_address"
            outbound_col = "r.addr"

        if direction == "inbound":
            base_sql = f"""
                SELECT {inbound_col} AS {label}, count(*) AS count
                FROM emails
                WHERE sender_address != ALL(%(user_emails)s)
                  {period_cond}
                  {exclude_inbound}
                  {account_cond}
                GROUP BY {inbound_col}
            """
        elif direction == "outbound":
            base_sql = f"""
                SELECT {outbound_col} AS {label}, count(*) AS count
                FROM emails,
                     LATERAL (
                         SELECT jsonb_array_elements_text(recipients->'to') AS addr
                         UNION ALL
                         SELECT jsonb_array_elements_text(recipients->'cc')
                         UNION ALL
                         SELECT jsonb_array_elements_text(recipients->'bcc')
                     ) AS r(addr)
                WHERE sender_address = ANY(%(user_emails)s)
                  AND r.addr != ALL(%(user_emails)s)
                  {period_cond}
                  {exclude_outbound}
                  {account_cond}
                GROUP BY {outbound_col}
            """
        else:  # both
            base_sql = f"""
                SELECT {label}, sum(count) AS count
                FROM (
                    SELECT {inbound_col} AS {label}, count(*) AS count
                    FROM emails
                    WHERE sender_address != ALL(%(user_emails)s)
                      {period_cond}
                      {exclude_inbound}
                      {account_cond}
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
                    WHERE sender_address = ANY(%(user_emails)s)
                      AND r.addr != ALL(%(user_emails)s)
                      {period_cond}
                      {exclude_outbound}
                      {account_cond}
                    GROUP BY {outbound_col}
                ) AS combined
                GROUP BY {label}
            """

        sql = f"""
            {base_sql}
            ORDER BY count DESC
            LIMIT %(limit)s OFFSET %(offset)s
        """

        rows = _query_dicts(self._pool, sql, params)
        if not include_total:
            return rows, None
        return rows, _count(self._pool, base_sql, params)

    def accounts(self) -> list[AccountSummary]:
        """Summarize email counts per source_account.

        Sourced from the email_accounts join table, so a message that was
        ingested under multiple accounts counts for each.
        """
        sql = """
            SELECT
                ea.source_account,
                COUNT(DISTINCT ea.email_id) AS email_count,
                MIN(e.date)                 AS first_date,
                MAX(e.date)                 AS last_date,
                COUNT(DISTINCT ea.import_id) AS import_count
            FROM email_accounts ea
            JOIN emails e ON e.id = ea.email_id
            GROUP BY ea.source_account
            ORDER BY email_count DESC
        """
        rows = _query_dicts(self._pool, sql)
        return [
            AccountSummary(
                source_account=row["source_account"],
                email_count=row["email_count"],
                first_date=row["first_date"],
                last_date=row["last_date"],
                import_count=row["import_count"],
            )
            for row in rows
        ]

    def import_history(
        self,
        *,
        account: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ImportRecord]:
        """Return import session records, newest first."""
        conditions: list[str] = []
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if account is not None:
            conditions.append("source_account = %(account)s")
            params["account"] = account
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"""
            SELECT id, source_account, source_file, started_at, completed_at,
                   messages_total, messages_inserted, messages_skipped, status
            FROM imports{where}
            ORDER BY started_at DESC
            LIMIT %(limit)s OFFSET %(offset)s
        """
        rows = _query_dicts(self._pool, sql, params)
        return [
            ImportRecord(
                id=row["id"],
                source_account=row["source_account"],
                source_file=row["source_file"],
                started_at=row["started_at"],
                completed_at=row["completed_at"],
                messages_total=row["messages_total"],
                messages_inserted=row["messages_inserted"],
                messages_skipped=row["messages_skipped"],
                status=row["status"],
            )
            for row in rows
        ]

    def topics_with(
        self,
        *,
        sender: str | None = None,
        sender_domain: str | None = None,
        account: str | None = None,
        limit: int = 5,
        offset: int = 0,
    ) -> tuple[list[Email], int]:
        """Representative emails spanning different topics with a contact.

        Uses greedy farthest-point selection on embeddings. The candidate pool is
        capped (LIMIT 500); ``total`` is a lower bound (``offset + returned``),
        consistent with ``search`` / ``search_attachments``, not a corpus count.

        Args:
            sender: Exact sender email address.
            sender_domain: Sender domain to match.
            account: Scope to a single source_account. Omit to query across all accounts.
            limit: Max results to return.
            offset: Skip first N results.
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

        account_conditions, account_params = self._build_filters(account=account)
        conditions.extend(account_conditions)
        params.update(account_params)

        where = " AND ".join(conditions)
        sql = f"SELECT {SELECT_COLS} FROM emails WHERE {where} ORDER BY date DESC NULLS LAST, id LIMIT 500"

        rows = _query_dicts(self._pool, sql, params)
        if not rows:
            return [], 0

        emails = [Email.from_row(row) for row in rows]
        selected = self._farthest_point_select(emails, limit + offset)
        page = selected[offset:]
        total = offset + len(page)
        return page, total

    @staticmethod
    def _farthest_point_select(emails: list[Email], limit: int) -> list[Email]:
        """Greedy farthest-point selection on embeddings for diverse topic extraction."""
        if len(emails) <= limit:
            return emails

        first_embedding = next((email.embedding for email in emails if email.embedding), None)
        if first_embedding is None:
            return emails[:1]

        vectors = np.zeros((len(emails), len(first_embedding)), dtype=np.float32)
        has_embedding = np.zeros(len(emails), dtype=bool)
        for i, email in enumerate(emails):
            if email.embedding is None:
                continue
            vectors[i] = np.asarray(email.embedding, dtype=np.float32)
            has_embedding[i] = True

        norms = np.linalg.norm(vectors, axis=1)
        positive_norm = norms > 0
        normalized = np.zeros_like(vectors)
        np.divide(vectors, norms[:, None], out=normalized, where=positive_norm[:, None])

        def distances_to(selected_idx: int) -> np.ndarray:
            if not positive_norm[selected_idx]:
                distances = np.zeros(len(emails), dtype=np.float32)
                distances[~has_embedding] = -np.inf
                return distances
            distances = np.asarray(1.0 - normalized @ normalized[selected_idx], dtype=np.float32)
            distances[~positive_norm] = 0.0
            distances[~has_embedding] = -np.inf
            return distances

        selected_indices = [0]
        selected_mask = np.zeros(len(emails), dtype=bool)
        selected_mask[0] = True
        min_distances = distances_to(0)
        min_distances[selected_mask] = -np.inf

        while len(selected_indices) < limit:
            best_idx = int(np.argmax(min_distances))
            if np.isneginf(min_distances[best_idx]):
                break
            selected_indices.append(best_idx)
            selected_mask[best_idx] = True
            min_distances = np.minimum(min_distances, distances_to(best_idx))
            min_distances[selected_mask] = -np.inf

        return [emails[i] for i in selected_indices]

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
                ORDER BY date DESC NULLS LAST, id
            """
            rows = _query_dicts(self._pool, sql, params)
        else:
            where_sql, params = build_where_clause(where, source="emails")  # type: ignore[arg-type]
            sql = f"""
                SELECT {SELECT_COLS} FROM emails
                WHERE {where_sql} AND embedding IS NOT NULL
                ORDER BY date DESC NULLS LAST, id LIMIT 500
            """
            rows = _query_dicts(self._pool, sql, params)

        total = len(rows)
        emails = [Email.from_row(row) for row in rows]
        if not emails:
            return [], 0
        selected = self._farthest_point_select(emails, limit + offset)
        return selected[offset:], total

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
        account: str | None = None,
        include_total: bool = False,
    ) -> tuple[list[Email], int | None]:
        """Messages with no reply in the same thread.

        Returns (emails, total) where total is None unless include_total=True;
        when present it is the exact count of matching rows regardless of offset.

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
            max_to: max number of To recipients (e.g. 1 for direct messages).
            max_cc: max number of CC recipients (e.g. 0 for no-CC messages).
            max_recipients: max total recipients across To + CC + BCC.
            direct_only: shorthand for max_to=1, max_cc=0 (cannot combine with max_to/max_cc).
            account: Scope to a single source_account. When provided,
                "you" = that account. When omitted, "you" = any configured user_emails.
            include_total: When True, compute an exact total via a separate count query.
        """
        if direction not in ("inbound", "outbound"):
            msg = f"Invalid direction: {direction!r}. Must be 'inbound' or 'outbound'."
            raise ValueError(msg)

        if direction == "inbound" and recipient is not None:
            raise ValueError("'recipient' is only valid for direction='outbound'")
        if direction == "outbound" and (sender is not None or sender_domain is not None):
            raise ValueError("'sender'/'sender_domain' are only valid for direction='inbound'")

        rc_conditions, rc_params = self._build_filters(
            max_to=max_to,
            max_cc=max_cc,
            max_recipients=max_recipients,
            direct_only=direct_only,
        )

        identities = self._identity_addresses(account)
        params: dict[str, Any] = {"user_emails": identities}
        params.update(rc_params)

        select_cols_aliased = """
            e.id, e.message_id, e.thread_id, e.subject, e.sender_name, e.sender_address,
            e.sender_domain, e.recipients, e.date, e.body_text, e.has_attachment,
            e.attachments, e.labels, e.in_reply_to, e."references",
            e.source_account, e.import_id, e.created_at
        """

        if direction == "inbound":
            conditions: list[str] = [
                "e.sender_address != ALL(%(user_emails)s)",
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
            if account is not None:
                conditions.append(
                    "EXISTS (SELECT 1 FROM email_accounts ea "
                    "WHERE ea.email_id = e.id AND ea.source_account = %(account)s)"
                )
                params["account"] = account

            conditions.extend(rc_conditions)
            where = " AND ".join(conditions)
            params["limit"] = limit
            params["offset"] = offset
            base_sql = f"""
                SELECT {select_cols_aliased}
                FROM emails e
                WHERE {where}
                  AND NOT EXISTS (
                      SELECT 1 FROM emails reply
                      WHERE reply.thread_id = e.thread_id
                        AND reply.sender_address = ANY(%(user_emails)s)
                        AND reply.date > e.date
                  )
            """
        else:
            # Outbound: messages FROM user where recipients never replied
            conditions = [
                "e.sender_address = ANY(%(user_emails)s)",
                "e.date IS NOT NULL",
            ]
            if after:
                conditions.append("e.date >= %(after)s")
                params["after"] = after
            if before:
                conditions.append("e.date < %(before)s")
                params["before"] = before
            if account is not None:
                conditions.append(
                    "EXISTS (SELECT 1 FROM email_accounts ea "
                    "WHERE ea.email_id = e.id AND ea.source_account = %(account)s)"
                )
                params["account"] = account

            if recipient:
                conditions.append(
                    "(e.recipients @> jsonb_build_object('to', %(recipient_arr)s::jsonb)"
                    " OR e.recipients @> jsonb_build_object('cc', %(recipient_arr)s::jsonb)"
                    " OR e.recipients @> jsonb_build_object('bcc', %(recipient_arr)s::jsonb))"
                )
                params["recipient_arr"] = json.dumps([recipient])
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
                          AND reply.sender_address != ALL(%(user_emails)s)
                          AND reply.date > e.date
                    )
                """

            conditions.extend(rc_conditions)
            where = " AND ".join(conditions)
            params["limit"] = limit
            params["offset"] = offset
            base_sql = f"""
                SELECT {select_cols_aliased}
                FROM emails e
                WHERE {where}
                  {not_exists}
            """

        sql = f"""
            {base_sql}
            ORDER BY e.date DESC NULLS LAST, e.id
            LIMIT %(limit)s OFFSET %(offset)s
        """

        rows = _query_dicts(self._pool, sql, params)
        results = [Email.from_row(row) for row in rows]
        if not include_total:
            return results, None
        return results, _count(self._pool, base_sql, params)

    def correspondence(
        self,
        *,
        address: str,
        after: str | None = None,
        before: str | None = None,
        account: str | None = None,
        limit: int = 500,
        offset: int = 0,
        order: str = "date ASC",
        include_total: bool = False,
    ) -> tuple[list[Email], int | None]:
        """All emails exchanged with a specific person.

        Returns emails where address is sender OR is in recipients (to/cc/bcc).
        Default chronological order, higher limit than find().
        Returns (emails, total) where total is None unless include_total=True;
        when present it is the exact count of matching rows regardless of offset.

        Args:
            address: Email address to match as sender or recipient.
            after: Only include emails on or after this date.
            before: Only include emails before this date.
            account: Scope to a single source_account. Omit to query across all accounts.
            limit: Max results to return.
            offset: Skip first N results.
            order: Result ordering.
            include_total: When True, compute an exact total via a separate count query.
        """
        order_sql = _order_clause(order)

        conditions: list[str] = [
            "(sender_address = %(address)s "
            "OR recipients @> jsonb_build_object('to', %(address_arr)s::jsonb) "
            "OR recipients @> jsonb_build_object('cc', %(address_arr)s::jsonb) "
            "OR recipients @> jsonb_build_object('bcc', %(address_arr)s::jsonb))"
        ]
        params: dict[str, Any] = {
            "address": address,
            "address_arr": json.dumps([address]),
            "limit": limit,
            "offset": offset,
        }

        if after:
            conditions.append("date >= %(after)s")
            params["after"] = after
        if before:
            conditions.append("date < %(before)s")
            params["before"] = before

        account_conditions, account_params = self._build_filters(account=account)
        conditions.extend(account_conditions)
        params.update(account_params)

        where = " AND ".join(conditions)
        sql = (
            f"SELECT {LIST_COLS} FROM emails WHERE {where} "
            f"ORDER BY {order_sql} LIMIT %(limit)s OFFSET %(offset)s"
        )

        rows = _query_dicts(self._pool, sql, params)
        results = [Email.from_row(row) for row in rows]
        if not include_total:
            return results, None
        return results, _count(self._pool, f"SELECT 1 FROM emails WHERE {where}", params)

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
        include_total: bool = False,
    ) -> tuple[list[Email], int | None]:
        """Case-insensitive keyword search in body_text and subject.

        Unlike search(), uses ILIKE (substring match) and does not require Ollama.
        Returns (emails, total) where total is None unless include_total=True;
        when present it is the exact count of matching rows regardless of offset.
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
            conditions.append(
                "EXISTS (SELECT 1 FROM email_accounts ea "
                "WHERE ea.email_id = emails.id AND ea.source_account = %(account)s)"
            )
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
        sql = (
            f"SELECT {LIST_COLS} FROM emails WHERE {where} "
            f"ORDER BY date DESC NULLS LAST, id LIMIT %(limit)s OFFSET %(offset)s"
        )
        rows = _query_dicts(self._pool, sql, params)
        results = [Email.from_row(row) for row in rows]
        if not include_total:
            return results, None
        return results, _count(self._pool, f"SELECT 1 FROM emails WHERE {where}", params)

    def search_attachments(
        self,
        query: str,
        *,
        sender: str | None = None,
        sender_domain: str | None = None,
        recipient: str | None = None,
        after: str | None = None,
        before: str | None = None,
        labels: list[str] | None = None,
        max_to: int | None = None,
        max_cc: int | None = None,
        max_recipients: int | None = None,
        direct_only: bool = False,
        account: str | None = None,
        content_type: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[AttachmentSearchResult], int]:
        """Semantic search over attachment chunk embeddings.

        Returns total as an approximate count of results seen so far
        (offset + rows returned), not an exact match count.
        """
        query_embedding = self._embedding_client.embed(query)

        conditions, params = self._build_filters(
            sender=sender,
            sender_domain=sender_domain,
            recipient=recipient,
            after=after,
            before=before,
            labels=labels,
            max_to=max_to,
            max_cc=max_cc,
            max_recipients=max_recipients,
            direct_only=direct_only,
            account=account,
            column_prefix="e.",
        )

        email_exists = ""
        if conditions:
            email_exists = (
                " AND EXISTS (SELECT 1 FROM email_attachments ea "
                "JOIN emails e ON e.id = ea.email_id "
                f"WHERE ea.attachment_id = ac.attachment_id AND {' AND '.join(conditions)})"
            )

        params["query_embedding"] = str(query_embedding)
        params["limit"] = limit
        params["offset"] = offset

        ct_clause = ""
        if content_type is not None:
            ct_clause = " AND a.content_type = %(content_type)s"
            params["content_type"] = content_type

        sql = f"""
            SELECT ac.id AS chunk_id, ac.attachment_id, ac.chunk_index,
                   ac.heading_path, ac.page_number, ac.token_count, ac.text,
                   a.filename, a.content_type, a.sha256,
                   1 - (ac.embedding <=> %(query_embedding)s::vector) AS similarity,
                   (SELECT COALESCE(array_agg(e2.message_id), ARRAY[]::text[])
                      FROM email_attachments ea2
                      JOIN emails e2 ON e2.id = ea2.email_id
                     WHERE ea2.attachment_id = ac.attachment_id) AS email_message_ids
            FROM attachment_chunks ac
            JOIN attachments a ON a.id = ac.attachment_id
            JOIN attachment_contents co
              ON co.attachment_id = ac.attachment_id AND co.status = 'extracted'
            WHERE ac.embedding IS NOT NULL
              AND vector_norm(ac.embedding) > 0
              {ct_clause}
              {email_exists}
            ORDER BY ac.embedding <=> %(query_embedding)s::vector
            LIMIT %(limit)s OFFSET %(offset)s
        """

        rows = _query_dicts_with_hnsw_ef_search(
            self._pool,
            sql,
            params,
            ef_search=str(max(40, limit + offset)),
        )
        total = offset + len(rows)
        results = []
        for row in rows:
            chunk = AttachmentChunk(
                id=row["chunk_id"],
                attachment_id=row["attachment_id"],
                chunk_index=row["chunk_index"],
                heading_path=row["heading_path"],
                page_number=row["page_number"],
                token_count=row["token_count"],
                text=row["text"],
            )
            results.append(
                AttachmentSearchResult(
                    attachment_id=row["attachment_id"],
                    filename=row["filename"],
                    content_type=row["content_type"],
                    sha256=row["sha256"],
                    chunk=chunk,
                    emails=list(row["email_message_ids"] or []),
                    similarity=row["similarity"],
                )
            )
        return results, total

    # --- Contacts address book ---

    def contacts_search(
        self,
        *,
        query: str | None = None,
        kind: str | None = None,
        tag: str | None = None,
        min_human_probability: float | None = None,
        limit: int = 20,
        offset: int = 0,
        include_total: bool = False,
        needs_review: bool = False,
    ) -> tuple[list[dict[str, Any]], int | None]:
        """Search the contacts address book.

        Joins contacts ← contact_addresses (aggregated per contact). Excludes
        contacts whose every address is the user. Order is by total message
        volume DESC, then last_seen DESC NULLS LAST, then id — unless
        needs_review is True, in which case only kind='unknown' contacts are
        returned ranked by curation priority (volume x human probability).
        Returns (contacts, total) where total is None unless include_total=True;
        when present it is the exact count of matching rows regardless of offset.
        """
        conditions: list[str] = ["NOT only_user"]
        params: dict[str, Any] = {"limit": limit, "offset": offset}

        if kind is not None:
            conditions.append("kind = %(kind)s")
            params["kind"] = kind
        if needs_review:
            conditions.append("kind = 'unknown'")
        if tag is not None:
            conditions.append("%(tag)s = ANY(tags)")
            params["tag"] = tag
        if min_human_probability is not None:
            conditions.append("human_probability >= %(min_human_probability)s")
            params["min_human_probability"] = min_human_probability
        if query is not None:
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            params["query_pattern"] = f"%{escaped}%"
            conditions.append(
                "("
                "display_name ILIKE %(query_pattern)s ESCAPE '\\' "
                "OR EXISTS ("
                "  SELECT 1 FROM unnest(name_variants) AS nv "
                "  WHERE nv ILIKE %(query_pattern)s ESCAPE '\\'"
                ") "
                "OR EXISTS ("
                "  SELECT 1 FROM unnest(addresses) AS addr "
                "  WHERE addr ILIKE %(query_pattern)s ESCAPE '\\'"
                ")"
                ")"
            )

        where = " AND ".join(conditions)
        base_sql = f"""
            WITH contact_stats AS (
                SELECT
                    c.id,
                    c.display_name,
                    c.kind,
                    c.kind_source,
                    c.tags,
                    c.human_probability,
                    array_agg(DISTINCT ca.address ORDER BY ca.address) AS addresses,
                    coalesce(
                        (
                            SELECT array_agg(DISTINCT v ORDER BY v)
                              FROM contact_addresses ca2
                              LEFT JOIN LATERAL unnest(ca2.name_variants) AS v ON TRUE
                             WHERE ca2.contact_id = c.id AND v IS NOT NULL
                        ),
                        '{{}}'::text[]
                    ) AS name_variants,
                    coalesce(sum(ca.messages_from), 0)::int AS messages_from,
                    coalesce(sum(ca.messages_to), 0)::int AS messages_to,
                    min(ca.first_seen) AS first_seen,
                    max(ca.last_seen) AS last_seen,
                    bool_and(ca.is_user) AS only_user
                  FROM contacts c
                  JOIN contact_addresses ca ON ca.contact_id = c.id
                 GROUP BY c.id
            )
            SELECT
                id, display_name, kind, kind_source, tags, human_probability,
                addresses, name_variants, messages_from, messages_to,
                first_seen, last_seen
              FROM contact_stats
             WHERE {where}
        """
        if needs_review:
            order_by = "(messages_from + messages_to) * coalesce(human_probability, 0.5) DESC, id"
        else:
            order_by = "(messages_from + messages_to) DESC, last_seen DESC NULLS LAST, id"
        sql = f"""
            {base_sql}
             ORDER BY {order_by}
             LIMIT %(limit)s OFFSET %(offset)s
        """
        rows = _query_dicts(self._pool, sql, params)
        results: list[dict[str, Any]] = [self._format_contact_row(row, full=False) for row in rows]
        if not include_total:
            return results, None
        return results, _count(self._pool, base_sql, params)

    def get_contact(
        self,
        *,
        address: str | None = None,
        contact_id: UUID | str | None = None,
    ) -> dict[str, Any] | None:
        """Return a full contact card by address or contact_id.

        Exactly one of address/contact_id is required. Address lookup is
        normalized to lowercase. Returns None if not found.
        """
        if (address is None) == (contact_id is None):
            msg = "Exactly one of address or contact_id is required"
            raise ValueError(msg)

        if address is not None:
            params: dict[str, Any] = {"address": address.lower().strip()}
            id_filter = """
                c.id = (
                    SELECT ca0.contact_id FROM contact_addresses ca0
                     WHERE ca0.address = %(address)s
                )
            """
        else:
            params = {"contact_id": contact_id}
            id_filter = "c.id = %(contact_id)s"

        sql = f"""
            SELECT
                c.id,
                c.display_name,
                c.kind,
                c.kind_source,
                c.tags,
                c.notes,
                c.metadata,
                c.human_probability,
                c.classification_signals,
                c.classified_at,
                array_agg(DISTINCT ca.address ORDER BY ca.address) AS addresses,
                coalesce(
                    (
                        SELECT array_agg(DISTINCT v ORDER BY v)
                          FROM contact_addresses ca2
                          LEFT JOIN LATERAL unnest(ca2.name_variants) AS v ON TRUE
                         WHERE ca2.contact_id = c.id AND v IS NOT NULL
                    ),
                    '{{}}'::text[]
                ) AS name_variants,
                coalesce(sum(ca.messages_from), 0)::int AS messages_from,
                coalesce(sum(ca.messages_to), 0)::int AS messages_to,
                min(ca.first_seen) AS first_seen,
                max(ca.last_seen) AS last_seen
              FROM contacts c
              JOIN contact_addresses ca ON ca.contact_id = c.id
             WHERE {id_filter}
             GROUP BY c.id
        """
        row = _query_one_dict(self._pool, sql, params)
        if row is None:
            return None
        return self._format_contact_row(row, full=True)

    def update_contact(
        self,
        *,
        contact_id: UUID | str,
        kind: str | None = None,
        tags: list[str] | None = None,
        notes: str | None = None,
        display_name: str | None = None,
    ) -> dict[str, Any]:
        """Curation-only write for a single contact.

        Updates only the provided fields. Setting kind also sets
        kind_source='manual'. Raises ValueError if the kind is invalid or the
        contact does not exist.
        """
        if kind is not None and kind not in VALID_CONTACT_KINDS:
            msg = (
                f"Invalid kind {kind!r}. Must be one of: {', '.join(sorted(VALID_CONTACT_KINDS))}"
            )
            raise ValueError(msg)

        sets: list[str] = []
        params: dict[str, Any] = {"contact_id": contact_id}
        if kind is not None:
            sets.append("kind = %(kind)s")
            sets.append("kind_source = 'manual'")
            params["kind"] = kind
        if tags is not None:
            sets.append("tags = %(tags)s")
            params["tags"] = tags
        if notes is not None:
            sets.append("notes = %(notes)s")
            params["notes"] = notes
        if display_name is not None:
            sets.append("display_name = %(display_name)s")
            params["display_name"] = display_name

        if sets:
            sets.append("updated_at = now()")
            sql = f"""
                UPDATE contacts
                   SET {", ".join(sets)}
                 WHERE id = %(contact_id)s
             RETURNING id
            """
            row = _query_one_dict(self._pool, sql, params)
            if row is None:
                msg = f"Contact {contact_id} does not exist"
                raise ValueError(msg)
        else:
            # No fields to update — still verify existence.
            exists = _query_one_dict(
                self._pool,
                "SELECT id FROM contacts WHERE id = %(contact_id)s",
                params,
            )
            if exists is None:
                msg = f"Contact {contact_id} does not exist"
                raise ValueError(msg)

        card = self.get_contact(contact_id=contact_id)
        if card is None:
            msg = f"Contact {contact_id} does not exist"
            raise ValueError(msg)
        return card

    def set_kind_bulk(
        self,
        *,
        kind: str,
        domain: str | None = None,
        address: str | None = None,
        contact_id: UUID | str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Bulk-set kind for contacts matched by exactly one selector.

        Manual curation: sets kind_source='manual'. Returns
        ``{"matched": int, "updated": int, "sample": [...]}`` where sample is
        up to 10 matching contacts with display_name and addresses.
        """
        if kind not in VALID_CONTACT_KINDS:
            msg = (
                f"Invalid kind {kind!r}. Must be one of: {', '.join(sorted(VALID_CONTACT_KINDS))}"
            )
            raise ValueError(msg)

        n_selectors = sum(x is not None for x in (domain, address, contact_id))
        if n_selectors != 1:
            msg = "Exactly one of domain, address, or contact_id is required"
            raise ValueError(msg)

        params: dict[str, Any] = {"kind": kind}
        if domain is not None:
            # Full LIKE pattern in the param so pyformat does not treat %@ as a placeholder.
            params["domain_pattern"] = f"%@{domain.lower().strip()}"
            match_sql = """
                SELECT DISTINCT ca.contact_id AS id
                  FROM contact_addresses ca
                 WHERE lower(ca.address) LIKE %(domain_pattern)s
            """
        elif address is not None:
            params["address"] = address.lower().strip()
            match_sql = """
                SELECT ca.contact_id AS id
                  FROM contact_addresses ca
                 WHERE ca.address = %(address)s
            """
        else:
            params["contact_id"] = contact_id
            match_sql = """
                SELECT c.id FROM contacts c WHERE c.id = %(contact_id)s
            """

        sample_sql = f"""
            SELECT
                c.display_name,
                array_agg(DISTINCT ca.address ORDER BY ca.address) AS addresses
              FROM contacts c
              JOIN ({match_sql}) AS matched ON matched.id = c.id
              JOIN contact_addresses ca ON ca.contact_id = c.id
             GROUP BY c.id, c.display_name
             ORDER BY c.id
             LIMIT 10
        """
        sample_rows = _query_dicts(self._pool, sample_sql, params)
        sample: list[dict[str, Any]] = [
            {
                "display_name": row.get("display_name"),
                "addresses": list(row["addresses"]) if row.get("addresses") is not None else [],
            }
            for row in sample_rows
        ]

        count_sql = f"SELECT count(*)::int AS n FROM ({match_sql}) AS matched"
        count_row = _query_one_dict(self._pool, count_sql, params)
        matched = int(count_row["n"]) if count_row is not None else 0

        if dry_run:
            return {"matched": matched, "updated": 0, "sample": sample}

        update_sql = f"""
            UPDATE contacts AS c
               SET kind = %(kind)s,
                   kind_source = 'manual',
                   updated_at = now()
              FROM ({match_sql}) AS matched
             WHERE c.id = matched.id
         RETURNING c.id
        """
        updated_rows = _query_dicts(self._pool, update_sql, params)
        return {"matched": matched, "updated": len(updated_rows), "sample": sample}

    @staticmethod
    def _format_contact_row(row: dict[str, Any], *, full: bool) -> dict[str, Any]:
        """Normalize a contact row to the public dict shape."""
        out: dict[str, Any] = {
            "id": str(row["id"]) if row.get("id") is not None else None,
            "display_name": row.get("display_name"),
            "kind": row.get("kind"),
            "kind_source": row.get("kind_source"),
            "tags": list(row["tags"]) if row.get("tags") is not None else [],
            "human_probability": row.get("human_probability"),
            "addresses": list(row["addresses"]) if row.get("addresses") is not None else [],
            "name_variants": (
                list(row["name_variants"]) if row.get("name_variants") is not None else []
            ),
            "messages_from": row.get("messages_from") or 0,
            "messages_to": row.get("messages_to") or 0,
            "first_seen": row.get("first_seen"),
            "last_seen": row.get("last_seen"),
        }
        if full:
            out["notes"] = row.get("notes")
            out["metadata"] = row.get("metadata") if row.get("metadata") is not None else {}
            out["classification_signals"] = row.get("classification_signals")
            out["classified_at"] = row.get("classified_at")
        return out

    def get_attachment_markdown(
        self, attachment_id: int, *, account: str | None = None
    ) -> str | None:
        """Return the full extracted markdown for an attachment, or None if
        extraction is pending, failed, or the row doesn't exist.

        When `account` is given, also require the attachment to be referenced
        by at least one email attributed to that account.
        """
        sql = (
            "SELECT c.markdown FROM attachment_contents c "
            "WHERE c.attachment_id = %(id)s AND c.status = 'extracted'"
        )
        params: dict[str, Any] = {"id": attachment_id}
        if account is not None:
            sql += (
                " AND EXISTS (SELECT 1 FROM email_attachments ea "
                "JOIN email_accounts eacc ON eacc.email_id = ea.email_id "
                "WHERE ea.attachment_id = c.attachment_id "
                "AND eacc.source_account = %(account)s)"
            )
            params["account"] = account
        row = _query_one_dict(self._pool, sql, params)
        return row["markdown"] if row else None

    def search_all(
        self,
        query: str,
        *,
        sender: str | None = None,
        sender_domain: str | None = None,
        recipient: str | None = None,
        after: str | None = None,
        before: str | None = None,
        labels: list[str] | None = None,
        max_to: int | None = None,
        max_cc: int | None = None,
        max_recipients: int | None = None,
        direct_only: bool = False,
        account: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[UnifiedSearchResult], int]:
        """Run both email and attachment searches; merge by rank fusion (RRF).

        Results are rank-fused across sources (reciprocal rank fusion, K=60) so
        neither corpus can crowd out the other by score-distribution differences.
        Per-result ``similarity`` values remain raw cosine similarities and are
        NOT comparable across sources. ``total`` is a lower bound
        (``offset + returned``), consistent with ``search`` /
        ``search_attachments``, not a corpus count across both corpora.
        """
        over_fetch = 2 * (limit + offset)
        email_hits, _ = self.search(
            query,
            sender=sender,
            sender_domain=sender_domain,
            recipient=recipient,
            after=after,
            before=before,
            labels=labels,
            max_to=max_to,
            max_cc=max_cc,
            max_recipients=max_recipients,
            direct_only=direct_only,
            account=account,
            limit=over_fetch,
            offset=0,
        )
        attachment_hits, _ = self.search_attachments(
            query,
            sender=sender,
            sender_domain=sender_domain,
            recipient=recipient,
            after=after,
            before=before,
            labels=labels,
            max_to=max_to,
            max_cc=max_cc,
            max_recipients=max_recipients,
            direct_only=direct_only,
            account=account,
            limit=over_fetch,
            offset=0,
        )

        # Build (sort_key, result) tuples; own_rank is 1-based within each source.
        # Sort key: (-rrf, -similarity, source, stable_id) for deterministic order.
        ranked: list[tuple[tuple[float, float, str, str | int], UnifiedSearchResult]] = []
        for own_rank, h in enumerate(email_hits, start=1):
            result = UnifiedSearchResult(
                source="email",
                similarity=h.similarity,
                email=h.email,
                attachment_result=None,
            )
            sort_key: tuple[float, float, str, str | int] = (
                -1.0 / (RRF_K + own_rank),
                -h.similarity,
                "email",
                h.email.message_id,
            )
            ranked.append((sort_key, result))
        for own_rank, a in enumerate(attachment_hits, start=1):
            result = UnifiedSearchResult(
                source="attachment",
                similarity=a.similarity,
                email=None,
                attachment_result=a,
            )
            sort_key = (
                -1.0 / (RRF_K + own_rank),
                -a.similarity,
                "attachment",
                a.chunk.id,
            )
            ranked.append((sort_key, result))

        ranked.sort(key=lambda item: item[0])
        unified = [item[1] for item in ranked]
        page = unified[offset : offset + limit]
        total = offset + len(page)
        return page, total

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
        account: str | None = None,
        include_total: bool = False,
    ) -> tuple[list[dict[str, Any]], int | None]:
        """Threads exceeding a message count threshold.

        Returns (threads, total) where total is None unless include_total=True;
        when present it is the exact count of matching rows regardless of offset.
        participant: only threads where this address appears as sender.
        account: scope to a single source_account.
        """
        conditions: list[str] = []
        params: dict[str, Any] = {"min_messages": min_messages, "limit": limit, "offset": offset}
        if after:
            conditions.append("date >= %(after)s")
            params["after"] = after
        if account is not None:
            conditions.append(
                "EXISTS (SELECT 1 FROM email_accounts ea "
                "WHERE ea.email_id = emails.id AND ea.source_account = %(account)s)"
            )
            params["account"] = account
        where = " AND ".join(conditions) if conditions else "TRUE"
        having_participant = ""
        if participant:
            having_participant = "AND %(participant)s = ANY(array_agg(sender_address))"
            params["participant"] = participant
        base_sql = f"""
            SELECT thread_id, count(*) AS message_count,
                   min(date) AS first_date, max(date) AS last_date,
                   array_agg(DISTINCT sender_address) AS participants
            FROM emails WHERE {where}
            GROUP BY thread_id
            HAVING count(*) >= %(min_messages)s {having_participant}
        """
        sql = f"""
            {base_sql}
            ORDER BY count(*) DESC
            LIMIT %(limit)s OFFSET %(offset)s
        """
        rows = _query_dicts(self._pool, sql, params)
        if not include_total:
            return rows, None
        return rows, _count(self._pool, base_sql, params)
