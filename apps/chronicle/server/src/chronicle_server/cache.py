# src/chronicle_server/cache.py
"""Postgres-backed response cache keyed by data version (§16.1).

Hot aggregate endpoints (archive summary, chronicle buckets/compare, search
facets) store whole-response or facet JSON under a namespaced key. Invalidation
is by construction: a cache hit requires an exact ``data_version`` match with
the current marker, so a changed archive or derived table never serves stale
payloads.

``data_version`` marker components (document which matter per endpoint):

- **emails** — ``count(*)`` + ``max(created_at)`` of ``emails``.
  Matters for: archive/summary, chronicle buckets/compare (message-ish lanes),
  search facets, topics list base.
- **app_topics** — ``max(updated_at)``. Matters for: topics lane / topics list;
  included so derived curation busts derived-content caches.
- **app_events** — ``max(updated_at)``. Matters for: events lane / topics list
  derived marker; same bust rationale.

ETag helpers import :func:`data_version` / :func:`emails_data_version` from here
so the marker is computed in one place.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from psycopg.types.json import Jsonb

if TYPE_CHECKING:
    from fastapi import Request
    from psycopg_pool import ConnectionPool

CACHE_MAX_ROWS = 500


def _iso_ts(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()  # type: ignore[no-any-return]
    return str(value)


def cache_key(namespace: str, payload: Any) -> str:
    """Namespaced sha256 of canonical request-identifying JSON.

    Example: ``\"buckets:\" + sha256(canonical request json)``.
    """
    material = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"{namespace}:{digest}"


def emails_data_version(pool: ConnectionPool, request: Request | None = None) -> str:
    """Cheap emails-only data-version marker: ``count(*)`` + ``max(created_at)``.

    Cached per-request on ``request.state`` so multiple ETag/cache checks in one
    handler only pay for one statement.
    """
    if request is not None:
        cached_ver = getattr(request.state, "emails_data_version", None)
        if isinstance(cached_ver, str):
            return cached_ver

    with pool.connection() as conn:
        row = conn.execute("SELECT count(*)::bigint, max(created_at) FROM emails").fetchone()

    count = int(row[0]) if row and row[0] is not None else 0
    max_created = _iso_ts(row[1] if row is not None else None)
    marker = f"{count}:{max_created}"

    if request is not None:
        request.state.emails_data_version = marker
    return marker


def data_version(pool: ConnectionPool, request: Request | None = None) -> str:
    """Full data-version marker for response cache and ETags.

    Combines the emails marker with ``max(updated_at)`` of ``app_topics`` and
    ``app_events`` so derived-table edits bust caches even when the raw email
    archive is unchanged. See module docstring for per-endpoint components.
    """
    if request is not None:
        cached_ver = getattr(request.state, "data_version", None)
        if isinstance(cached_ver, str):
            return cached_ver
        # Back-compat alias used by topics list ETag path.
        cached_topics = getattr(request.state, "topics_data_version", None)
        if isinstance(cached_topics, str):
            return cached_topics

    base = emails_data_version(pool, request)
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT
                (SELECT max(updated_at) FROM app_topics),
                (SELECT max(updated_at) FROM app_events)
            """
        ).fetchone()

    topics_max = _iso_ts(row[0] if row is not None else None)
    events_max = _iso_ts(row[1] if row is not None else None)
    marker = f"{base}|topics:{topics_max}|events:{events_max}"

    if request is not None:
        request.state.data_version = marker
        request.state.topics_data_version = marker
    return marker


def topics_data_version(pool: ConnectionPool, request: Request | None = None) -> str:
    """Emails + app_topics/app_events marker (alias of :func:`data_version`)."""
    return data_version(pool, request)


def cached(
    pool: ConnectionPool,
    *,
    key: str,
    data_version: str,
    compute: Callable[[], dict[str, Any]],
) -> dict[str, Any]:
    """Read-through cache: hit when key exists and ``data_version`` matches.

    On miss (or version mismatch), call *compute*, UPSERT the row, enforce the
    size bound (keep newest :data:`CACHE_MAX_ROWS` by ``created_at``), return.
    """
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT data_version, value FROM app_cache WHERE key = %(key)s",
            {"key": key},
        ).fetchone()
        if row is not None and row[0] == data_version:
            value = row[1]
            if isinstance(value, dict):
                return value
            if value is not None:
                return dict(value)

    # Compute outside the read connection so lane/summary SQL can borrow freely.
    result = compute()

    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO app_cache (key, data_version, value)
            VALUES (%(key)s, %(data_version)s, %(value)s)
            ON CONFLICT (key) DO UPDATE
               SET data_version = EXCLUDED.data_version,
                   value = EXCLUDED.value,
                   created_at = now()
            """,
            {
                "key": key,
                "data_version": data_version,
                "value": Jsonb(result),
            },
        )
        # Size bound: keep the newest CACHE_MAX_ROWS rows (one statement).
        conn.execute(
            """
            DELETE FROM app_cache
             WHERE key NOT IN (
                SELECT key FROM (
                    SELECT key FROM app_cache
                     ORDER BY created_at DESC
                     LIMIT %(limit)s
                ) keepers
             )
            """,
            {"limit": CACHE_MAX_ROWS},
        )
        conn.commit()
    return result


def cache_row_count(pool: ConnectionPool) -> int:
    """Number of rows currently in ``app_cache`` (health observability)."""
    with pool.connection() as conn:
        row = conn.execute("SELECT count(*)::int FROM app_cache").fetchone()
    return int(row[0]) if row and row[0] is not None else 0
