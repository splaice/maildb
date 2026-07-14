# src/chronicle_server/interpret.py
"""POST /api/query/interpret — NL → QueryScope proposal with origin-labeled chips.

Deterministic syntax parsing always runs; the model gateway optionally extracts
constraints from residual free text. The endpoint never fails because the model
is unavailable (Phase 2 Task 2.2; spec §5.2, RD-003).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from typing import TYPE_CHECKING, Any, Literal

import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from chronicle_server.auth import require_user
from chronicle_server.db import audit
from chronicle_server.gateway import ModelGateway
from chronicle_server.querysyntax import parse_query
from chronicle_server.scope import QueryScope
from chronicle_server.search import _is_provided
from chronicle_server.settings_api import effective_ai_flags

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

    from chronicle_server.config import ChronicleSettings

logger = structlog.get_logger()

router = APIRouter(tags=["query"])

# Fixed system policy for constraint extraction (spec §5.2 / task 2.2).
EXTRACT_SYSTEM_POLICY = (
    "extract search constraints; output ONLY a JSON object with optional keys: "
    "senders, recipients, participants (arrays of names/addresses), "
    "date_from, date_to (ISO dates; resolve phrases like 'around 2012' to a ±1y range), "
    "file_types (array), has_attachment (bool), residual_text (string) "
    "— no other keys, no prose"
)

_MODEL_WHITELIST = frozenset(
    {
        "senders",
        "recipients",
        "participants",
        "date_from",
        "date_to",
        "file_types",
        "has_attachment",
        "residual_text",
    }
)

_MIN_FREE_WORDS = 3
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

ChipOrigin = Literal["syntax", "model"]


class InterpretRequest(BaseModel):
    text: str = ""
    scope: QueryScope = Field(default_factory=QueryScope)


class InterpretChip(BaseModel):
    kind: str
    value: str
    origin: ChipOrigin
    display: str | None = None


class InterpretResponse(BaseModel):
    scope: dict[str, Any]
    free_text: str
    chips: list[InterpretChip]
    model_used: bool


def _gateway_from_request(request: Request, settings: ChronicleSettings) -> ModelGateway:
    transport = getattr(request.app.state, "chat_transport", None)
    return ModelGateway(settings, transport)


def _model_available(request: Request, gateway: ModelGateway) -> bool:
    forced = getattr(request.app.state, "model_available", None)
    if forced is not None:
        return bool(forced)
    return gateway.availability()


def _word_count(text: str) -> int:
    return len([w for w in text.split() if w])


def _is_email_like(value: str) -> bool:
    return bool(_EMAIL_RE.match(value.strip()))


def _parse_iso_date(raw: str) -> str | None:
    raw = raw.strip()
    if len(raw) < 10:
        return None
    try:
        date.fromisoformat(raw[:10])
    except ValueError:
        return None
    return raw[:10]


def _largest_json_object(text: str) -> str | None:
    """Return the largest balanced ``{...}`` substring, or None."""
    best: str | None = None
    depth = 0
    start: int | None = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    candidate = text[start : i + 1]
                    if best is None or len(candidate) > len(best):
                        best = candidate
    return best


def _coerce_str_list(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    out: list[str] = []
    for item in value:
        if item is None:
            continue
        if isinstance(item, str):
            s = item.strip()
            if s:
                out.append(s)
        elif isinstance(item, (int, float, bool)):
            out.append(str(item))
        else:
            continue
    return out


def validate_model_extraction(raw: Any) -> dict[str, Any] | None:
    """Validate model JSON against the whitelist. Returns None on any failure."""
    if not isinstance(raw, dict):
        return None
    result: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in _MODEL_WHITELIST:
            continue  # drop unknown keys
        if key in ("senders", "recipients", "participants", "file_types"):
            coerced = _coerce_str_list(value)
            if coerced is None:
                continue
            if coerced:
                result[key] = coerced
        elif key in ("date_from", "date_to"):
            if not isinstance(value, str):
                continue
            d = _parse_iso_date(value)
            if d is None:
                # Bad date → treat whole extraction as failed per defensive policy
                # for invalid date types; skip individual bad dates only when
                # format is wrong — task says "dates validated"; drop the key.
                continue
            result[key] = d
        elif key == "has_attachment":
            if isinstance(value, bool):
                result[key] = value
            elif value in (0, 1, "true", "false", "True", "False", "yes", "no"):
                result[key] = value in (1, "true", "True", "yes")
            else:
                continue
        elif key == "residual_text":
            if isinstance(value, str):
                result[key] = value
            else:
                continue
    return result


def parse_model_response(content: str) -> dict[str, Any] | None:
    """Extract and validate model JSON. None on any parse/validation failure."""
    if not content or not content.strip():
        return None
    block = _largest_json_object(content)
    if block is None:
        return None
    try:
        raw = json.loads(block)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return validate_model_extraction(raw)


def _complete_chat(
    gateway: ModelGateway,
    messages: list[dict[str, str]],
) -> str:
    """One non-streaming completion: collect all transport deltas."""
    settings = gateway._settings  # noqa: SLF001 — intentional reuse of gateway wiring
    transport = gateway._transport  # noqa: SLF001
    parts: list[str] = []
    for delta in transport(settings.answer_model, messages, False):
        if delta:
            parts.append(str(delta))
    return "".join(parts)


def _syntax_scope_updates(parsed_updates: dict[str, Any]) -> dict[str, Any]:
    """Strip free_text from parser updates (residual is handled separately)."""
    out = dict(parsed_updates)
    out.pop("free_text", None)
    return out


def _model_to_scope_updates(
    extracted: dict[str, Any],
    *,
    resolved_people: dict[str, list[str]],
) -> dict[str, Any]:
    """Map validated model fields + resolved addresses into scope_updates.

    ``resolved_people`` maps role → list of resolved email addresses that should
    be applied (unresolved names are omitted).
    """
    updates: dict[str, Any] = {}
    for role in ("senders", "recipients", "participants"):
        addrs = list(resolved_people.get(role, []))
        if addrs:
            updates[role] = addrs

    date_obj: dict[str, str] = {}
    if "date_from" in extracted:
        date_obj["from"] = extracted["date_from"]
    if "date_to" in extracted:
        date_obj["to"] = extracted["date_to"]
    if date_obj:
        updates["date"] = date_obj

    if "file_types" in extracted and extracted["file_types"]:
        updates["file_types"] = list(extracted["file_types"])

    if "has_attachment" in extracted:
        updates["has_attachment"] = extracted["has_attachment"]

    return updates


def resolve_person_names_with_display(
    pool: ConnectionPool,
    extracted: dict[str, Any],
) -> tuple[dict[str, list[tuple[str, str | None]]], list[InterpretChip]]:
    """Like resolve_person_names but keeps (address, display_name) pairs."""
    from maildb import MailDB

    db = MailDB._from_pool(pool)
    resolved: dict[str, list[tuple[str, str | None]]] = {
        "senders": [],
        "recipients": [],
        "participants": [],
    }
    unresolved_chips: list[InterpretChip] = []
    seen_unresolved: set[str] = set()

    for role in ("senders", "recipients", "participants"):
        values = extracted.get(role) or []
        if not isinstance(values, list):
            continue
        for raw in values:
            if not isinstance(raw, str) or not raw.strip():
                continue
            name_or_addr = raw.strip()
            if _is_email_like(name_or_addr):
                resolved[role].append((name_or_addr, None))
                continue

            try:
                contacts, _ = db.contacts_search(query=name_or_addr, limit=3)
            except Exception as exc:
                logger.debug("contacts_search_failed", error=str(exc), name=name_or_addr)
                contacts = []

            matches_with_addrs = [
                c
                for c in contacts
                if isinstance(c, dict) and c.get("addresses") and len(c.get("addresses") or []) > 0
            ]

            if len(matches_with_addrs) == 1:
                contact = matches_with_addrs[0]
                addrs = list(contact["addresses"])
                primary = str(addrs[0])
                display = contact.get("display_name")
                display_s = str(display) if display else name_or_addr
                resolved[role].append((primary, display_s))
            else:
                key = name_or_addr.lower()
                if key not in seen_unresolved:
                    seen_unresolved.add(key)
                    unresolved_chips.append(
                        InterpretChip(
                            kind="unresolved_person",
                            value=name_or_addr,
                            origin="model",
                            display=name_or_addr,
                        )
                    )

    return resolved, unresolved_chips


def merge_interpret_scope(
    request_scope: QueryScope,
    model_updates: dict[str, Any],
    syntax_updates: dict[str, Any],
) -> QueryScope:
    """Merge with priority: syntax > model > request scope (per field)."""
    try:
        from_model = QueryScope.model_validate(model_updates) if model_updates else QueryScope()
    except Exception:
        from_model = QueryScope()
    try:
        from_syntax = QueryScope.model_validate(syntax_updates) if syntax_updates else QueryScope()
    except Exception:
        from_syntax = QueryScope()

    req = request_scope.model_dump(mode="python", by_alias=True)
    mod = from_model.model_dump(mode="python", by_alias=True)
    syn = from_syntax.model_dump(mode="python", by_alias=True)

    merged: dict[str, Any] = {}
    for key in set(req) | set(mod) | set(syn):
        sv = syn.get(key)
        mv = mod.get(key)
        rv = req.get(key)
        if _is_provided(sv):
            merged[key] = sv
        elif _is_provided(mv):
            merged[key] = mv
        elif _is_provided(rv):
            merged[key] = rv
        else:
            merged[key] = sv if sv is not None else (mv if mv is not None else rv)
    return QueryScope.model_validate(merged)


def _field_origin(
    key: str,
    syntax_updates: dict[str, Any],
    model_updates: dict[str, Any],
) -> ChipOrigin | None:
    """Return origin that won for *key*, or None if neither provided it."""
    syn_scope: dict[str, Any] = {}
    mod_scope: dict[str, Any] = {}
    try:
        if syntax_updates:
            syn_scope = QueryScope.model_validate(syntax_updates).model_dump(
                mode="python", by_alias=True
            )
    except Exception:
        pass
    try:
        if model_updates:
            mod_scope = QueryScope.model_validate(model_updates).model_dump(
                mode="python", by_alias=True
            )
    except Exception:
        pass
    if _is_provided(syn_scope.get(key)):
        return "syntax"
    if _is_provided(mod_scope.get(key)):
        return "model"
    return None


def _build_chips(
    *,
    final_scope: QueryScope,
    syntax_updates: dict[str, Any],
    model_updates: dict[str, Any],
    unsupported: list[str],
    unresolved: list[InterpretChip],
    display_by_addr: dict[str, str],
) -> list[InterpretChip]:
    """Chips for the final proposal with origins (syntax/model only)."""
    chips: list[InterpretChip] = []

    # People lists
    for field, kind in (
        ("senders", "sender"),
        ("recipients", "recipient"),
        ("participants", "participant"),
    ):
        origin = _field_origin(field, syntax_updates, model_updates)
        if origin is None:
            continue
        values = getattr(final_scope, field) or []
        for v in values:
            chip = InterpretChip(kind=kind, value=v, origin=origin)
            if origin == "model" and v in display_by_addr:
                chip = InterpretChip(kind=kind, value=v, origin=origin, display=display_by_addr[v])
            chips.append(chip)

    # Date
    origin = _field_origin("date", syntax_updates, model_updates)
    if origin is not None and final_scope.date is not None:
        d = final_scope.date
        from_s = d.from_ or ""
        to_s = d.to or ""
        if from_s or to_s:
            chips.append(
                InterpretChip(
                    kind="date",
                    value=f"{from_s}..{to_s}",
                    origin=origin,
                )
            )

    # Scalars / lists with origins
    origin = _field_origin("subject_contains", syntax_updates, model_updates)
    if origin is not None and final_scope.subject_contains:
        chips.append(
            InterpretChip(
                kind="subject",
                value=final_scope.subject_contains,
                origin=origin,
            )
        )

    origin = _field_origin("has_attachment", syntax_updates, model_updates)
    if origin is not None and final_scope.has_attachment is not None:
        chips.append(
            InterpretChip(
                kind="has_attachment",
                value="true" if final_scope.has_attachment else "false",
                origin=origin,
            )
        )

    for field, kind in (
        ("mailboxes", "mailbox"),
        ("file_types", "file_type"),
        ("filenames", "filename"),
        ("source_types", "source_type"),
    ):
        origin = _field_origin(field, syntax_updates, model_updates)
        if origin is None:
            continue
        for v in getattr(final_scope, field) or []:
            chips.append(InterpretChip(kind=kind, value=v, origin=origin))

    for token in unsupported:
        chips.append(InterpretChip(kind="unsupported", value=token, origin="syntax"))

    chips.extend(unresolved)
    return chips


def run_interpret(
    pool: ConnectionPool,
    body: InterpretRequest,
    *,
    request: Request | None = None,
    settings: ChronicleSettings | None = None,
    gateway: ModelGateway | None = None,
    model_available: bool | None = None,
) -> InterpretResponse:
    """Core interpret pipeline (testable without HTTP)."""
    text = body.text if isinstance(body.text, str) else ""
    parsed = parse_query(text)
    syntax_updates = _syntax_scope_updates(parsed.scope_updates)
    free_text = parsed.free_text or ""

    model_updates: dict[str, Any] = {}
    model_used = False
    unresolved: list[InterpretChip] = []
    display_by_addr: dict[str, str] = {}
    residual_from_model: str | None = None

    use_model = (
        model_available is True
        and gateway is not None
        and _word_count(free_text) >= _MIN_FREE_WORDS
    )

    if use_model:
        assert gateway is not None
        messages = [
            {"role": "system", "content": EXTRACT_SYSTEM_POLICY},
            {"role": "user", "content": free_text},
        ]
        try:
            content = _complete_chat(gateway, messages)
            extracted = parse_model_response(content)
            # Empty after validation ≡ model returned nothing useful.
            if extracted is not None and len(extracted) > 0:
                model_used = True
                residual_from_model = extracted.get("residual_text")
                if isinstance(residual_from_model, str):
                    residual_from_model = residual_from_model.strip()
                else:
                    residual_from_model = None

                resolved_pairs, unresolved = resolve_person_names_with_display(pool, extracted)
                for _role, pairs in resolved_pairs.items():
                    for addr, disp in pairs:
                        if disp:
                            display_by_addr[addr] = disp
                model_updates = _model_to_scope_updates(
                    extracted,
                    resolved_people={
                        role: [addr for addr, _ in pairs] for role, pairs in resolved_pairs.items()
                    },
                )
            else:
                logger.debug(
                    "interpret_model_parse_failed",
                    preview=(content or "")[:200],
                )
                model_used = False
        except Exception as exc:
            logger.debug("interpret_model_call_failed", error=str(exc))
            model_used = False
            model_updates = {}
            unresolved = []

    final_scope = merge_interpret_scope(body.scope, model_updates, syntax_updates)

    # free_text: model residual when used, else syntax residual
    out_free = residual_from_model if model_used and residual_from_model is not None else free_text

    # Apply free_text onto scope for the proposal (search uses it as query)
    scope_dump = final_scope.model_dump(mode="json", by_alias=True, exclude_none=True)
    if out_free:
        scope_dump["free_text"] = out_free
    else:
        scope_dump.pop("free_text", None)

    chips = _build_chips(
        final_scope=final_scope,
        syntax_updates=syntax_updates,
        model_updates=model_updates,
        unsupported=list(parsed.unsupported),
        unresolved=unresolved,
        display_by_addr=display_by_addr,
    )

    return InterpretResponse(
        scope=scope_dump,
        free_text=out_free,
        chips=chips,
        model_used=model_used,
    )


@router.post("/query/interpret", response_model=InterpretResponse)
def post_interpret(
    body: InterpretRequest,
    request: Request,
    user: str = Depends(require_user),
) -> InterpretResponse:
    """Convert natural language into a proposed QueryScope with origin chips.

    Never 5xx from model issues. Syntax always wins over model on field conflicts.
    """
    settings: ChronicleSettings = request.app.state.settings
    pool: ConnectionPool = request.app.state.pool
    gateway = _gateway_from_request(request, settings)
    flags = effective_ai_flags(pool, settings)
    # Per-action disable → syntax/parse-only (model never called).
    available = flags["interpret_enabled"] and _model_available(request, gateway)

    text = body.text if isinstance(body.text, str) else ""
    text_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()

    try:
        result = run_interpret(
            pool,
            body,
            request=request,
            settings=settings,
            gateway=gateway,
            model_available=available,
        )
    except Exception as exc:
        # Last-resort: still return syntax-only rather than 5xx
        logger.warning("interpret_unexpected_error", error=str(exc))
        parsed = parse_query(text)
        syntax_updates = _syntax_scope_updates(parsed.scope_updates)
        final_scope = merge_interpret_scope(body.scope, {}, syntax_updates)
        free_text = parsed.free_text or ""
        scope_dump = final_scope.model_dump(mode="json", by_alias=True, exclude_none=True)
        if free_text:
            scope_dump["free_text"] = free_text
        chips = _build_chips(
            final_scope=final_scope,
            syntax_updates=syntax_updates,
            model_updates={},
            unsupported=list(parsed.unsupported),
            unresolved=[],
            display_by_addr={},
        )
        result = InterpretResponse(
            scope=scope_dump,
            free_text=free_text,
            chips=chips,
            model_used=False,
        )

    try:
        audit(
            pool,
            username=user,
            action="interpret",
            detail={
                "model_used": result.model_used,
                "text_sha256": text_sha,
            },
        )
    except Exception as exc:
        logger.debug("interpret_audit_failed", error=str(exc))

    return result
