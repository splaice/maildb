"""Analyst-authored events: CRUD, versioning, list, and status transitions.

Phase 3 Task 3.1 — origin ``analyst`` events only. Automatic generation arrives
in 3.2. Version rows are append-only; edits never overwrite history.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from chronicle_server.auth import require_user
from chronicle_server.cursor import decode_cursor, encode_cursor
from chronicle_server.db import audit
from chronicle_server.ids import decode_source_id, msg_key_to_uuid
from chronicle_server.scope import QueryScope

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

    from chronicle_server.config import ChronicleSettings

logger = structlog.get_logger()

router = APIRouter(tags=["events"])

TimePrecision = Literal["year", "quarter", "month", "week", "day", "hour"]
EventType = Literal[
    "decision",
    "meeting",
    "travel",
    "purchase",
    "deadline",
    "transition",
    "document",
    "communication",
    "user_defined",
]
EventStatus = Literal[
    "unreviewed",
    "confirmed",
    "edited",
    "dismissed",
    "superseded",
    "unresolved",
]
ClaimStatus = Literal["direct", "supported", "conflicting", "unresolved"]
Origin = Literal["source", "imported", "automatic", "analyst"]

_VALID_PRECISIONS = frozenset({"year", "quarter", "month", "week", "day", "hour"})
_VALID_TYPES = frozenset(
    {
        "decision",
        "meeting",
        "travel",
        "purchase",
        "deadline",
        "transition",
        "document",
        "communication",
        "user_defined",
    }
)
_VALID_STATUSES = frozenset(
    {"unreviewed", "confirmed", "edited", "dismissed", "superseded", "unresolved"}
)
_STATUS_ONLY_TARGETS = frozenset({"confirmed", "dismissed", "unreviewed"})
_LIST_DEFAULT_LIMIT = 100
_LIST_MAX_LIMIT = 200


# --- request / response models ---


class ClaimCreate(BaseModel):
    text: str = Field(min_length=1)
    citations: list[str] = Field(default_factory=list)  # source_ids
    status: ClaimStatus = "direct"


class EventCreate(BaseModel):
    title: str = Field(min_length=1)
    time_start: str
    time_end: str | None = None
    time_precision: TimePrecision = "day"
    event_type: EventType = "communication"
    summary: str | None = None
    claims: list[ClaimCreate] = Field(default_factory=list)


class EventPatch(BaseModel):
    """Optimistic edit: requires ``current_version``; 409 on mismatch."""

    current_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1)
    time_start: str | None = None
    time_end: str | None = None
    time_precision: TimePrecision | None = None
    event_type: EventType | None = None
    summary: str | None = None
    claims: list[ClaimCreate] | None = None
    # Target status for confirm/dismiss/restore transitions.
    status: EventStatus | None = None

    @model_validator(mode="after")
    def _at_least_one_change(self) -> EventPatch:
        content = any(
            v is not None
            for v in (
                self.title,
                self.time_start,
                self.time_end,
                self.time_precision,
                self.event_type,
                self.summary,
                self.claims,
            )
        )
        if not content and self.status is None:
            raise ValueError("at least one of title, time, type, summary, claims, status required")
        return self


class TimeRange(BaseModel):
    from_: str = Field(..., alias="from")
    to: str

    model_config = ConfigDict(populate_by_name=True)


class EventListRequest(BaseModel):
    scope: QueryScope = Field(default_factory=QueryScope)
    viewport: TimeRange
    include_dismissed: bool = False
    cursor: str | None = None
    limit: int = _LIST_DEFAULT_LIMIT

    @field_validator("limit")
    @classmethod
    def _clamp_limit(cls, value: int) -> int:
        if value < 1:
            raise ValueError("limit must be >= 1")
        return min(value, _LIST_MAX_LIMIT)


class AdoptRequest(BaseModel):
    """Optimistic adopt of a higher-numbered suggestion version."""

    current_version: int = Field(ge=1)


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


def _parse_ts(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        text = text + "T00:00:00+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _source_type_for_kind(kind: str) -> str:
    if kind == "msg":
        return "message"
    if kind == "att":
        return "attachment"
    if kind == "thr":
        return "thread"
    return kind


def _validate_source_ids(pool: ConnectionPool, source_ids: list[str]) -> list[dict[str, Any]]:
    """Decode + existence-check source_ids; return citation dicts. 404 on unknown."""
    citations: list[dict[str, Any]] = []
    for sid in source_ids:
        try:
            kind, key = decode_source_id(sid)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=f"Unknown source_id: {sid}") from exc

        with pool.connection() as conn:
            exists = False
            if kind == "msg" and isinstance(key, int):
                row = conn.execute(
                    "SELECT 1 FROM emails WHERE id = %(id)s",
                    {"id": msg_key_to_uuid(key)},
                ).fetchone()
                exists = row is not None
            elif kind == "att" and isinstance(key, int):
                row = conn.execute(
                    "SELECT 1 FROM attachments WHERE id = %(id)s",
                    {"id": key},
                ).fetchone()
                exists = row is not None
            elif kind == "thr" and isinstance(key, str):
                row = conn.execute(
                    "SELECT 1 FROM emails WHERE thread_id = %(tid)s LIMIT 1",
                    {"tid": key},
                ).fetchone()
                exists = row is not None

        if not exists:
            raise HTTPException(status_code=404, detail=f"Unknown source_id: {sid}")

        citations.append(
            {
                "source_id": sid,
                "source_type": _source_type_for_kind(kind),
                "excerpt": None,
                "excerpt_hash": None,
                "location": None,
            }
        )
    return citations


def _hydrate_citations(
    pool: ConnectionPool,
    citations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach date/sender/subject display metadata via one set-based query per kind."""
    if not citations:
        return []

    msg_ids: list[UUID] = []
    att_ids: list[int] = []
    thr_ids: list[str] = []
    sid_to_kind: dict[str, str] = {}

    for cit in citations:
        sid = cit.get("source_id")
        if not sid or not isinstance(sid, str):
            continue
        try:
            kind, key = decode_source_id(sid)
        except ValueError:
            continue
        sid_to_kind[sid] = kind
        if kind == "msg" and isinstance(key, int):
            msg_ids.append(msg_key_to_uuid(key))
        elif kind == "att" and isinstance(key, int):
            att_ids.append(key)
        elif kind == "thr" and isinstance(key, str):
            thr_ids.append(key)

    meta: dict[str, dict[str, Any]] = {}

    with pool.connection() as conn:
        if msg_ids:
            # import encode here to map back to source_id
            from chronicle_server.ids import encode_source_id

            rows = conn.execute(
                """
                SELECT id, date, sender_name, sender_address, subject
                  FROM emails
                 WHERE id = ANY(%(ids)s)
                """,
                {"ids": msg_ids},
            ).fetchall()
            for row in rows:
                sid = encode_source_id("msg", row[0])
                meta[sid] = {
                    "date": _iso(row[1]),
                    "sender": row[2] or row[3],
                    "subject": row[4],
                }
        if att_ids:
            from chronicle_server.ids import encode_source_id

            rows = conn.execute(
                """
                SELECT a.id, e.date, e.sender_name, e.sender_address, a.filename
                  FROM attachments a
                  LEFT JOIN email_attachments ea ON ea.attachment_id = a.id
                  LEFT JOIN emails e ON e.id = ea.email_id
                 WHERE a.id = ANY(%(ids)s)
                """,
                {"ids": att_ids},
            ).fetchall()
            for row in rows:
                sid = encode_source_id("att", int(row[0]))
                # First row wins if multiple parent emails
                if sid not in meta:
                    meta[sid] = {
                        "date": _iso(row[1]),
                        "sender": row[2] or row[3],
                        "subject": row[4],
                    }
        if thr_ids:
            from chronicle_server.ids import encode_source_id

            rows = conn.execute(
                """
                SELECT DISTINCT ON (thread_id)
                       thread_id, date, sender_name, sender_address, subject
                  FROM emails
                 WHERE thread_id = ANY(%(ids)s)
                 ORDER BY thread_id, date ASC NULLS LAST
                """,
                {"ids": thr_ids},
            ).fetchall()
            for row in rows:
                sid = encode_source_id("thr", str(row[0]))
                meta[sid] = {
                    "date": _iso(row[1]),
                    "sender": row[2] or row[3],
                    "subject": row[4],
                }

    out: list[dict[str, Any]] = []
    for cit in citations:
        entry = dict(cit)
        sid = entry.get("source_id")
        if isinstance(sid, str) and sid in meta:
            entry.update(meta[sid])
        else:
            entry.setdefault("date", None)
            entry.setdefault("sender", None)
            entry.setdefault("subject", None)
        out.append(entry)
    return out


def _event_row_dict(row: Any) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "title": row[1],
        "time_start": _iso(row[2]),
        "time_end": _iso(row[3]),
        "time_precision": row[4],
        "origin": row[5],
        "event_type": row[6],
        "status": row[7],
        "evidence_strength": row[8],
        "scope_fingerprint": row[9],
        "current_version": int(row[10]),
        "created_at": _iso(row[11]),
        "updated_at": _iso(row[12]),
    }


def _fetch_event(pool: ConnectionPool, event_id: UUID) -> dict[str, Any] | None:
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT id, title, time_start, time_end, time_precision, origin,
                   event_type, status, evidence_strength, scope_fingerprint,
                   current_version, created_at, updated_at
              FROM app_events
             WHERE id = %(id)s
            """,
            {"id": event_id},
        ).fetchone()
    if row is None:
        return None
    return _event_row_dict(row)


def _fetch_version(
    pool: ConnectionPool,
    event_id: UUID,
    version: int,
) -> dict[str, Any] | None:
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT version, author, title, summary, derivation, created_at
              FROM app_event_versions
             WHERE event_id = %(eid)s AND version = %(ver)s
            """,
            {"eid": event_id, "ver": version},
        ).fetchone()
    if row is None:
        return None
    derivation = row[4] if isinstance(row[4], dict) else {}
    return {
        "version": int(row[0]),
        "author": row[1],
        "title": row[2],
        "summary": row[3],
        "derivation": derivation,
        "created_at": _iso(row[5]),
    }


def _fetch_claims(
    pool: ConnectionPool,
    event_id: UUID,
    version: int,
) -> list[dict[str, Any]]:
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT id, position, text, status, citations
              FROM app_event_claims
             WHERE event_id = %(eid)s AND version = %(ver)s
             ORDER BY position ASC
            """,
            {"eid": event_id, "ver": version},
        ).fetchall()

    claims: list[dict[str, Any]] = []
    all_cits: list[dict[str, Any]] = []
    raw_per_claim: list[list[dict[str, Any]]] = []

    for row in rows:
        raw = row[4] if isinstance(row[4], list) else []
        cits = [dict(c) if isinstance(c, dict) else {} for c in raw]
        raw_per_claim.append(cits)
        all_cits.extend(cits)

    hydrated_all = _hydrate_citations(pool, all_cits)
    # Re-slice hydrated citations back onto claims in order.
    # Hydration preserves input order.
    idx = 0
    for i, row in enumerate(rows):
        n = len(raw_per_claim[i])
        claim_cits = hydrated_all[idx : idx + n]
        idx += n
        claims.append(
            {
                "id": str(row[0]),
                "position": int(row[1]),
                "text": row[2],
                "status": row[3],
                "citations": claim_cits,
            }
        )
    return claims


def _max_version(pool: ConnectionPool, event_id: UUID) -> int:
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT coalesce(max(version), 0)
              FROM app_event_versions
             WHERE event_id = %(eid)s
            """,
            {"eid": event_id},
        ).fetchone()
    return int(row[0]) if row is not None else 0


def _has_suggestions(pool: ConnectionPool, event_id: UUID, current_version: int) -> bool:
    """True when any version number is higher than the analyst-visible current."""
    return _max_version(pool, event_id) > current_version


def _compute_conflicts(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Claims in conflict: status=conflicting, or source-overlap direct vs conflicting.

    Keep simple: expose per-claim position + statuses so the UI can render both chains.
    """
    by_source: dict[str, list[tuple[int, str]]] = {}
    for claim in claims:
        pos = int(claim["position"])
        status = str(claim["status"])
        for cit in claim.get("citations") or []:
            if not isinstance(cit, dict):
                continue
            sid = cit.get("source_id")
            if isinstance(sid, str) and sid:
                by_source.setdefault(sid, []).append((pos, status))

    overlap_positions: set[int] = set()
    for entries in by_source.values():
        statuses = {s for _, s in entries}
        if "direct" in statuses and "conflicting" in statuses:
            for pos, _ in entries:
                overlap_positions.add(pos)

    conflicts: list[dict[str, Any]] = []
    seen: set[int] = set()
    for claim in claims:
        pos = int(claim["position"])
        status = str(claim["status"])
        if status == "conflicting" or pos in overlap_positions:
            if pos in seen:
                continue
            seen.add(pos)
            conflicts.append({"claim_position": pos, "statuses": [status]})
    return conflicts


def _full_event(pool: ConnectionPool, event_id: UUID) -> dict[str, Any]:
    event = _fetch_event(pool, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    ver = int(event["current_version"])
    version = _fetch_version(pool, event_id, ver)
    claims = _fetch_claims(pool, event_id, ver)
    out = dict(event)
    out["version"] = version
    out["claims"] = claims
    # Prefer version title/summary when present (title also on event row).
    if version:
        out["summary"] = version.get("summary")
        out["derivation"] = version.get("derivation") or {}
    else:
        out["summary"] = None
        out["derivation"] = {}
    out["has_suggestions"] = _has_suggestions(pool, event_id, ver)
    out["conflicts"] = _compute_conflicts(claims)
    return out


def _fetch_all_versions(
    pool: ConnectionPool,
    event_id: UUID,
) -> list[dict[str, Any]]:
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT version, author, title, summary, derivation, created_at
              FROM app_event_versions
             WHERE event_id = %(eid)s
             ORDER BY version ASC
            """,
            {"eid": event_id},
        ).fetchall()
    versions: list[dict[str, Any]] = []
    for row in rows:
        derivation = row[4] if isinstance(row[4], dict) else {}
        versions.append(
            {
                "version": int(row[0]),
                "author": row[1],
                "title": row[2],
                "summary": row[3],
                "derivation": derivation,
                "created_at": _iso(row[5]),
            }
        )
    return versions


def _insert_claims(
    conn: Any,
    *,
    event_id: UUID,
    version: int,
    claims: list[tuple[str, str, list[dict[str, Any]]]],
) -> None:
    for position, (text, status, citations) in enumerate(claims):
        conn.execute(
            """
            INSERT INTO app_event_claims (event_id, version, position, text, status, citations)
            VALUES (%(eid)s, %(ver)s, %(pos)s, %(text)s, %(status)s, %(cits)s)
            """,
            {
                "eid": event_id,
                "ver": version,
                "pos": position,
                "text": text,
                "status": status,
                "cits": Jsonb(citations),
            },
        )


def _build_claim_rows(
    pool: ConnectionPool,
    claims: list[ClaimCreate],
) -> list[tuple[str, str, list[dict[str, Any]]]]:
    out: list[tuple[str, str, list[dict[str, Any]]]] = []
    for claim in claims:
        cits = _validate_source_ids(pool, claim.citations)
        out.append((claim.text, claim.status, cits))
    return out


def _copy_claims_from_version(
    pool: ConnectionPool,
    event_id: UUID,
    from_version: int,
) -> list[tuple[str, str, list[dict[str, Any]]]]:
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT text, status, citations
              FROM app_event_claims
             WHERE event_id = %(eid)s AND version = %(ver)s
             ORDER BY position ASC
            """,
            {"eid": event_id, "ver": from_version},
        ).fetchall()
    out: list[tuple[str, str, list[dict[str, Any]]]] = []
    for row in rows:
        raw = row[2] if isinstance(row[2], list) else []
        cits = [dict(c) if isinstance(c, dict) else {} for c in raw]
        out.append((str(row[0]), str(row[1]), cits))
    return out


def _validate_status_transition(current: str, target: str) -> None:
    """Endpoint-validated transitions: confirm, dismiss, restore."""
    if target == "confirmed":
        # confirm from unreviewed / edited / unresolved
        if current in ("confirmed", "dismissed", "superseded"):
            if current == "confirmed":
                return  # idempotent confirm ok
            raise HTTPException(
                status_code=422,
                detail=f"cannot confirm from status {current!r}",
            )
        return
    if target == "dismissed":
        if current == "superseded":
            raise HTTPException(
                status_code=422,
                detail=f"cannot dismiss from status {current!r}",
            )
        return
    if target == "unreviewed":
        # restore: only from dismissed
        if current != "dismissed":
            raise HTTPException(
                status_code=422,
                detail="restore only allowed from dismissed",
            )
        return
    if target == "edited":
        return
    raise HTTPException(status_code=422, detail=f"invalid status transition to {target!r}")


# --- routes ---


@router.post("/events", status_code=201)
def create_event(
    body: EventCreate,
    request: Request,
    user: str = Depends(require_user),
) -> dict[str, Any]:
    """Create an analyst-authored event (origin analyst, status confirmed)."""
    pool: ConnectionPool = request.app.state.pool

    if body.time_precision not in _VALID_PRECISIONS:
        raise HTTPException(status_code=422, detail="invalid time_precision")
    if body.event_type not in _VALID_TYPES:
        raise HTTPException(status_code=422, detail="invalid event_type")

    time_start = _parse_ts(body.time_start)
    time_end = _parse_ts(body.time_end) if body.time_end else None
    claim_rows = _build_claim_rows(pool, body.claims)

    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO app_events (
                title, time_start, time_end, time_precision, origin,
                event_type, status, current_version
            ) VALUES (
                %(title)s, %(ts)s, %(te)s, %(prec)s, 'analyst',
                %(etype)s, 'confirmed', 1
            )
            RETURNING id, title, time_start, time_end, time_precision, origin,
                      event_type, status, evidence_strength, scope_fingerprint,
                      current_version, created_at, updated_at
            """,
            {
                "title": body.title,
                "ts": time_start,
                "te": time_end,
                "prec": body.time_precision,
                "etype": body.event_type,
            },
        ).fetchone()
        assert row is not None
        event_id: UUID = row[0]
        conn.execute(
            """
            INSERT INTO app_event_versions (
                event_id, version, author, title, summary, derivation
            ) VALUES (
                %(eid)s, 1, 'analyst', %(title)s, %(summary)s, '{}'::jsonb
            )
            """,
            {
                "eid": event_id,
                "title": body.title,
                "summary": body.summary,
            },
        )
        _insert_claims(conn, event_id=event_id, version=1, claims=claim_rows)
        conn.commit()

    audit(
        pool,
        username=user,
        action="event_create",
        detail={"event_id": str(event_id), "origin": "analyst"},
    )
    logger.info("event_created", event_id=str(event_id), username=user)
    return _full_event(pool, event_id)


@router.get("/events/{event_id}")
def get_event(
    event_id: UUID,
    request: Request,
    _user: str = Depends(require_user),
) -> dict[str, Any]:
    """Event + current version + ordered claims with hydrated citations."""
    pool: ConnectionPool = request.app.state.pool
    return _full_event(pool, event_id)


@router.get("/events/{event_id}/versions")
def list_event_versions(
    event_id: UUID,
    request: Request,
    _user: str = Depends(require_user),
) -> dict[str, Any]:
    """Full version list with claims; higher-than-current versions are suggestions."""
    pool: ConnectionPool = request.app.state.pool
    event = _fetch_event(pool, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    current = int(event["current_version"])
    versions_out: list[dict[str, Any]] = []
    for ver in _fetch_all_versions(pool, event_id):
        vnum = int(ver["version"])
        entry = dict(ver)
        entry["claims"] = _fetch_claims(pool, event_id, vnum)
        # Analyst-visible pointer is current_version; higher numbers are suggestions.
        entry["is_suggestion"] = vnum > current
        versions_out.append(entry)
    return {
        "event_id": str(event_id),
        "current_version": current,
        "versions": versions_out,
    }


@router.post("/events/{event_id}/adopt/{version}")
def adopt_event_version(
    event_id: UUID,
    version: int,
    body: AdoptRequest,
    request: Request,
    user: str = Depends(require_user),
) -> dict[str, Any]:
    """Adopt a suggestion version: point current_version at it, status edited."""
    pool: ConnectionPool = request.app.state.pool
    event = _fetch_event(pool, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")

    if int(event["current_version"]) != body.current_version:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "version_conflict",
                "current_version": event["current_version"],
            },
        )

    target = _fetch_version(pool, event_id, version)
    if target is None:
        raise HTTPException(status_code=404, detail="Version not found")

    with pool.connection() as conn:
        updated = conn.execute(
            """
            UPDATE app_events
               SET current_version = %(ver)s,
                   title = %(title)s,
                   status = 'edited',
                   updated_at = now()
             WHERE id = %(id)s AND current_version = %(cur)s
            RETURNING id
            """,
            {
                "id": event_id,
                "ver": version,
                "title": target["title"],
                "cur": body.current_version,
            },
        ).fetchone()
        if updated is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "version_conflict",
                    "current_version": event["current_version"],
                },
            )
        conn.commit()

    audit(
        pool,
        username=user,
        action="event_adopt",
        detail={
            "event_id": str(event_id),
            "version": version,
            "from_version": body.current_version,
        },
    )
    return _full_event(pool, event_id)


@router.patch("/events/{event_id}")
def patch_event(
    event_id: UUID,
    body: EventPatch,
    request: Request,
    user: str = Depends(require_user),
) -> dict[str, Any]:
    """Analyst edit or status transition with optimistic concurrency."""
    pool: ConnectionPool = request.app.state.pool
    event = _fetch_event(pool, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")

    if int(event["current_version"]) != body.current_version:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "version_conflict",
                "current_version": event["current_version"],
            },
        )

    content_fields = any(
        v is not None
        for v in (
            body.title,
            body.time_start,
            body.time_end,
            body.time_precision,
            body.event_type,
            body.summary,
            body.claims,
        )
    )
    # time_end alone with explicit None is still a content change only if provided —
    # model uses None for "not set"; we treat only non-None fields as content.

    if content_fields:
        new_title = body.title if body.title is not None else event["title"]
        new_type = body.event_type if body.event_type is not None else event["event_type"]
        new_prec = (
            body.time_precision if body.time_precision is not None else event["time_precision"]
        )
        if new_prec not in _VALID_PRECISIONS:
            raise HTTPException(status_code=422, detail="invalid time_precision")
        if new_type not in _VALID_TYPES:
            raise HTTPException(status_code=422, detail="invalid event_type")

        new_ts = _parse_ts(body.time_start) if body.time_start else _parse_ts(event["time_start"])
        if body.time_end is not None:
            new_te: datetime | None = _parse_ts(body.time_end) if body.time_end else None
        else:
            new_te = _parse_ts(event["time_end"]) if event["time_end"] else None

        cur_ver = int(event["current_version"])
        next_ver = cur_ver + 1
        prev_version = _fetch_version(pool, event_id, cur_ver)
        new_summary = (
            body.summary
            if body.summary is not None
            else (prev_version.get("summary") if prev_version else None)
        )

        if body.claims is not None:
            claim_rows = _build_claim_rows(pool, body.claims)
        else:
            claim_rows = _copy_claims_from_version(pool, event_id, cur_ver)

        with pool.connection() as conn:
            updated = conn.execute(
                """
                UPDATE app_events
                   SET title = %(title)s,
                       time_start = %(ts)s,
                       time_end = %(te)s,
                       time_precision = %(prec)s,
                       event_type = %(etype)s,
                       status = 'edited',
                       current_version = %(ver)s,
                       updated_at = now()
                 WHERE id = %(id)s AND current_version = %(cur)s
                RETURNING id
                """,
                {
                    "id": event_id,
                    "title": new_title,
                    "ts": new_ts,
                    "te": new_te,
                    "prec": new_prec,
                    "etype": new_type,
                    "ver": next_ver,
                    "cur": cur_ver,
                },
            ).fetchone()
            if updated is None:
                raise HTTPException(
                    status_code=409,
                    detail={"error": "version_conflict", "current_version": cur_ver},
                )
            conn.execute(
                """
                INSERT INTO app_event_versions (
                    event_id, version, author, title, summary, derivation
                ) VALUES (
                    %(eid)s, %(ver)s, 'analyst', %(title)s, %(summary)s, '{}'::jsonb
                )
                """,
                {
                    "eid": event_id,
                    "ver": next_ver,
                    "title": new_title,
                    "summary": new_summary,
                },
            )
            _insert_claims(conn, event_id=event_id, version=next_ver, claims=claim_rows)
            conn.commit()

        audit(
            pool,
            username=user,
            action="event_edit",
            detail={"event_id": str(event_id), "version": next_ver},
        )
        return _full_event(pool, event_id)

    # Status-only transition
    assert body.status is not None
    target = body.status
    if target not in _STATUS_ONLY_TARGETS and target not in _VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"invalid status {target!r}")
    if target not in _STATUS_ONLY_TARGETS:
        raise HTTPException(
            status_code=422,
            detail=f"status-only transitions limited to {sorted(_STATUS_ONLY_TARGETS)}",
        )

    _validate_status_transition(str(event["status"]), target)

    action_map = {
        "confirmed": "event_confirm",
        "dismissed": "event_dismiss",
        "unreviewed": "event_restore",
    }
    action = action_map.get(target, "event_status")

    with pool.connection() as conn:
        updated = conn.execute(
            """
            UPDATE app_events
               SET status = %(status)s,
                   updated_at = now()
             WHERE id = %(id)s AND current_version = %(cur)s
            RETURNING id
            """,
            {
                "id": event_id,
                "status": target,
                "cur": body.current_version,
            },
        ).fetchone()
        if updated is None:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "version_conflict",
                    "current_version": event["current_version"],
                },
            )
        conn.commit()

    audit(
        pool,
        username=user,
        action=action,
        detail={"event_id": str(event_id), "status": target},
    )
    return _full_event(pool, event_id)


@router.delete("/events/{event_id}", status_code=204)
def delete_event(
    event_id: UUID,
    request: Request,
    user: str = Depends(require_user),
) -> None:
    """Hard-delete only analyst-origin events; others must be dismissed (403)."""
    pool: ConnectionPool = request.app.state.pool
    event = _fetch_event(pool, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    if event["origin"] != "analyst":
        raise HTTPException(
            status_code=403,
            detail="Only analyst-origin events can be deleted; dismiss others instead",
        )
    with pool.connection() as conn:
        conn.execute("DELETE FROM app_events WHERE id = %(id)s", {"id": event_id})
        conn.commit()
    audit(
        pool,
        username=user,
        action="event_delete",
        detail={"event_id": str(event_id)},
    )


@router.post("/events/list")
def list_events(
    body: EventListRequest,
    request: Request,
    _user: str = Depends(require_user),
) -> dict[str, Any]:
    """Events intersecting the viewport; scope date only; keyset (time_start, id)."""
    pool: ConnectionPool = request.app.state.pool
    settings: ChronicleSettings = request.app.state.settings

    vp_from = _parse_ts(body.viewport.from_)
    vp_to = _parse_ts(body.viewport.to)
    if vp_to <= vp_from:
        raise HTTPException(status_code=422, detail="viewport.to must be after viewport.from")

    conditions = [
        "time_start < %(vp_to)s",
        "coalesce(time_end, time_start) >= %(vp_from)s",
    ]
    params: dict[str, Any] = {
        "vp_from": vp_from,
        "vp_to": vp_to,
        "limit": body.limit + 1,
    }

    if not body.include_dismissed:
        conditions.append("status <> 'dismissed'")

    # Scope date only (person/topic scoping arrives later).
    if body.scope.date is not None:
        if body.scope.date.from_ is not None:
            conditions.append("time_start >= %(scope_from)s")
            params["scope_from"] = body.scope.date.from_
        if body.scope.date.to is not None:
            conditions.append("time_start < %(scope_to)s")
            params["scope_to"] = body.scope.date.to

    if body.cursor:
        try:
            cur = decode_cursor(body.cursor, settings.secret_key)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid cursor") from exc
        try:
            cur_ts = _parse_ts(str(cur["time_start"]))
            cur_id = UUID(str(cur["id"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail="invalid cursor") from exc
        conditions.append("(time_start, id) > (%(cur_ts)s, %(cur_id)s)")
        params["cur_ts"] = cur_ts
        params["cur_id"] = cur_id

    sql = f"""
        SELECT id, title, time_start, time_end, time_precision, origin,
               event_type, status, evidence_strength, scope_fingerprint,
               current_version, created_at, updated_at
          FROM app_events
         WHERE {" AND ".join(conditions)}
         ORDER BY time_start ASC, id ASC
         LIMIT %(limit)s
    """
    with pool.connection() as conn:
        rows = conn.execute(sql, params).fetchall()

    items = [_event_row_dict(r) for r in rows[: body.limit]]
    next_cursor = None
    if len(rows) > body.limit:
        last = items[-1]
        next_cursor = encode_cursor(
            {"time_start": last["time_start"], "id": last["id"]},
            settings.secret_key,
        )

    return {"items": items, "next_cursor": next_cursor}
