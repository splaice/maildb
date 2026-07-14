"""POST /api/ask — SSE grounded answers over hybrid retrieval (Phase 2 Task 2.4)."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from chronicle_server.auth import require_user
from chronicle_server.gateway import (
    AskSource,
    ModelGateway,
    plain_text_from_bodies,
    prepare_source_text,
    resolve_citations,
)
from chronicle_server.ids import decode_source_id, msg_key_to_uuid
from chronicle_server.scope import QueryScope, scope_fingerprint
from chronicle_server.search import SearchRequest, run_search
from chronicle_server.settings_api import effective_ai_flags
from chronicle_server.settings_api import router as settings_router

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

    from chronicle_server.config import ChronicleSettings

logger = structlog.get_logger()

router = APIRouter(tags=["ask"])
# Settings surface is mounted here (app.py is outside the 5.3 allowlist).
router.include_router(settings_router)

_TAG_STRIP = re.compile(r"<[^>]+>")


class AskRequest(BaseModel):
    question: str
    scope: QueryScope = Field(default_factory=QueryScope)
    mode: Literal["scope"] = "scope"


def _sse_frame(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, default=str, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


def _load_source_plain(
    pool: ConnectionPool,
    card: dict[str, Any],
) -> tuple[str, str | None, str | None, str | None]:
    """Return (plain_text, date, sender, title) for a search result card."""
    sid = str(card["id"])
    result_type = card.get("result_type")
    date = card.get("date")
    sender = card.get("sender_name") or card.get("sender")
    title = card.get("subject") if result_type == "message" else card.get("filename")

    try:
        kind, key = decode_source_id(sid)
    except ValueError:
        # Fall back to snippet only
        return str(card.get("snippet") or ""), date, sender, title

    if kind == "msg" and isinstance(key, int):
        email_uuid = msg_key_to_uuid(key)
        with pool.connection() as conn:
            row = conn.execute(
                """
                SELECT body_text, body_html, subject, sender_name, date
                  FROM emails
                 WHERE id = %(id)s
                """,
                {"id": email_uuid},
            ).fetchone()
        if row is None:
            return str(card.get("snippet") or ""), date, sender, title
        body_text, body_html, subject, sname, d = row
        plain = plain_text_from_bodies(
            str(body_text) if body_text else None,
            str(body_html) if body_html else None,
        )
        return (
            plain or str(card.get("snippet") or ""),
            date or (d.isoformat() if d is not None and hasattr(d, "isoformat") else date),
            sender or sname,
            title or subject,
        )

    if kind == "att" and isinstance(key, int):
        with pool.connection() as conn:
            row = conn.execute(
                """
                SELECT ac.markdown, a.filename
                  FROM attachments a
                  LEFT JOIN attachment_contents ac ON ac.attachment_id = a.id
                 WHERE a.id = %(id)s
                """,
                {"id": key},
            ).fetchone()
        if row is None:
            return str(card.get("snippet") or ""), date, sender, title
        markdown, filename = row
        plain = ""
        if markdown:
            plain = _TAG_STRIP.sub(" ", str(markdown)).strip()
        if not plain:
            plain = str(card.get("snippet") or "")
        return plain, date, sender, title or filename

    return str(card.get("snippet") or ""), date, sender, title


def _cards_to_sources(
    pool: ConnectionPool,
    cards: list[dict[str, Any]],
) -> list[AskSource]:
    sources: list[AskSource] = []
    for i, card in enumerate(cards, start=1):
        marker = f"S{i}"
        plain, date, sender, title = _load_source_plain(pool, card)
        block, excerpt, location, excerpt_hash = prepare_source_text(plain)
        rtype = card.get("result_type") or "message"
        source_type = "attachment" if rtype == "attachment" else "message"
        sources.append(
            AskSource(
                marker=marker,
                source_id=str(card["id"]),
                source_type=source_type,
                date=date,
                sender=sender,
                title=title,
                plain_text=plain,
                block_text=block,
                excerpt=excerpt,
                location=location,
                excerpt_hash=excerpt_hash,
            )
        )
    return sources


def _persist_answer(
    pool: ConnectionPool,
    *,
    question: str,
    scope_fp: str,
    model_route: str,
    policy_version: str,
    status: str,
    answer_text: str | None,
    retrieval: list[dict[str, Any]],
    citations: list[dict[str, Any]] | None = None,
) -> UUID:
    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO app_answers (
                question, scope_fingerprint, model_route, policy_version,
                status, answer_text, retrieval
            ) VALUES (
                %(question)s, %(scope_fp)s, %(model_route)s, %(policy_version)s,
                %(status)s, %(answer_text)s, %(retrieval)s
            )
            RETURNING id
            """,
            {
                "question": question,
                "scope_fp": scope_fp,
                "model_route": model_route,
                "policy_version": policy_version,
                "status": status,
                "answer_text": answer_text,
                "retrieval": Jsonb(retrieval),
            },
        ).fetchone()
        assert row is not None
        answer_id: UUID = row[0]
        if citations:
            for cit in citations:
                conn.execute(
                    """
                    INSERT INTO app_citations (
                        answer_id, marker, source_id, source_type,
                        location, excerpt, excerpt_hash
                    ) VALUES (
                        %(answer_id)s, %(marker)s, %(source_id)s, %(source_type)s,
                        %(location)s, %(excerpt)s, %(excerpt_hash)s
                    )
                    """,
                    {
                        "answer_id": answer_id,
                        "marker": cit["marker"],
                        "source_id": cit["source_id"],
                        "source_type": cit["source_type"],
                        "location": Jsonb(cit.get("location")),
                        "excerpt": cit.get("excerpt"),
                        "excerpt_hash": cit.get("excerpt_hash"),
                    },
                )
        conn.commit()
    return answer_id


def _gateway_from_request(request: Request, settings: ChronicleSettings) -> ModelGateway:
    transport = getattr(request.app.state, "chat_transport", None)
    return ModelGateway(settings, transport)


def _event_stream(
    *,
    request: Request,
    body: AskRequest,
    username: str,
) -> Iterator[str]:
    settings: ChronicleSettings = request.app.state.settings
    pool: ConnectionPool = request.app.state.pool
    gateway = _gateway_from_request(request, settings)
    secret_key: str = settings.secret_key

    # 1. Hybrid retrieval (Task 2.1 pipeline)
    search_body = SearchRequest(
        query=body.question,
        mode="hybrid",
        scope=body.scope,
        limit=settings.ask_source_limit,
        cursor=None,
        include_facets=False,
    )
    try:
        search_resp = run_search(pool, search_body, secret_key)
    except Exception as exc:
        logger.warning("ask_retrieval_failed", error=str(exc))
        yield _sse_frame("error", {"message": "Retrieval failed"})
        return

    cards = list(search_resp.results)
    type_counts = {"message": 0, "attachment": 0}
    for c in cards:
        rt = c.get("result_type")
        if rt == "attachment":
            type_counts["attachment"] += 1
        else:
            type_counts["message"] += 1

    retrieval_meta = {
        "count": len(cards),
        "types": type_counts,
        "degraded": search_resp.degraded,
    }
    yield _sse_frame("retrieval", retrieval_meta)

    sources = _cards_to_sources(pool, cards)
    # Persistable retrieval list (ids + types, no full content)
    retrieval_rows = [
        {
            "source_id": s.source_id,
            "source_type": s.source_type,
            "marker": s.marker,
            "title": s.title,
            "date": s.date,
            "sender": s.sender,
            "snippet": s.excerpt,
        }
        for s in sources
    ]
    scope_fp = search_resp.scope_fingerprint or scope_fingerprint(body.scope)

    full_text_parts: list[str] = []
    try:
        for delta in gateway.stream(
            question=body.question,
            sources=sources,
            pool=pool,
            username=username,
        ):
            full_text_parts.append(delta)
            yield _sse_frame("token", {"text": delta})
    except Exception as exc:
        logger.warning("ask_model_error", error=str(exc))
        partial = "".join(full_text_parts)
        try:
            _persist_answer(
                pool,
                question=body.question,
                scope_fp=scope_fp,
                model_route=gateway.model_route,
                policy_version=gateway.policy_version,
                status="error",
                answer_text=partial or None,
                retrieval=retrieval_rows,
            )
        except Exception as persist_exc:
            logger.warning("ask_persist_error", error=str(persist_exc))
        yield _sse_frame("error", {"message": "Model generation failed"})
        return

    answer_text = "".join(full_text_parts)
    citations, unmatched = resolve_citations(answer_text, sources)

    for cit in citations:
        yield _sse_frame(
            "citation",
            {
                "marker": cit["marker"],
                "source_id": cit["source_id"],
                "source_type": cit["source_type"],
                "excerpt": cit["excerpt"],
                "location": cit["location"],
            },
        )

    try:
        answer_id = _persist_answer(
            pool,
            question=body.question,
            scope_fp=scope_fp,
            model_route=gateway.model_route,
            policy_version=gateway.policy_version,
            status="complete",
            answer_text=answer_text,
            retrieval=retrieval_rows,
            citations=citations,
        )
    except Exception as persist_exc:
        logger.warning("ask_persist_error", error=str(persist_exc))
        yield _sse_frame("error", {"message": "Failed to persist answer"})
        return

    generated_at = datetime.now(UTC).isoformat()
    yield _sse_frame(
        "done",
        {
            "answer_id": str(answer_id),
            "model_route": gateway.model_route,
            "policy_version": gateway.policy_version,
            "generated_at": generated_at,
            "unmatched_markers": unmatched,
        },
    )


@router.post("/ask", response_model=None)
def post_ask(
    body: AskRequest,
    request: Request,
    user: str = Depends(require_user),
) -> StreamingResponse | JSONResponse:
    """Grounded answer stream: retrieval → tokens → citations → done.

    When the model is unavailable or ask is disabled, returns JSON
    ``{"available": false, "reason": ...}`` (not SSE) so search stays usable.
    """
    settings: ChronicleSettings = request.app.state.settings
    pool: ConnectionPool = request.app.state.pool
    flags = effective_ai_flags(pool, settings)

    if not flags["ask_enabled"]:
        return JSONResponse(
            status_code=200,
            content={
                "available": False,
                "reason": "Ask is disabled",
            },
        )

    gateway = _gateway_from_request(request, settings)
    # Allow tests to force availability via app.state.model_available
    forced = getattr(request.app.state, "model_available", None)
    available = bool(forced) if forced is not None else gateway.availability()
    if not available:
        return JSONResponse(
            status_code=200,
            content={
                "available": False,
                "reason": "Model service unavailable",
            },
        )

    return StreamingResponse(
        _event_stream(request=request, body=body, username=user),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
