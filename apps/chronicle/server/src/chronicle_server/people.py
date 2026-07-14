"""People & Organizations: thin authenticated proxies over MailDB contacts.

Identity resolution, archive span, activity, topics, and merge/unmerge curation
all flow through the existing contacts subsystem — no duplicated resolution logic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from chronicle_server.auth import require_user
from chronicle_server.cursor import decode_cursor, encode_cursor
from chronicle_server.db import audit
from chronicle_server.ids import encode_source_id

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

logger = structlog.get_logger()

router = APIRouter(tags=["people"])

_LIST_DEFAULT_LIMIT = 50
_LIST_MAX_LIMIT = 500
_CANDIDATES_DEFAULT_LIMIT = 20
_CANDIDATES_MAX_LIMIT = 100
_TOP_TOPICS = 5
_GRAPH_DEFAULT_MAX_NODES = 25
_GRAPH_HARD_MAX_NODES = 50
_GRAPH_EVIDENCE_THREAD_CAP = 20

ContactKind = Literal["human", "organization", "automated", "mailing_list", "unknown"]
VALID_KINDS: frozenset[str] = frozenset(
    {"human", "organization", "automated", "mailing_list", "unknown"}
)


# --- request models ---


class ContactPatch(BaseModel):
    kind: ContactKind | None = None
    tags: list[str] | None = None
    notes: str | None = None
    display_name: str | None = None


class MergeRequest(BaseModel):
    source_id: str = Field(min_length=1)
    target_id: str = Field(min_length=1)


class UnmergeRequest(BaseModel):
    merge_id: str = Field(min_length=1)


# --- helpers ---


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def _serialize_contact(card: dict[str, Any]) -> dict[str, Any]:
    """Normalize a MailDB contact card for JSON (ISO datetimes, stable ids)."""
    out = dict(card)
    for key in ("first_seen", "last_seen", "classified_at"):
        if key in out:
            out[key] = _iso(out[key])
    if "id" in out and out["id"] is not None:
        out["id"] = str(out["id"])
    if "merge_id" in out and out["merge_id"] is not None:
        out["merge_id"] = str(out["merge_id"])
    return out


def _maildb(pool: ConnectionPool) -> Any:
    from maildb import MailDB

    return MailDB._from_pool(pool)


def _clamp_limit(limit: int, *, default: int, maximum: int) -> int:
    if limit < 1:
        return default
    return min(limit, maximum)


def _parse_offset_cursor(cursor: str | None, secret_key: str) -> int:
    if not cursor:
        return 0
    try:
        payload = decode_cursor(cursor, secret_key)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid cursor") from exc
    offset = payload.get("offset", 0)
    if not isinstance(offset, int) or offset < 0:
        raise HTTPException(status_code=422, detail="invalid cursor offset")
    return offset


def _address_rows(pool: ConnectionPool, contact_id: UUID) -> list[dict[str, Any]]:
    """Per-address volumes and owner flag (PE-002)."""
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT address, is_user, messages_from, messages_to, first_seen, last_seen
              FROM contact_addresses
             WHERE contact_id = %(id)s
             ORDER BY (messages_from + messages_to) DESC, address
            """,
            {"id": contact_id},
        ).fetchall()
    return [
        {
            "address": str(r[0]),
            "is_user": bool(r[1]),
            "messages_from": int(r[2] or 0),
            "messages_to": int(r[3] or 0),
            "first_seen": _iso(r[4]),
            "last_seen": _iso(r[5]),
        }
        for r in rows
    ]


def _activity_buckets(pool: ConnectionPool, addresses: list[str]) -> list[dict[str, Any]]:
    """Monthly sent-message buckets for contact addresses (one statement)."""
    if not addresses:
        return []
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT date_trunc('month', e.date) AS bucket, count(*)::int AS count
              FROM emails e
             WHERE lower(e.sender_address) = ANY(%(addrs)s)
               AND e.date IS NOT NULL
             GROUP BY 1
             ORDER BY 1
            """,
            {"addrs": [a.lower() for a in addresses]},
        ).fetchall()
    return [{"bucket": _iso(r[0]), "count": int(r[1])} for r in rows if r[0] is not None]


def _top_topics(pool: ConnectionPool, addresses: list[str]) -> list[dict[str, Any]]:
    """Top topics by authored membership; empty when topics not generated / table missing."""
    if not addresses:
        return []
    try:
        with pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT t.id, t.label, count(*)::int AS cnt
                  FROM app_topic_members m
                  JOIN emails e ON e.id = m.email_id
                  JOIN app_topics t ON t.id = m.topic_id
                 WHERE lower(e.sender_address) = ANY(%(addrs)s)
                   AND t.hidden = FALSE
                 GROUP BY t.id, t.label
                 ORDER BY cnt DESC, t.label ASC
                 LIMIT %(lim)s
                """,
                {"addrs": [a.lower() for a in addresses], "lim": _TOP_TOPICS},
            ).fetchall()
    except Exception as exc:
        logger.debug("people_topics_empty", error=str(exc))
        return []
    return [{"id": str(r[0]), "label": str(r[1]), "count": int(r[2])} for r in rows]


def _thread_count(pool: ConnectionPool, addresses: list[str]) -> int:
    if not addresses:
        return 0
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT count(DISTINCT e.thread_id)::int
              FROM emails e
             WHERE lower(e.sender_address) = ANY(%(addrs)s)
               AND e.thread_id IS NOT NULL
            """,
            {"addrs": [a.lower() for a in addresses]},
        ).fetchone()
    return int(row[0]) if row else 0


def _merge_history(pool: ConnectionPool, contact_id: UUID) -> list[dict[str, Any]]:
    """Merges where this contact is the surviving target (unmerge-able)."""
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT id, source_id, target_id, merged_at
              FROM contact_merges
             WHERE target_id = %(id)s
             ORDER BY merged_at DESC
            """,
            {"id": contact_id},
        ).fetchall()
    return [
        {
            "id": str(r[0]),
            "source_id": str(r[1]),
            "target_id": str(r[2]),
            "merged_at": _iso(r[3]),
        }
        for r in rows
    ]


def _parse_optional_date(raw: str | None, *, field: str) -> datetime | None:
    """Parse optional ISO date or datetime query param to timezone-aware UTC."""
    if raw is None or raw == "":
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        if len(text) == 10 and text[4] == "-" and text[7] == "-":
            dt = datetime(int(text[0:4]), int(text[5:7]), int(text[8:10]), tzinfo=UTC)
        else:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid {field}") from exc
    return dt


def _date_only(value: Any) -> str | None:
    """ISO calendar date (YYYY-MM-DD) for edge first/last fields."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return dt.astimezone(UTC).date().isoformat()
    if hasattr(value, "isoformat"):
        s = str(value.isoformat())
        return s[:10] if len(s) >= 10 else s
    s = str(value)
    return s[:10] if len(s) >= 10 else s


def _ego_graph(
    pool: ConnectionPool,
    *,
    contact_id: UUID,
    ego_label: str,
    ego_kind: str,
    max_nodes: int,
    date_from: datetime | None,
    date_to: datetime | None,
) -> dict[str, Any]:
    """Bounded depth-1 co-participation graph around one contact (PE-003).

    One set-based pipeline: ego threads (sender or recipient) → co-senders →
    identity join → rank/cap. Deeper neighborhoods arrive on demand later.
    """
    ego_id = str(contact_id)
    with pool.connection() as conn:
        rows = conn.execute(
            """
            WITH ego_addrs AS (
                SELECT lower(btrim(address)) AS address
                  FROM contact_addresses
                 WHERE contact_id = %(cid)s
                   AND address IS NOT NULL
                   AND btrim(address) <> ''
            ),
            ego_threads AS (
                SELECT DISTINCT e.thread_id
                  FROM emails e
                 WHERE e.thread_id IS NOT NULL
                   AND (%(date_from)s::timestamptz IS NULL
                        OR e.date >= %(date_from)s::timestamptz)
                   AND (%(date_to)s::timestamptz IS NULL
                        OR e.date <= %(date_to)s::timestamptz)
                   AND (
                        lower(btrim(e.sender_address)) IN (SELECT address FROM ego_addrs)
                        OR EXISTS (
                            SELECT 1
                              FROM ego_addrs a
                             WHERE e.recipients @> jsonb_build_object(
                                       'to', to_jsonb(ARRAY[a.address])
                                   )
                                OR e.recipients @> jsonb_build_object(
                                       'cc', to_jsonb(ARRAY[a.address])
                                   )
                                OR e.recipients @> jsonb_build_object(
                                       'bcc', to_jsonb(ARRAY[a.address])
                                   )
                        )
                   )
            ),
            co_raw AS (
                SELECT
                    CASE
                        WHEN ca.contact_id IS NOT NULL THEN ca.contact_id::text
                        ELSE 'addr:' || lower(btrim(e.sender_address))
                    END AS node_id,
                    CASE
                        WHEN ca.contact_id IS NOT NULL THEN
                            coalesce(
                                nullif(btrim(c.display_name), ''),
                                lower(btrim(e.sender_address))
                            )
                        ELSE lower(btrim(e.sender_address))
                    END AS label,
                    CASE
                        WHEN ca.contact_id IS NOT NULL THEN coalesce(c.kind, 'unknown')
                        ELSE 'address'
                    END AS kind,
                    e.thread_id,
                    e.date
                  FROM emails e
                  JOIN ego_threads t ON t.thread_id = e.thread_id
                  LEFT JOIN contact_addresses ca
                         ON lower(btrim(ca.address)) = lower(btrim(e.sender_address))
                  LEFT JOIN contacts c ON c.id = ca.contact_id
                 WHERE e.sender_address IS NOT NULL
                   AND btrim(e.sender_address) <> ''
                   AND lower(btrim(e.sender_address)) NOT IN (SELECT address FROM ego_addrs)
                   AND (%(date_from)s::timestamptz IS NULL
                        OR e.date >= %(date_from)s::timestamptz)
                   AND (%(date_to)s::timestamptz IS NULL
                        OR e.date <= %(date_to)s::timestamptz)
            ),
            co_threads AS (
                SELECT node_id,
                       thread_id,
                       min(date) AS thread_first,
                       max(date) AS thread_last,
                       (array_agg(label ORDER BY label))[1] AS label,
                       (array_agg(kind ORDER BY kind))[1] AS kind
                  FROM co_raw
                 GROUP BY node_id, thread_id
            ),
            agg AS (
                SELECT
                    node_id,
                    (array_agg(label ORDER BY label))[1] AS label,
                    (array_agg(kind ORDER BY kind))[1] AS kind,
                    count(*)::int AS shared_thread_count,
                    min(thread_first) AS first_shared,
                    max(thread_last) AS last_shared,
                    (array_agg(thread_id ORDER BY thread_id))[1:%(ev_cap)s] AS sample_threads
                  FROM co_threads
                 GROUP BY node_id
            ),
            ranked AS (
                SELECT
                    a.*,
                    count(*) OVER ()::int AS total_coparticipants,
                    row_number() OVER (
                        ORDER BY a.shared_thread_count DESC, a.node_id ASC
                    ) AS rn
                  FROM agg a
            )
            SELECT node_id, label, kind, shared_thread_count,
                   first_shared, last_shared, sample_threads,
                   total_coparticipants
              FROM ranked
             WHERE rn <= %(max_nodes)s
             ORDER BY shared_thread_count DESC, node_id ASC
            """,
            {
                "cid": contact_id,
                "date_from": date_from,
                "date_to": date_to,
                "max_nodes": max_nodes,
                "ev_cap": _GRAPH_EVIDENCE_THREAD_CAP,
            },
        ).fetchall()

    total = int(rows[0][7]) if rows else 0
    truncated = total > max_nodes

    nodes: list[dict[str, Any]] = [
        {
            "id": ego_id,
            "label": ego_label,
            "kind": ego_kind,
            "is_ego": True,
        }
    ]
    edges: list[dict[str, Any]] = []
    for r in rows:
        node_id = str(r[0])
        label = str(r[1] or node_id)
        kind = str(r[2] or "unknown")
        shared = int(r[3] or 0)
        first = _date_only(r[4])
        last = _date_only(r[5])
        sample = r[6] or []
        if not isinstance(sample, (list, tuple)):
            sample = list(sample) if sample else []
        thr_ids = [encode_source_id("thr", str(tid)) for tid in sample if tid is not None][
            :_GRAPH_EVIDENCE_THREAD_CAP
        ]

        nodes.append(
            {
                "id": node_id,
                "label": label,
                "kind": kind,
                "is_ego": False,
            }
        )
        edges.append(
            {
                "source": ego_id,
                "target": node_id,
                "kind": "thread_co_participation",
                "shared_threads": shared,
                "first": first,
                "last": last,
                "evidence": {"thread_ids": thr_ids},
            }
        )

    return {
        "nodes": nodes,
        "edges": edges,
        "truncated": truncated,
        "total_coparticipants": total,
    }


def _enrich_card(pool: ConnectionPool, card: dict[str, Any]) -> dict[str, Any]:
    """Full profile: card + address classes, activity, topics, merges."""
    contact_id = UUID(str(card["id"]))
    addresses = list(card.get("addresses") or [])
    addr_rows = _address_rows(pool, contact_id)
    address_classes: dict[str, str] = {
        r["address"]: ("owner" if r["is_user"] else "external") for r in addr_rows
    }
    # Fallback for addresses present on the card but missing detail rows.
    for addr in addresses:
        if addr not in address_classes:
            address_classes[addr] = "external"

    out = _serialize_contact(card)
    out["address_classes"] = address_classes
    out["address_details"] = addr_rows
    out["activity"] = _activity_buckets(pool, addresses)
    out["topics"] = _top_topics(pool, addresses)
    out["thread_count"] = _thread_count(pool, addresses)
    merges = _merge_history(pool, contact_id)
    if merges:
        out["merges"] = merges
    return out


# --- endpoints ---


@router.get("/people")
def people_list(
    request: Request,
    q: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    needs_review: bool = Query(default=False),
    limit: int = Query(default=_LIST_DEFAULT_LIMIT, ge=1, le=_LIST_MAX_LIMIT),
    cursor: str | None = Query(default=None),
    _user: str = Depends(require_user),
) -> dict[str, Any]:
    """Search contacts (include_total on first page). Offset cursor, limit ≤ 500."""
    if kind is not None and kind not in VALID_KINDS:
        raise HTTPException(
            status_code=422,
            detail=f"kind must be one of: {', '.join(sorted(VALID_KINDS))}",
        )
    pool: ConnectionPool = request.app.state.pool
    secret_key: str = request.app.state.settings.secret_key
    offset = _parse_offset_cursor(cursor, secret_key)
    lim = _clamp_limit(limit, default=_LIST_DEFAULT_LIMIT, maximum=_LIST_MAX_LIMIT)

    db = _maildb(pool)
    include_total = offset == 0
    results, total = db.contacts_search(
        query=q if q else None,
        kind=kind,
        needs_review=needs_review,
        limit=lim,
        offset=offset,
        include_total=include_total,
    )
    items = [_serialize_contact(r) for r in results]
    next_cursor: str | None = None
    if len(items) >= lim:
        next_cursor = encode_cursor({"offset": offset + lim}, secret_key)

    return {
        "items": items,
        "total": total,
        "next_cursor": next_cursor,
        "limit": lim,
        "offset": offset,
    }


@router.get("/people/merge-candidates")
def people_merge_candidates(
    request: Request,
    limit: int = Query(default=_CANDIDATES_DEFAULT_LIMIT, ge=1, le=_CANDIDATES_MAX_LIMIT),
    _user: str = Depends(require_user),
) -> dict[str, Any]:
    """Contact pairs that share a normalized name variant."""
    pool: ConnectionPool = request.app.state.pool
    lim = _clamp_limit(limit, default=_CANDIDATES_DEFAULT_LIMIT, maximum=_CANDIDATES_MAX_LIMIT)
    db = _maildb(pool)
    pairs = db.merge_candidates(limit=lim)
    return {"items": pairs}


@router.get("/people/{contact_id}")
def people_get(
    contact_id: UUID,
    request: Request,
    _user: str = Depends(require_user),
) -> dict[str, Any]:
    """Full contact card + activity, topics, address classes (PE-002)."""
    pool: ConnectionPool = request.app.state.pool
    db = _maildb(pool)
    card = db.get_contact(contact_id=contact_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    return _enrich_card(pool, card)


@router.get("/people/{contact_id}/graph")
def people_graph(
    contact_id: UUID,
    request: Request,
    depth: int = Query(default=1, ge=1),
    max_nodes: int = Query(default=_GRAPH_DEFAULT_MAX_NODES, ge=1, le=_GRAPH_HARD_MAX_NODES),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    _user: str = Depends(require_user),
) -> dict[str, Any]:
    """Bounded depth-1 ego co-participation graph (spec §7.3, PE-003, PERF-002).

    depth=1 only — deeper neighborhoods arrive on demand later. Never a global graph.
    """
    if depth != 1:
        raise HTTPException(
            status_code=422,
            detail="depth=1 only; deeper neighborhoods arrive on demand later",
        )
    pool: ConnectionPool = request.app.state.pool
    db = _maildb(pool)
    card = db.get_contact(contact_id=contact_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Contact not found")

    df = _parse_optional_date(date_from, field="date_from")
    dt = _parse_optional_date(date_to, field="date_to")
    # Inclusive end-of-day when only a calendar date was supplied.
    if dt is not None and date_to is not None and len(date_to.strip()) == 10:
        dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)

    ego_label = str(card.get("display_name") or "")
    if not ego_label:
        addrs = card.get("addresses") or []
        ego_label = str(addrs[0]) if addrs else str(contact_id)
    ego_kind = str(card.get("kind") or "unknown")
    lim = min(max(1, max_nodes), _GRAPH_HARD_MAX_NODES)

    return _ego_graph(
        pool,
        contact_id=contact_id,
        ego_label=ego_label,
        ego_kind=ego_kind,
        max_nodes=lim,
        date_from=df,
        date_to=dt,
    )


@router.patch("/people/{contact_id}")
def people_patch(
    contact_id: UUID,
    body: ContactPatch,
    request: Request,
    user: str = Depends(require_user),
) -> dict[str, Any]:
    """Curation write: kind/tags/notes/display_name (audited)."""
    pool: ConnectionPool = request.app.state.pool
    db = _maildb(pool)
    try:
        card = db.update_contact(
            contact_id=contact_id,
            kind=body.kind,
            tags=body.tags,
            notes=body.notes,
            display_name=body.display_name,
        )
    except ValueError as exc:
        msg = str(exc)
        if "does not exist" in msg:
            raise HTTPException(status_code=404, detail=msg) from exc
        raise HTTPException(status_code=422, detail=msg) from exc

    audit(
        pool,
        username=user,
        action="people_update",
        detail={
            "contact_id": str(contact_id),
            "kind": body.kind,
            "tags": body.tags,
            "notes": body.notes is not None,
            "display_name": body.display_name is not None,
        },
    )
    return _enrich_card(pool, card)


@router.post("/people/merge")
def people_merge(
    body: MergeRequest,
    request: Request,
    user: str = Depends(require_user),
) -> dict[str, Any]:
    """Merge source into target; audited (PE-001)."""
    pool: ConnectionPool = request.app.state.pool
    db = _maildb(pool)
    try:
        card = db.merge_contacts(source_id=body.source_id, target_id=body.target_id)
    except ValueError as exc:
        msg = str(exc)
        if "does not exist" in msg:
            raise HTTPException(status_code=404, detail=msg) from exc
        raise HTTPException(status_code=422, detail=msg) from exc

    merge_id = card.get("merge_id")
    audit(
        pool,
        username=user,
        action="people_merge",
        detail={
            "source_id": body.source_id,
            "target_id": body.target_id,
            "merge_id": str(merge_id) if merge_id is not None else None,
        },
    )
    return _enrich_card(pool, card)


@router.post("/people/unmerge")
def people_unmerge(
    body: UnmergeRequest,
    request: Request,
    user: str = Depends(require_user),
) -> dict[str, Any]:
    """Reverse a prior merge by merge_id; audited (PE-001)."""
    pool: ConnectionPool = request.app.state.pool
    db = _maildb(pool)
    try:
        result = db.unmerge_contacts(merge_id=body.merge_id)
    except ValueError as exc:
        msg = str(exc)
        if "does not exist" in msg:
            raise HTTPException(status_code=404, detail=msg) from exc
        raise HTTPException(status_code=422, detail=msg) from exc

    audit(
        pool,
        username=user,
        action="people_unmerge",
        detail={"merge_id": body.merge_id},
    )
    source = result.get("source") or {}
    target = result.get("target") or {}
    return {
        "source": _enrich_card(pool, source) if source.get("id") else source,
        "target": _enrich_card(pool, target) if target.get("id") else target,
    }
