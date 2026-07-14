# src/chronicle_server/search.py
"""POST /api/search — hybrid / exact / semantic ranked retrieval (Phase 2 Task 2.1)."""

from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from chronicle_server.auth import require_user
from chronicle_server.cursor import decode_cursor, encode_cursor
from chronicle_server.ids import encode_source_id
from chronicle_server.querysyntax import parse_query
from chronicle_server.scope import QueryScope, scope_filters, scope_fingerprint

if TYPE_CHECKING:
    from maildb.models import Email, UnifiedSearchResult
    from psycopg_pool import ConnectionPool

logger = structlog.get_logger()

router = APIRouter(tags=["search"])

_RRF_K = 60
_DEFAULT_LIMIT = 25
_MAX_LIMIT = 100
_MAX_WINDOW = 500
_SNIPPET_LEN = 300

SearchMode = Literal["hybrid", "exact", "semantic"]


# --- request / response models ---


class SearchRequest(BaseModel):
    query: str = ""
    mode: SearchMode = "hybrid"
    scope: QueryScope = Field(default_factory=QueryScope)
    limit: int = _DEFAULT_LIMIT
    cursor: str | None = None
    include_facets: bool = True

    @field_validator("limit")
    @classmethod
    def _clamp_limit(cls, value: int) -> int:
        if value < 1:
            raise ValueError("limit must be >= 1")
        return min(value, _MAX_LIMIT)

    @field_validator("mode")
    @classmethod
    def _check_mode(cls, value: str) -> str:
        if value not in ("hybrid", "exact", "semantic"):
            raise ValueError("mode must be hybrid, exact, or semantic")
        return value


class SearchResponse(BaseModel):
    results: list[dict[str, Any]]
    next_cursor: str | None = None
    scope: dict[str, Any]
    unsupported: list[str] = Field(default_factory=list)
    scope_fingerprint: str
    mode: SearchMode
    took_ms: int
    duplicates_suppressed: int = 0
    facets: dict[str, Any] | None = None
    facet_basis: str | None = None
    degraded: dict[str, str] | None = None

    model_config = ConfigDict(populate_by_name=True)


# --- scope merge ---


def _is_provided(value: Any) -> bool:
    if value is None:
        return False
    if value == [] or value == {}:
        return False
    if isinstance(value, dict):
        return any(v is not None and v != [] for v in value.values())
    return True


def merge_scope(request_scope: QueryScope, updates: dict[str, Any]) -> QueryScope:
    """Merge parser ``scope_updates`` into request scope; request non-empty fields win."""
    try:
        from_updates = QueryScope.model_validate(updates)
    except Exception:
        from_updates = QueryScope()

    req = request_scope.model_dump(mode="python", by_alias=True)
    upd = from_updates.model_dump(mode="python", by_alias=True)
    merged: dict[str, Any] = {}
    for key in set(req) | set(upd):
        rv = req.get(key)
        uv = upd.get(key)
        if _is_provided(rv):
            merged[key] = rv
        elif _is_provided(uv):
            merged[key] = uv
        else:
            merged[key] = rv if rv is not None else uv
    return QueryScope.model_validate(merged)


# --- helpers ---


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()  # type: ignore[no-any-return]
    return str(value)


def _snippet(text: str | None, free_text: str | None, max_len: int = _SNIPPET_LEN) -> str:
    if not text:
        return ""
    if free_text:
        lower = text.lower()
        needle = free_text.lower()
        idx = lower.find(needle)
        if idx >= 0:
            # Center window near the first hit; keep ~50 chars of lead-in when possible.
            start = max(0, idx - 50)
            end = min(len(text), start + max_len)
            start = max(0, end - max_len)
            piece = text[start:end]
            if start > 0:
                piece = "…" + piece
            if end < len(text):
                piece = piece + "…"
            return piece
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


def _exact_match_field(email: Email, free_text: str | None) -> str:
    if free_text:
        ft = free_text.lower()
        if email.subject and ft in email.subject.lower():
            return "subject"
        if email.body_text and ft in email.body_text.lower():
            return "body"
    return "metadata"


def _message_card(
    email: Email,
    *,
    free_text: str | None,
    match: dict[str, Any],
) -> dict[str, Any]:
    return {
        "result_type": "message",
        "id": encode_source_id("msg", email.id),
        "subject": email.subject,
        "sender": email.sender_address,
        "sender_name": email.sender_name,
        "date": _iso(email.date),
        "mailbox": email.source_account,
        "thread_id": encode_source_id("thr", email.thread_id) if email.thread_id else None,
        "snippet": _snippet(email.body_text, free_text),
        "has_attachment": bool(email.has_attachment),
        "match": match,
    }


def _attachment_card(
    *,
    attachment_id: int,
    filename: str,
    content_type: str | None,
    chunk_text: str | None,
    source_message_id: str | None,
    sender: str | None,
    date: str | None,
    extraction_status: str | None,
    free_text: str | None,
    match: dict[str, Any],
) -> dict[str, Any]:
    return {
        "result_type": "attachment",
        "id": encode_source_id("att", attachment_id),
        "filename": filename,
        "content_type": content_type,
        "source_message_id": source_message_id,
        "sender": sender,
        "date": date,
        "snippet": _snippet(chunk_text, free_text),
        "extraction_status": extraction_status,
        "match": match,
    }


def _maildb_kwargs(scope: QueryScope, *, for_find: bool = False) -> dict[str, Any]:
    """Map QueryScope onto MailDB method kwargs (best-effort single-value filters)."""
    kw: dict[str, Any] = {}
    if scope.date is not None:
        if scope.date.from_ is not None:
            kw["after"] = scope.date.from_
        if scope.date.to is not None:
            kw["before"] = scope.date.to
    if len(scope.senders) == 1:
        kw["sender"] = scope.senders[0]
    if len(scope.mailboxes) == 1:
        kw["account"] = scope.mailboxes[0]
    if len(scope.recipients) == 1:
        kw["recipient"] = scope.recipients[0]
    if for_find:
        if scope.has_attachment is not None:
            kw["has_attachment"] = scope.has_attachment
        if scope.subject_contains is not None:
            kw["subject_contains"] = scope.subject_contains
    return kw


def _email_passes_scope(email: Email, scope: QueryScope) -> bool:
    """Post-filter for multi-value / participant constraints MailDB kwargs can't express."""
    if scope.senders and email.sender_address not in scope.senders:
        return False
    if scope.mailboxes and email.source_account not in scope.mailboxes:
        return False
    if scope.has_attachment is not None and bool(email.has_attachment) != scope.has_attachment:
        return False
    if scope.subject_contains is not None:
        subj = email.subject or ""
        if scope.subject_contains.lower() not in subj.lower():
            return False
    if scope.recipients and not _email_has_any_recipient(email, scope.recipients):
        return False
    if scope.participants:
        ok = any(
            email.sender_address == p or _email_has_any_recipient(email, [p])
            for p in scope.participants
        )
        if not ok:
            return False
    return True


def _email_has_any_recipient(email: Email, addresses: list[str]) -> bool:
    if email.recipients is None:
        return False
    want = set(addresses)
    for bucket in (email.recipients.to, email.recipients.cc, email.recipients.bcc):
        if any(a in want for a in bucket):
            return True
    return False


def _wants_messages(scope: QueryScope) -> bool:
    if not scope.source_types:
        return True
    return "message" in scope.source_types


def _wants_attachments(scope: QueryScope) -> bool:
    if not scope.source_types:
        return True
    return "attachment" in scope.source_types


def _attachment_passes_scope(
    *,
    filename: str,
    content_type: str | None,
    scope: QueryScope,
) -> bool:
    if scope.filenames:
        fn_lower = filename.lower()
        if not any(f.lower() in fn_lower for f in scope.filenames):
            return False
    if scope.file_types:
        ct = (content_type or "").lower()
        # Match content-type families (e.g. filetype:pdf vs application/pdf)
        matched = any(ft.lower() in ct for ft in scope.file_types)
        if not matched:
            return False
    return True


def _result_key(card: dict[str, Any]) -> str:
    return str(card["id"])


def _suppress_duplicates(cards: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Drop exact-duplicate message bodies by (subject, sender, date); keep first."""
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    suppressed = 0
    for card in cards:
        if card.get("result_type") != "message":
            out.append(card)
            continue
        key = (card.get("subject"), card.get("sender"), card.get("date"))
        if key in seen:
            suppressed += 1
            continue
        seen.add(key)
        out.append(card)
    return out, suppressed


def _rrf_merge(
    exact_cards: list[dict[str, Any]],
    semantic_cards: list[dict[str, Any]],
    *,
    k: int = _RRF_K,
) -> list[dict[str, Any]]:
    """RRF-merge exact and semantic lists; exact∩semantic gets boost +1/k."""
    scores: dict[str, float] = {}
    exact_rank: dict[str, int] = {}
    semantic_rank: dict[str, int] = {}
    by_id: dict[str, dict[str, Any]] = {}
    similarities: dict[str, float | None] = {}

    for rank, card in enumerate(exact_cards, start=1):
        kid = _result_key(card)
        exact_rank[kid] = rank
        scores[kid] = scores.get(kid, 0.0) + 1.0 / (k + rank)
        by_id[kid] = card
        similarities.setdefault(kid, None)

    for rank, card in enumerate(semantic_cards, start=1):
        kid = _result_key(card)
        semantic_rank[kid] = rank
        scores[kid] = scores.get(kid, 0.0) + 1.0 / (k + rank)
        # Prefer semantic card payload when only on that leg; merge match later.
        if kid not in by_id:
            by_id[kid] = card
        sim = card.get("match", {}).get("similarity")
        if sim is not None:
            similarities[kid] = float(sim)

    for kid in scores:
        if kid in exact_rank and kid in semantic_rank:
            scores[kid] += 1.0 / k  # exact-match boost

    ordered = sorted(scores.keys(), key=lambda i: (-scores[i], i))
    merged: list[dict[str, Any]] = []
    for kid in ordered:
        card = dict(by_id[kid])
        card["match"] = {
            "kind": "hybrid",
            "exact_rank": exact_rank.get(kid),
            "semantic_rank": semantic_rank.get(kid),
            "similarity": similarities.get(kid),
        }
        merged.append(card)
    return merged


# --- retrieval legs ---


def _run_exact(
    db: Any,
    scope: QueryScope,
    free_text: str | None,
    fetch_limit: int,
) -> list[dict[str, Any]]:
    if not _wants_messages(scope) and not free_text:
        # Exact path is message-oriented; still allow free_text email hits.
        pass
    if not _wants_messages(scope):
        return []

    cards: list[dict[str, Any]] = []
    # Over-fetch for multi-value post-filters and later window slice.
    over = min(_MAX_WINDOW, max(fetch_limit * 2, fetch_limit))

    if free_text:
        kw = _maildb_kwargs(scope, for_find=False)
        # mention_search does not accept recipient/has_attachment/subject_contains
        kw.pop("recipient", None)
        emails, _ = db.mention_search(
            text=free_text, limit=over, offset=0, include_total=False, **kw
        )
        for email in emails:
            if not _email_passes_scope(email, scope):
                continue
            field = _exact_match_field(email, free_text)
            cards.append(
                _message_card(
                    email,
                    free_text=free_text,
                    match={"kind": "exact", "field": field},
                )
            )
    else:
        kw = _maildb_kwargs(scope, for_find=True)
        emails, _ = db.find(limit=over, offset=0, order="date DESC", include_total=False, **kw)
        for email in emails:
            if not _email_passes_scope(email, scope):
                continue
            cards.append(
                _message_card(
                    email,
                    free_text=None,
                    match={"kind": "exact", "field": "metadata"},
                )
            )

    # Exact is date DESC (no fabricated relevance) — re-sort to enforce.
    def _date_key(c: dict[str, Any]) -> str:
        return c.get("date") or ""

    cards.sort(key=_date_key, reverse=True)
    return cards[:fetch_limit] if fetch_limit else cards


def _unified_to_card(
    hit: UnifiedSearchResult,
    free_text: str | None,
    scope: QueryScope,
) -> dict[str, Any] | None:
    if hit.source == "email" and hit.email is not None:
        if not _wants_messages(scope):
            return None
        if not _email_passes_scope(hit.email, scope):
            return None
        return _message_card(
            hit.email,
            free_text=free_text,
            match={"kind": "semantic", "similarity": hit.similarity},
        )
    if hit.source == "attachment" and hit.attachment_result is not None:
        if not _wants_attachments(scope):
            return None
        ar = hit.attachment_result
        if not _attachment_passes_scope(
            filename=ar.filename,
            content_type=ar.content_type,
            scope=scope,
        ):
            return None
        # Resolve a source message id from linked email message_ids when possible.
        source_msg: str | None = None
        sender: str | None = None
        date_s: str | None = None
        # attachment_result.emails is list of message_id strings — not UUIDs.
        # Leave source_message_id null unless we have an email on the hit.
        if hit.email is not None:
            source_msg = encode_source_id("msg", hit.email.id)
            sender = hit.email.sender_address
            date_s = _iso(hit.email.date)
        return _attachment_card(
            attachment_id=ar.attachment_id,
            filename=ar.filename,
            content_type=ar.content_type,
            chunk_text=ar.chunk.text if ar.chunk else None,
            source_message_id=source_msg,
            sender=sender,
            date=date_s,
            extraction_status="extracted",
            free_text=free_text,
            match={"kind": "semantic", "similarity": hit.similarity},
        )
    return None


def _run_semantic(
    db: Any,
    scope: QueryScope,
    free_text: str | None,
    fetch_limit: int,
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Return (cards, error). error set when embedding service unavailable."""
    if not free_text:
        return [], None

    kw = _maildb_kwargs(scope, for_find=False)
    # search_all accepts recipient via _build_filters
    over = min(_MAX_WINDOW, max(fetch_limit * 2, fetch_limit))
    try:
        hits, _ = db.search_all(free_text, limit=over, offset=0, **kw)
    except Exception as exc:
        logger.warning("semantic_search_unavailable", error=str(exc))
        return None, "unavailable"

    cards: list[dict[str, Any]] = []
    for hit in hits:
        card = _unified_to_card(hit, free_text, scope)
        if card is not None:
            cards.append(card)
    return cards[:fetch_limit] if fetch_limit else cards, None


# --- facets ---


def _free_text_condition(free_text: str | None, params: dict[str, Any]) -> list[str]:
    if not free_text:
        return []
    escaped = free_text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    params["facet_pattern"] = f"%{escaped}%"
    return [
        "(body_text ILIKE %(facet_pattern)s ESCAPE '\\' "
        "OR subject ILIKE %(facet_pattern)s ESCAPE '\\')"
    ]


def compute_facets(
    pool: ConnectionPool,
    scope: QueryScope,
    free_text: str | None,
) -> dict[str, list[dict[str, Any]]]:
    """Exact-leg facets: mailbox (top 10), year, has_attachment split."""
    scope_conds, params = scope_filters(scope)
    conditions = list(scope_conds)
    conditions.extend(_free_text_condition(free_text, params))
    where = " AND ".join(conditions) if conditions else "TRUE"

    with pool.connection() as conn:
        mailbox_rows = conn.execute(
            f"""
            SELECT source_account AS value, count(*)::int AS count
              FROM emails
             WHERE {where} AND source_account IS NOT NULL
             GROUP BY source_account
             ORDER BY count DESC, source_account
             LIMIT 10
            """,
            params,
        ).fetchall()

        year_rows = conn.execute(
            f"""
            SELECT EXTRACT(YEAR FROM date)::int AS value, count(*)::int AS count
              FROM emails
             WHERE {where} AND date IS NOT NULL
             GROUP BY 1
             ORDER BY 1
            """,
            params,
        ).fetchall()

        att_rows = conn.execute(
            f"""
            SELECT has_attachment AS value, count(*)::int AS count
              FROM emails
             WHERE {where}
             GROUP BY has_attachment
             ORDER BY has_attachment
            """,
            params,
        ).fetchall()

    return {
        "mailbox": [{"value": r[0], "count": r[1]} for r in mailbox_rows],
        "year": [{"value": r[0], "count": r[1]} for r in year_rows],
        "has_attachment": [{"value": bool(r[0]), "count": r[1]} for r in att_rows],
    }


# --- main pipeline ---


def run_search(
    pool: ConnectionPool,
    body: SearchRequest,
    secret_key: str,
) -> SearchResponse:
    t0 = time.perf_counter()

    parsed = parse_query(body.query)
    merged = merge_scope(body.scope, parsed.scope_updates)
    free_text = merged.free_text or parsed.free_text or None
    if free_text == "":
        free_text = None

    # Cursor → offset into ranked window
    offset = 0
    if body.cursor:
        try:
            payload = decode_cursor(body.cursor, secret_key)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid cursor") from exc
        raw_o = payload.get("o", 0)
        try:
            offset = int(raw_o)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="invalid cursor") from exc
        if offset < 0:
            raise HTTPException(status_code=400, detail="invalid cursor")

    if offset + body.limit > _MAX_WINDOW:
        raise HTTPException(
            status_code=422,
            detail="narrow the query",
        )

    from maildb import MailDB

    db = MailDB._from_pool(pool)
    fetch_limit = min(_MAX_WINDOW, offset + body.limit)

    degraded: dict[str, str] | None = None
    ranked: list[dict[str, Any]] = []

    if body.mode == "exact":
        ranked = _run_exact(db, merged, free_text, fetch_limit=min(_MAX_WINDOW, fetch_limit + 50))
    elif body.mode == "semantic":
        cards, err = _run_semantic(
            db, merged, free_text, fetch_limit=min(_MAX_WINDOW, fetch_limit + 50)
        )
        if err is not None:
            raise HTTPException(
                status_code=503,
                detail={"error": "semantic search unavailable", "semantic": "unavailable"},
            )
        ranked = cards or []
    else:  # hybrid
        exact_fetch = min(_MAX_WINDOW, max(body.limit * 2, fetch_limit * 2))
        exact_cards = _run_exact(db, merged, free_text, fetch_limit=exact_fetch)
        sem_cards, err = _run_semantic(db, merged, free_text, fetch_limit=exact_fetch)
        if err is not None:
            # NOT silent: return exact results with degraded flag
            degraded = {"semantic": "unavailable"}
            ranked = exact_cards
        else:
            ranked = _rrf_merge(exact_cards, sem_cards or [])

    ranked, dup_n = _suppress_duplicates(ranked)

    page = ranked[offset : offset + body.limit]
    has_more = (offset + body.limit) < len(ranked) and (offset + body.limit) < _MAX_WINDOW
    # Also has more if we filled the page and haven't hit window end
    if len(ranked) > offset + body.limit:
        has_more = True
    if offset + body.limit >= _MAX_WINDOW:
        has_more = False

    next_cursor: str | None = None
    if has_more and page:
        next_cursor = encode_cursor({"o": offset + body.limit}, secret_key)

    facets: dict[str, Any] | None = None
    facet_basis: str | None = None
    # Facets only when requested and not paging (cursor unset)
    if body.include_facets and body.cursor is None:
        facets = compute_facets(pool, merged, free_text)
        facet_basis = "exact"

    took_ms = int((time.perf_counter() - t0) * 1000)

    return SearchResponse(
        results=page,
        next_cursor=next_cursor,
        scope=merged.model_dump(mode="json", by_alias=True, exclude_none=True),
        unsupported=list(parsed.unsupported),
        scope_fingerprint=scope_fingerprint(merged),
        mode=body.mode,
        took_ms=took_ms,
        duplicates_suppressed=dup_n,
        facets=facets,
        facet_basis=facet_basis,
        degraded=degraded,
    )


@router.post("/search")
def post_search(
    body: SearchRequest,
    request: Request,
    _user: str = Depends(require_user),
) -> SearchResponse:
    """Ranked source retrieval: hybrid / exact / semantic modes."""
    pool: ConnectionPool = request.app.state.pool
    secret_key: str = request.app.state.settings.secret_key
    return run_search(pool, body, secret_key)
