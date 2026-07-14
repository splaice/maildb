"""GET/PUT /api/settings — app preference document (Phase 5 Task 5.3).

Whitelisted groups with shallow-merge PATCH semantics. AI per-action flags
gate gateway-consuming endpoints; session max-age is read when stored.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from chronicle_server.auth import require_user
from chronicle_server.db import audit

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

    from chronicle_server.config import ChronicleSettings

logger = structlog.get_logger()

router = APIRouter(tags=["settings"])

SESSION_MAX_AGE_MIN = 900
SESSION_MAX_AGE_MAX = 86400

SearchMode = Literal["hybrid", "exact", "semantic"]
_VALID_MODES = frozenset({"hybrid", "exact", "semantic"})
_VALID_TOP_KEYS = frozenset({"ai", "privacy", "search", "chronicle"})

# Writable AI fields (answer_model is display-only from env)
_AI_WRITABLE = frozenset(
    {
        "ask_enabled",
        "interpret_enabled",
        "generate_enabled",
        "retention_note",
    }
)
_PRIVACY_WRITABLE = frozenset({"session_max_age_s"})
_SEARCH_WRITABLE = frozenset({"default_mode"})
_CHRONICLE_WRITABLE = frozenset({"default_lanes"})


class SettingsDocument(BaseModel):
    """Full settings document returned by GET (defaults filled)."""

    ai: dict[str, Any] = Field(default_factory=dict)
    privacy: dict[str, Any] = Field(default_factory=dict)
    search: dict[str, Any] = Field(default_factory=dict)
    chronicle: dict[str, Any] = Field(default_factory=dict)


def defaults_document(settings: ChronicleSettings) -> dict[str, Any]:
    """Build the full document from env defaults (no DB)."""
    return {
        "ai": {
            "ask_enabled": bool(settings.ask_enabled),
            "interpret_enabled": bool(settings.interpret_enabled),
            "generate_enabled": bool(settings.generate_enabled),
            "answer_model": settings.answer_model,
            "retention_note": settings.retention_note,
        },
        "privacy": {
            "session_max_age_s": int(settings.session_max_age_s),
        },
        "search": {
            "default_mode": "hybrid",
        },
        "chronicle": {
            "default_lanes": ["messages", "attachments", "top_people"],
        },
    }


def _load_row(pool: ConnectionPool, key: str) -> Any | None:
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT value FROM app_settings WHERE key = %(key)s",
            {"key": key},
        ).fetchone()
    if row is None:
        return None
    return row[0]


def load_stored_groups(pool: ConnectionPool) -> dict[str, Any]:
    """Load all known top-level groups from app_settings (missing → absent)."""
    out: dict[str, Any] = {}
    for key in _VALID_TOP_KEYS:
        val = _load_row(pool, key)
        if isinstance(val, dict):
            out[key] = val
    return out


def merge_document(
    settings: ChronicleSettings,
    stored: dict[str, Any],
) -> dict[str, Any]:
    """Shallow-merge stored groups over env defaults; answer_model always from env."""
    base = defaults_document(settings)
    for group, stored_val in stored.items():
        if group not in base or not isinstance(stored_val, dict):
            continue
        merged = {**base[group], **stored_val}
        if group == "ai":
            # Display-only: never trust a stored answer_model override
            merged["answer_model"] = settings.answer_model
        base[group] = merged
    return base


def read_document(pool: ConnectionPool, settings: ChronicleSettings) -> dict[str, Any]:
    return merge_document(settings, load_stored_groups(pool))


def apply_session_max_age(settings: ChronicleSettings, document: dict[str, Any]) -> None:
    """Mutate in-memory settings so auth cookie max-age tracks the stored value."""
    privacy = document.get("privacy") or {}
    raw = privacy.get("session_max_age_s")
    if raw is None:
        return
    try:
        age = int(raw)
    except (TypeError, ValueError):
        return
    if SESSION_MAX_AGE_MIN <= age <= SESSION_MAX_AGE_MAX:
        settings.session_max_age_s = age


def effective_ai_flags(
    pool: ConnectionPool,
    settings: ChronicleSettings,
) -> dict[str, bool]:
    """Per-action AI flags: stored app_settings override env defaults."""
    try:
        doc = read_document(pool, settings)
    except Exception as exc:
        logger.debug("settings_ai_flags_fallback", error=str(exc))
        return {
            "ask_enabled": bool(settings.ask_enabled),
            "interpret_enabled": bool(settings.interpret_enabled),
            "generate_enabled": bool(settings.generate_enabled),
        }
    apply_session_max_age(settings, doc)
    ai = doc.get("ai") or {}
    return {
        "ask_enabled": bool(ai.get("ask_enabled", settings.ask_enabled)),
        "interpret_enabled": bool(ai.get("interpret_enabled", settings.interpret_enabled)),
        "generate_enabled": bool(ai.get("generate_enabled", settings.generate_enabled)),
    }


def _upsert_group(pool: ConnectionPool, key: str, value: dict[str, Any]) -> None:
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (%(key)s, %(value)s, now())
            ON CONFLICT (key) DO UPDATE
               SET value = EXCLUDED.value,
                   updated_at = now()
            """,
            {"key": key, "value": Jsonb(value)},
        )
        conn.commit()


def validate_and_merge_patch(
    settings: ChronicleSettings,
    stored: dict[str, Any],
    patch: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Validate a shallow patch; return (new_stored_groups, changed_top_keys).

    Raises HTTPException 422 on unknown keys or invalid values.
    """
    if not isinstance(patch, dict):
        raise HTTPException(status_code=422, detail="body must be an object")

    unknown = [k for k in patch if k not in _VALID_TOP_KEYS]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail={"error": "unknown keys", "keys": sorted(unknown)},
        )

    base = merge_document(settings, stored)
    new_stored = {k: dict(v) for k, v in stored.items() if isinstance(v, dict)}
    changed: list[str] = []

    for group, group_patch in patch.items():
        if not isinstance(group_patch, dict):
            raise HTTPException(
                status_code=422,
                detail={"error": f"{group} must be an object"},
            )

        if group == "ai":
            unknown_fields = [
                k for k in group_patch if k not in _AI_WRITABLE and k != "answer_model"
            ]
            if unknown_fields:
                raise HTTPException(
                    status_code=422,
                    detail={"error": "unknown keys", "keys": sorted(unknown_fields)},
                )
            # answer_model is read-only display — ignore if sent
            writable = {k: v for k, v in group_patch.items() if k in _AI_WRITABLE}
            for flag in ("ask_enabled", "interpret_enabled", "generate_enabled"):
                if flag in writable and not isinstance(writable[flag], bool):
                    raise HTTPException(
                        status_code=422,
                        detail={"error": f"ai.{flag} must be a boolean"},
                    )
            if "retention_note" in writable and not isinstance(writable["retention_note"], str):
                raise HTTPException(
                    status_code=422,
                    detail={"error": "ai.retention_note must be a string"},
                )
            current = dict(base["ai"])
            # Drop display-only before persist
            current.pop("answer_model", None)
            current.update(writable)
            # Persist without answer_model
            persist = {k: v for k, v in current.items() if k != "answer_model"}
            prev = new_stored.get("ai") or {}
            if persist != prev:
                new_stored["ai"] = persist
                changed.append("ai")

        elif group == "privacy":
            unknown_fields = [k for k in group_patch if k not in _PRIVACY_WRITABLE]
            if unknown_fields:
                raise HTTPException(
                    status_code=422,
                    detail={"error": "unknown keys", "keys": sorted(unknown_fields)},
                )
            if "session_max_age_s" in group_patch:
                try:
                    age = int(group_patch["session_max_age_s"])
                except (TypeError, ValueError) as exc:
                    raise HTTPException(
                        status_code=422,
                        detail={"error": "privacy.session_max_age_s must be an integer"},
                    ) from exc
                if age < SESSION_MAX_AGE_MIN or age > SESSION_MAX_AGE_MAX:
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "error": (
                                f"privacy.session_max_age_s must be between "
                                f"{SESSION_MAX_AGE_MIN} and {SESSION_MAX_AGE_MAX}"
                            )
                        },
                    )
                persist = {"session_max_age_s": age}
                prev = new_stored.get("privacy") or {}
                if persist != prev:
                    new_stored["privacy"] = persist
                    changed.append("privacy")

        elif group == "search":
            unknown_fields = [k for k in group_patch if k not in _SEARCH_WRITABLE]
            if unknown_fields:
                raise HTTPException(
                    status_code=422,
                    detail={"error": "unknown keys", "keys": sorted(unknown_fields)},
                )
            if "default_mode" in group_patch:
                mode = group_patch["default_mode"]
                if mode not in _VALID_MODES:
                    raise HTTPException(
                        status_code=422,
                        detail={"error": "search.default_mode must be hybrid, exact, or semantic"},
                    )
                persist = {"default_mode": mode}
                prev = new_stored.get("search") or {}
                if persist != prev:
                    new_stored["search"] = persist
                    changed.append("search")

        elif group == "chronicle":
            unknown_fields = [k for k in group_patch if k not in _CHRONICLE_WRITABLE]
            if unknown_fields:
                raise HTTPException(
                    status_code=422,
                    detail={"error": "unknown keys", "keys": sorted(unknown_fields)},
                )
            if "default_lanes" in group_patch:
                lanes = group_patch["default_lanes"]
                if not isinstance(lanes, list) or not all(isinstance(x, str) for x in lanes):
                    raise HTTPException(
                        status_code=422,
                        detail={"error": "chronicle.default_lanes must be a string array"},
                    )
                persist = {"default_lanes": list(lanes)}
                prev = new_stored.get("chronicle") or {}
                if persist != prev:
                    new_stored["chronicle"] = persist
                    changed.append("chronicle")

    return new_stored, changed


@router.get("/settings")
def get_settings(
    request: Request,
    user: str = Depends(require_user),
) -> dict[str, Any]:
    """Return the full settings document (defaults + stored shallow merge)."""
    _ = user
    settings: ChronicleSettings = request.app.state.settings
    pool: ConnectionPool = request.app.state.pool
    try:
        doc = read_document(pool, settings)
    except Exception as exc:
        # Stub pools / missing table → defaults only
        logger.debug("settings_read_fallback", error=str(exc))
        doc = defaults_document(settings)
    apply_session_max_age(settings, doc)
    return doc


@router.put("/settings")
def put_settings(
    body: dict[str, Any],
    request: Request,
    user: str = Depends(require_user),
) -> dict[str, Any]:
    """Shallow-merge patch of whitelisted groups; audit changed keys (not values)."""
    settings: ChronicleSettings = request.app.state.settings
    pool: ConnectionPool = request.app.state.pool

    try:
        stored = load_stored_groups(pool)
    except Exception:
        stored = {}

    new_stored, changed = validate_and_merge_patch(settings, stored, body)

    for key in changed:
        _upsert_group(pool, key, new_stored[key])

    if changed:
        try:
            audit(
                pool,
                username=user,
                action="settings_update",
                detail={"changed_keys": changed},
            )
        except Exception as exc:
            logger.debug("settings_audit_failed", error=str(exc))

    doc = merge_document(settings, new_stored)
    apply_session_max_age(settings, doc)
    return doc
