"""Repair thread ids after parsing.

In-Reply-To-only clients can make each reply point at its direct parent instead
of the thread root, fragmenting conversations across thread_id values. Missing
archive messages still carry identity through reply headers, so absent
intermediates must participate in the connectivity graph.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, cast

import structlog

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

logger = structlog.get_logger()

type ThreadRow = tuple[str, str, str | None, list[str] | None, datetime | None]


class _UnionFind:
    def __init__(self) -> None:
        self._index: dict[str, int] = {}
        self._parent: list[int] = []
        self._size: list[int] = []

    def add(self, key: str) -> int:
        idx = self._index.get(key)
        if idx is not None:
            return idx
        idx = len(self._parent)
        self._index[key] = idx
        self._parent.append(idx)
        self._size.append(1)
        return idx

    def find(self, idx: int) -> int:
        root = idx
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[idx] != idx:
            next_idx = self._parent[idx]
            self._parent[idx] = root
            idx = next_idx
        return root

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if self._size[left_root] < self._size[right_root]:
            left_root, right_root = right_root, left_root
        self._parent[right_root] = left_root
        self._size[left_root] += self._size[right_root]


def _load_rows(pool: ConnectionPool) -> list[ThreadRow]:
    with pool.connection() as conn:
        cur = conn.execute(
            'SELECT message_id, thread_id, in_reply_to, "references", date FROM emails'
        )
        return cast("list[ThreadRow]", cur.fetchall())


def _is_older(
    date: datetime | None,
    message_id: str,
    best_date: datetime | None,
    best_message_id: str,
) -> bool:
    if date is None and best_date is None:
        return message_id < best_message_id
    if date is None:
        return False
    if best_date is None:
        return True
    if date == best_date:
        return message_id < best_message_id
    return date < best_date


def _compute_updates(rows: list[ThreadRow]) -> tuple[list[tuple[str, str]], int]:
    uf = _UnionFind()

    for message_id, _thread_id, in_reply_to, references, _date in rows:
        message_node = uf.add(message_id)
        if in_reply_to is not None:
            uf.union(message_node, uf.add(in_reply_to))
        for reference in references or []:
            uf.union(message_node, uf.add(reference))

    component_roots: dict[int, tuple[datetime | None, str]] = {}
    for message_id, _thread_id, _in_reply_to, _references, date in rows:
        component = uf.find(uf.add(message_id))
        best = component_roots.get(component)
        if best is None or _is_older(date, message_id, best[0], best[1]):
            component_roots[component] = (date, message_id)

    updates: list[tuple[str, str]] = []
    for message_id, thread_id, _in_reply_to, _references, _date in rows:
        component = uf.find(uf.add(message_id))
        new_thread_id = component_roots[component][1]
        if thread_id != new_thread_id:
            updates.append((message_id, new_thread_id))

    return updates, len(component_roots)


def _apply_updates(pool: ConnectionPool, updates: list[tuple[str, str]]) -> int:
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(
            """CREATE TEMP TABLE thread_id_repair_updates (
                   message_id text PRIMARY KEY,
                   new_thread_id text NOT NULL
               ) ON COMMIT DROP"""
        )
        if updates:
            cur.executemany(
                """INSERT INTO thread_id_repair_updates (message_id, new_thread_id)
                   VALUES (%s, %s)""",
                updates,
            )
        cur.execute(
            """UPDATE emails e
               SET thread_id = t.new_thread_id
               FROM thread_id_repair_updates t
               WHERE e.message_id = t.message_id"""
        )
        updated = max(cur.rowcount, 0)
        conn.commit()
        return updated


def repair_thread_ids(pool: ConnectionPool) -> int:
    """Rewrite thread_id to the connected-component root. Returns rows updated."""
    rows = _load_rows(pool)
    logger.info("thread_repair_start", rows=len(rows))
    updates, components = _compute_updates(rows)
    updated = _apply_updates(pool, updates)
    logger.info("thread_repair_complete", updated=updated, components=components)
    return updated
