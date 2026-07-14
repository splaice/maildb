"""Workspaces v1: CRUD, notebook blocks, pins, notes, export (Phase 2 Task 2.6)."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field, ValidationError, model_validator

from chronicle_server.auth import require_fresh_auth, require_user
from chronicle_server.db import audit
from chronicle_server.ids import decode_source_id, msg_key_to_uuid
from chronicle_server.redact import redact_workspace_copy, scan_workspace_pii
from chronicle_server.scope import QueryScope

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

    from chronicle_server.config import ChronicleSettings

logger = structlog.get_logger()

router = APIRouter(tags=["workspaces"])

BlockType = Literal["heading", "note", "pin", "answer"]
ExportFormat = Literal["markdown", "json", "csv"]

_SAFE_FILENAME = re.compile(r"[^a-zA-Z0-9._-]+")


# --- content shapes ---


class HeadingContent(BaseModel):
    text: str


class NoteContent(BaseModel):
    text: str


class PinContent(BaseModel):
    source_id: str
    source_type: str
    title: str
    date: str | None = None
    sender: str | None = None
    excerpt: str | None = None


class AnswerContent(BaseModel):
    answer_id: UUID


# --- request bodies ---


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1)
    description: str | None = None
    scope: QueryScope = Field(default_factory=QueryScope)


class WorkspacePatch(BaseModel):
    version: int
    name: str | None = None
    description: str | None = None
    scope: QueryScope | None = None

    @model_validator(mode="after")
    def _at_least_one_field(self) -> WorkspacePatch:
        if self.name is None and self.description is None and self.scope is None:
            raise ValueError("at least one of name, description, scope required")
        return self


class BlockCreate(BaseModel):
    block_type: BlockType
    content: dict[str, Any] = Field(default_factory=dict)
    position: int | None = None


class BlockPatch(BaseModel):
    content: dict[str, Any] | None = None
    position: int | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> BlockPatch:
        if self.content is None and self.position is None:
            raise ValueError("at least one of content, position required")
        return self


class RedactOptions(BaseModel):
    """Export redaction controls (§15.4).

    When ``enabled`` and not ``confirmed``, the export endpoint returns a
    review payload (no file). When ``confirmed``, a redacted *copy* is
    generated — originals and workspace rows are never modified (SEC-003).
    """

    enabled: bool = False
    kinds: list[str] = Field(default_factory=list)
    custom_terms: list[str] = Field(default_factory=list)
    confirmed: bool = False


class ExportBody(BaseModel):
    format: ExportFormat = "markdown"
    redact: RedactOptions | None = None


# --- content validation ---


def validate_block_content(block_type: BlockType, content: dict[str, Any]) -> dict[str, Any]:
    """Validate content shape per block type; return JSON-serializable dict.

    Raises pydantic.ValidationError on bad shapes (FastAPI → 422).
    """
    if block_type == "heading":
        return HeadingContent.model_validate(content).model_dump()
    if block_type == "note":
        return NoteContent.model_validate(content).model_dump()
    if block_type == "pin":
        return PinContent.model_validate(content).model_dump()
    # answer
    parsed = AnswerContent.model_validate(content)
    return {"answer_id": str(parsed.answer_id)}


# --- helpers ---


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return dt.isoformat()
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def _scope_dict(scope: QueryScope | dict[str, Any] | None) -> dict[str, Any]:
    if scope is None:
        return {}
    if isinstance(scope, QueryScope):
        return scope.model_dump(mode="json", by_alias=True, exclude_none=True)
    return dict(scope)


def _scope_summary(scope: dict[str, Any]) -> str:
    parts: list[str] = []
    date = scope.get("date")
    if isinstance(date, dict):
        fr = date.get("from")
        to = date.get("to")
        if fr or to:
            parts.append(f"date {fr or '…'} → {to or '…'}")
    for key, label in (
        ("mailboxes", "mailboxes"),
        ("senders", "senders"),
        ("recipients", "recipients"),
        ("participants", "participants"),
        ("file_types", "file_types"),
        ("filenames", "filenames"),
        ("source_types", "source_types"),
    ):
        vals = scope.get(key)
        if vals:
            parts.append(f"{label}={','.join(str(v) for v in vals)}")
    if scope.get("subject_contains"):
        parts.append(f"subject~{scope['subject_contains']}")
    if scope.get("has_attachment") is not None:
        parts.append(f"has_attachment={scope['has_attachment']}")
    if scope.get("free_text"):
        parts.append(f"q={scope['free_text']}")
    return "; ".join(parts) if parts else "(full archive)"


def _safe_filename(name: str, ext: str) -> str:
    base = _SAFE_FILENAME.sub("_", name).strip("._")[:80] or "workspace"
    return f"{base}.{ext}"


def source_exists(pool: ConnectionPool, source_id: str) -> bool:
    """Return True if source_id decodes and the underlying row exists."""
    try:
        kind, key = decode_source_id(source_id)
    except ValueError:
        return False

    with pool.connection() as conn:
        if kind == "msg" and isinstance(key, int):
            row = conn.execute(
                "SELECT 1 FROM emails WHERE id = %(id)s",
                {"id": msg_key_to_uuid(key)},
            ).fetchone()
            return row is not None
        if kind == "att" and isinstance(key, int):
            row = conn.execute(
                "SELECT 1 FROM attachments WHERE id = %(id)s",
                {"id": key},
            ).fetchone()
            return row is not None
        if kind == "thr" and isinstance(key, str):
            row = conn.execute(
                "SELECT 1 FROM emails WHERE thread_id = %(tid)s LIMIT 1",
                {"tid": key},
            ).fetchone()
            return row is not None
    return False


def answer_exists(pool: ConnectionPool, answer_id: UUID) -> bool:
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM app_answers WHERE id = %(id)s",
            {"id": answer_id},
        ).fetchone()
    return row is not None


def _load_answer_hydration(pool: ConnectionPool, answer_id: UUID) -> dict[str, Any] | None:
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT id, question, answer_text, status, model_route, policy_version,
                   scope_fingerprint, created_at
              FROM app_answers
             WHERE id = %(id)s
            """,
            {"id": answer_id},
        ).fetchone()
        if row is None:
            return None
        citations = conn.execute(
            """
            SELECT marker, source_id, source_type, excerpt, excerpt_hash, location
              FROM app_citations
             WHERE answer_id = %(id)s
             ORDER BY created_at ASC
            """,
            {"id": answer_id},
        ).fetchall()
    return {
        "answer_id": str(row[0]),
        "question": row[1],
        "answer_text": row[2],
        "status": row[3],
        "model_route": row[4],
        "policy_version": row[5],
        "scope_fingerprint": row[6],
        "created_at": _iso(row[7]),
        "citations": [
            {
                "marker": c[0],
                "source_id": c[1],
                "source_type": c[2],
                "excerpt": c[3],
                "excerpt_hash": c[4],
                "location": c[5],
            }
            for c in citations
        ],
    }


def _block_row_to_dict(
    pool: ConnectionPool,
    *,
    bid: UUID,
    workspace_id: UUID,
    position: int,
    block_type: str,
    content: Any,
    created_at: Any,
    updated_at: Any,
    hydrate: bool = True,
) -> dict[str, Any]:
    content_dict = dict(content) if isinstance(content, dict) else {}
    out: dict[str, Any] = {
        "id": str(bid),
        "workspace_id": str(workspace_id),
        "position": position,
        "block_type": block_type,
        "content": content_dict,
        "created_at": _iso(created_at),
        "updated_at": _iso(updated_at),
    }
    if hydrate and block_type == "answer":
        aid_raw = content_dict.get("answer_id")
        if aid_raw:
            try:
                hydrated = _load_answer_hydration(pool, UUID(str(aid_raw)))
            except ValueError:
                hydrated = None
            if hydrated is not None:
                out["answer"] = hydrated
    return out


def _fetch_workspace_row(pool: ConnectionPool, workspace_id: UUID) -> dict[str, Any] | None:
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT id, name, description, scope, created_at, updated_at, version
              FROM app_workspaces
             WHERE id = %(id)s
            """,
            {"id": workspace_id},
        ).fetchone()
    if row is None:
        return None
    scope = row[3] if isinstance(row[3], dict) else {}
    return {
        "id": str(row[0]),
        "name": row[1],
        "description": row[2],
        "scope": scope,
        "created_at": _iso(row[4]),
        "updated_at": _iso(row[5]),
        "version": row[6],
    }


def _fetch_blocks(pool: ConnectionPool, workspace_id: UUID) -> list[dict[str, Any]]:
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT id, workspace_id, position, block_type, content, created_at, updated_at
              FROM app_workspace_blocks
             WHERE workspace_id = %(wid)s
             ORDER BY position ASC, created_at ASC
            """,
            {"wid": workspace_id},
        ).fetchall()
    return [
        _block_row_to_dict(
            pool,
            bid=r[0],
            workspace_id=r[1],
            position=r[2],
            block_type=r[3],
            content=r[4],
            created_at=r[5],
            updated_at=r[6],
            hydrate=True,
        )
        for r in rows
    ]


def _source_meta_from_db(pool: ConnectionPool, source_id: str) -> dict[str, Any] | None:
    """Best-effort metadata for manifest rows (type/date/sender/title/excerpt_hash)."""
    try:
        kind, key = decode_source_id(source_id)
    except ValueError:
        return None

    with pool.connection() as conn:
        if kind == "msg" and isinstance(key, int):
            row = conn.execute(
                """
                SELECT subject, sender_name, sender_address, date
                  FROM emails
                 WHERE id = %(id)s
                """,
                {"id": msg_key_to_uuid(key)},
            ).fetchone()
            if row is None:
                return None
            subject, sname, saddr, date = row
            return {
                "source_id": source_id,
                "source_type": "message",
                "date": _iso(date),
                "sender": sname or saddr,
                "subject_or_filename": subject,
                "excerpt_hash": None,
            }
        if kind == "att" and isinstance(key, int):
            row = conn.execute(
                """
                SELECT a.filename, e.sender_name, e.sender_address, e.date
                  FROM attachments a
                  LEFT JOIN email_attachments ea ON ea.attachment_id = a.id
                  LEFT JOIN emails e ON e.id = ea.email_id
                 WHERE a.id = %(id)s
                 ORDER BY e.date DESC NULLS LAST
                 LIMIT 1
                """,
                {"id": key},
            ).fetchone()
            if row is None:
                return None
            filename, sname, saddr, date = row
            return {
                "source_id": source_id,
                "source_type": "attachment",
                "date": _iso(date),
                "sender": sname or saddr,
                "subject_or_filename": filename,
                "excerpt_hash": None,
            }
        if kind == "thr" and isinstance(key, str):
            row = conn.execute(
                """
                SELECT subject, sender_name, sender_address, date
                  FROM emails
                 WHERE thread_id = %(tid)s
                 ORDER BY date ASC NULLS LAST
                 LIMIT 1
                """,
                {"tid": key},
            ).fetchone()
            if row is None:
                return None
            subject, sname, saddr, date = row
            return {
                "source_id": source_id,
                "source_type": "thread",
                "date": _iso(date),
                "sender": sname or saddr,
                "subject_or_filename": subject,
                "excerpt_hash": None,
            }
    return None


def _build_manifest(
    pool: ConnectionPool,
    blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deduplicated source rows from pins and answer citations."""
    by_id: dict[str, dict[str, Any]] = {}

    for block in blocks:
        btype = block["block_type"]
        content = block.get("content") or {}
        if btype == "pin":
            sid = content.get("source_id")
            if not sid:
                continue
            if sid not in by_id:
                meta = _source_meta_from_db(pool, str(sid)) or {}
                by_id[str(sid)] = {
                    "source_id": str(sid),
                    "source_type": content.get("source_type")
                    or meta.get("source_type")
                    or "message",
                    "date": content.get("date") or meta.get("date"),
                    "sender": content.get("sender") or meta.get("sender"),
                    "subject_or_filename": content.get("title") or meta.get("subject_or_filename"),
                    "title": content.get("title") or meta.get("subject_or_filename"),
                    "excerpt_hash": None,
                }
            # Prefer pin excerpt hash if we can hash the excerpt
            excerpt = content.get("excerpt")
            if excerpt and not by_id[str(sid)].get("excerpt_hash"):
                by_id[str(sid)]["excerpt_hash"] = hashlib.sha256(
                    str(excerpt).encode("utf-8")
                ).hexdigest()
        elif btype == "answer":
            answer = block.get("answer")
            citations = (answer or {}).get("citations") or []
            for cit in citations:
                sid = cit.get("source_id")
                if not sid:
                    continue
                sid = str(sid)
                if sid not in by_id:
                    meta = _source_meta_from_db(pool, sid) or {}
                    by_id[sid] = {
                        "source_id": sid,
                        "source_type": cit.get("source_type")
                        or meta.get("source_type")
                        or "message",
                        "date": meta.get("date"),
                        "sender": meta.get("sender"),
                        "subject_or_filename": meta.get("subject_or_filename"),
                        "title": meta.get("subject_or_filename"),
                        "excerpt_hash": cit.get("excerpt_hash"),
                    }
                elif cit.get("excerpt_hash") and not by_id[sid].get("excerpt_hash"):
                    by_id[sid]["excerpt_hash"] = cit.get("excerpt_hash")

    return list(by_id.values())


def _manifest_fingerprint(manifest: list[dict[str, Any]]) -> str:
    """Stable sha256 of canonical manifest JSON."""
    # Normalize keys order and drop None for stability
    normalized = []
    for row in manifest:
        item = {
            "source_id": row.get("source_id"),
            "source_type": row.get("source_type"),
            "date": row.get("date"),
            "sender": row.get("sender"),
            "subject_or_filename": row.get("subject_or_filename") or row.get("title"),
            "excerpt_hash": row.get("excerpt_hash"),
        }
        normalized.append(item)
    normalized.sort(key=lambda r: str(r.get("source_id") or ""))
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _render_markdown(
    workspace: dict[str, Any],
    blocks: list[dict[str, Any]],
    manifest: list[dict[str, Any]],
) -> str:
    lines: list[str] = []
    lines.append(f"# {workspace['name']}")
    if workspace.get("description"):
        lines.append("")
        lines.append(str(workspace["description"]))
    lines.append("")
    lines.append(f"Scope: {_scope_summary(workspace.get('scope') or {})}")
    lines.append("")

    for block in blocks:
        btype = block["block_type"]
        content = block.get("content") or {}
        if btype == "heading":
            lines.append(f"## {content.get('text') or ''}")
            lines.append("")
        elif btype == "note":
            lines.append(str(content.get("text") or ""))
            lines.append("")
        elif btype == "pin":
            title = content.get("title") or content.get("source_id") or ""
            sid = content.get("source_id") or ""
            date = content.get("date") or ""
            sender = content.get("sender") or ""
            lines.append(f"- [{title}] ({sid}) — {date} — {sender}")
            excerpt = content.get("excerpt")
            if excerpt:
                lines.append(f"> {excerpt}")
            lines.append("")
        elif btype == "answer":
            answer = block.get("answer") or {}
            text = answer.get("answer_text") or ""
            if text:
                lines.append(text)
                lines.append("")
            for cit in answer.get("citations") or []:
                marker = cit.get("marker") or ""
                # Normalize marker to [S#] style in legend
                m = marker if str(marker).startswith("[") else f"[{marker}]"
                lines.append(f"{m} {cit.get('source_id') or ''}")
            if answer.get("citations"):
                lines.append("")

    lines.append("## Source manifest")
    lines.append("")
    for row in manifest:
        lines.append(
            f"- {row.get('source_id')} ({row.get('source_type')}) "
            f"{row.get('date') or ''} "
            f"{row.get('sender') or ''} "
            f"{row.get('subject_or_filename') or row.get('title') or ''} "
            f"hash={row.get('excerpt_hash') or ''}".rstrip()
        )
    lines.append("")
    return "\n".join(lines)


def _render_csv(manifest: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["source_id", "type", "date", "sender", "title", "excerpt_hash"])
    for row in manifest:
        writer.writerow(
            [
                row.get("source_id") or "",
                row.get("source_type") or "",
                row.get("date") or "",
                row.get("sender") or "",
                row.get("subject_or_filename") or row.get("title") or "",
                row.get("excerpt_hash") or "",
            ]
        )
    return buf.getvalue()


# --- routes ---


@router.get("/workspaces")
def list_workspaces(
    request: Request,
    _user: str = Depends(require_user),
) -> dict[str, Any]:
    """List workspaces (id, name, counts, updated_at), newest first."""
    pool: ConnectionPool = request.app.state.pool
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT w.id, w.name, w.updated_at,
                   count(b.id) AS block_count,
                   count(b.id) FILTER (WHERE b.block_type = 'pin') AS pin_count,
                   count(b.id) FILTER (WHERE b.block_type = 'note') AS note_count,
                   count(b.id) FILTER (WHERE b.block_type = 'answer') AS answer_count,
                   count(b.id) FILTER (WHERE b.block_type = 'heading') AS heading_count
              FROM app_workspaces w
              LEFT JOIN app_workspace_blocks b ON b.workspace_id = w.id
             GROUP BY w.id, w.name, w.updated_at
             ORDER BY w.updated_at DESC
            """
        ).fetchall()
    items = [
        {
            "id": str(r[0]),
            "name": r[1],
            "updated_at": _iso(r[2]),
            "counts": {
                "blocks": int(r[3] or 0),
                "pins": int(r[4] or 0),
                "notes": int(r[5] or 0),
                "answers": int(r[6] or 0),
                "headings": int(r[7] or 0),
            },
        }
        for r in rows
    ]
    return {"items": items}


@router.post("/workspaces", status_code=201)
def create_workspace(
    body: WorkspaceCreate,
    request: Request,
    user: str = Depends(require_user),
) -> dict[str, Any]:
    pool: ConnectionPool = request.app.state.pool
    scope = _scope_dict(body.scope)
    with pool.connection() as conn:
        row = conn.execute(
            """
            INSERT INTO app_workspaces (name, description, scope)
            VALUES (%(name)s, %(description)s, %(scope)s)
            RETURNING id, name, description, scope, created_at, updated_at, version
            """,
            {
                "name": body.name,
                "description": body.description,
                "scope": Jsonb(scope),
            },
        ).fetchone()
        conn.commit()
    assert row is not None
    audit(
        pool,
        username=user,
        action="workspace_create",
        detail={"workspace_id": str(row[0]), "name": body.name},
    )
    return {
        "id": str(row[0]),
        "name": row[1],
        "description": row[2],
        "scope": row[3] if isinstance(row[3], dict) else scope,
        "created_at": _iso(row[4]),
        "updated_at": _iso(row[5]),
        "version": row[6],
        "blocks": [],
    }


@router.get("/workspaces/{workspace_id}")
def get_workspace(
    workspace_id: UUID,
    request: Request,
    _user: str = Depends(require_user),
) -> dict[str, Any]:
    pool: ConnectionPool = request.app.state.pool
    ws = _fetch_workspace_row(pool, workspace_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    blocks = _fetch_blocks(pool, workspace_id)
    return {**ws, "blocks": blocks}


@router.patch("/workspaces/{workspace_id}")
def patch_workspace(
    workspace_id: UUID,
    body: WorkspacePatch,
    request: Request,
    _user: str = Depends(require_user),
) -> dict[str, Any]:
    """Update name/description/scope with optimistic concurrency on version."""
    pool: ConnectionPool = request.app.state.pool
    sets: list[str] = ["version = version + 1", "updated_at = now()"]
    params: dict[str, Any] = {"id": workspace_id, "version": body.version}
    if body.name is not None:
        sets.append("name = %(name)s")
        params["name"] = body.name
    if body.description is not None:
        sets.append("description = %(description)s")
        params["description"] = body.description
    if body.scope is not None:
        sets.append("scope = %(scope)s")
        params["scope"] = Jsonb(_scope_dict(body.scope))

    with pool.connection() as conn:
        # Distinguish not-found vs version mismatch
        existing = conn.execute(
            "SELECT version FROM app_workspaces WHERE id = %(id)s",
            {"id": workspace_id},
        ).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="Workspace not found")
        row = conn.execute(
            f"""
            UPDATE app_workspaces
               SET {", ".join(sets)}
             WHERE id = %(id)s AND version = %(version)s
         RETURNING id, name, description, scope, created_at, updated_at, version
            """,
            params,
        ).fetchone()
        conn.commit()
    if row is None:
        raise HTTPException(status_code=409, detail="Version conflict")
    return {
        "id": str(row[0]),
        "name": row[1],
        "description": row[2],
        "scope": row[3] if isinstance(row[3], dict) else {},
        "created_at": _iso(row[4]),
        "updated_at": _iso(row[5]),
        "version": row[6],
    }


@router.delete("/workspaces/{workspace_id}", status_code=204)
def delete_workspace(
    workspace_id: UUID,
    request: Request,
    user: str = Depends(require_user),
) -> Response:
    pool: ConnectionPool = request.app.state.pool
    with pool.connection() as conn:
        row = conn.execute(
            "DELETE FROM app_workspaces WHERE id = %(id)s RETURNING id",
            {"id": workspace_id},
        ).fetchone()
        conn.commit()
    if row is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    audit(
        pool,
        username=user,
        action="workspace_delete",
        detail={"workspace_id": str(workspace_id)},
    )
    return Response(status_code=204)


@router.post("/workspaces/{workspace_id}/blocks", status_code=201)
def create_block(
    workspace_id: UUID,
    body: BlockCreate,
    request: Request,
    _user: str = Depends(require_user),
) -> dict[str, Any]:
    pool: ConnectionPool = request.app.state.pool
    try:
        content = validate_block_content(body.block_type, body.content)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    if body.block_type == "pin":
        sid = str(content["source_id"])
        if not source_exists(pool, sid):
            raise HTTPException(status_code=404, detail="Pin source not found")
    if body.block_type == "answer":
        aid = UUID(str(content["answer_id"]))
        if not answer_exists(pool, aid):
            raise HTTPException(status_code=404, detail="Answer not found")

    with pool.connection() as conn:
        ws = conn.execute(
            "SELECT 1 FROM app_workspaces WHERE id = %(id)s",
            {"id": workspace_id},
        ).fetchone()
        if ws is None:
            raise HTTPException(status_code=404, detail="Workspace not found")

        if body.position is None:
            pos_row = conn.execute(
                """
                SELECT COALESCE(MAX(position), -1) + 1
                  FROM app_workspace_blocks
                 WHERE workspace_id = %(wid)s
                """,
                {"wid": workspace_id},
            ).fetchone()
            position = int(pos_row[0]) if pos_row else 0
        else:
            position = body.position
            # Shift existing blocks at/after position
            conn.execute(
                """
                UPDATE app_workspace_blocks
                   SET position = position + 1, updated_at = now()
                 WHERE workspace_id = %(wid)s AND position >= %(pos)s
                """,
                {"wid": workspace_id, "pos": position},
            )

        row = conn.execute(
            """
            INSERT INTO app_workspace_blocks (workspace_id, position, block_type, content)
            VALUES (%(wid)s, %(pos)s, %(btype)s, %(content)s)
            RETURNING id, workspace_id, position, block_type, content, created_at, updated_at
            """,
            {
                "wid": workspace_id,
                "pos": position,
                "btype": body.block_type,
                "content": Jsonb(content),
            },
        ).fetchone()
        # Touch workspace updated_at
        conn.execute(
            "UPDATE app_workspaces SET updated_at = now() WHERE id = %(id)s",
            {"id": workspace_id},
        )
        conn.commit()

    assert row is not None
    return _block_row_to_dict(
        pool,
        bid=row[0],
        workspace_id=row[1],
        position=row[2],
        block_type=row[3],
        content=row[4],
        created_at=row[5],
        updated_at=row[6],
        hydrate=True,
    )


@router.patch("/workspaces/{workspace_id}/blocks/{block_id}")
def patch_block(
    workspace_id: UUID,
    block_id: UUID,
    body: BlockPatch,
    request: Request,
    _user: str = Depends(require_user),
) -> dict[str, Any]:
    pool: ConnectionPool = request.app.state.pool

    with pool.connection() as conn:
        existing = conn.execute(
            """
            SELECT id, workspace_id, position, block_type, content, created_at, updated_at
              FROM app_workspace_blocks
             WHERE id = %(bid)s AND workspace_id = %(wid)s
            """,
            {"bid": block_id, "wid": workspace_id},
        ).fetchone()
        if existing is None:
            raise HTTPException(status_code=404, detail="Block not found")

        old_pos = int(existing[2])
        raw_type = str(existing[3])
        if raw_type not in ("heading", "note", "pin", "answer"):
            raise HTTPException(status_code=500, detail="Invalid block type")
        block_type = cast(BlockType, raw_type)
        content = dict(existing[4]) if isinstance(existing[4], dict) else {}

        if body.content is not None:
            try:
                content = validate_block_content(block_type, body.content)
            except ValidationError as exc:
                raise HTTPException(status_code=422, detail=exc.errors()) from exc
            if block_type == "pin" and not source_exists(pool, str(content["source_id"])):
                raise HTTPException(status_code=404, detail="Pin source not found")
            if block_type == "answer" and not answer_exists(pool, UUID(str(content["answer_id"]))):
                raise HTTPException(status_code=404, detail="Answer not found")

        new_pos = body.position if body.position is not None else old_pos

        if new_pos != old_pos:
            if new_pos < old_pos:
                conn.execute(
                    """
                    UPDATE app_workspace_blocks
                       SET position = position + 1, updated_at = now()
                     WHERE workspace_id = %(wid)s
                       AND position >= %(new_pos)s
                       AND position < %(old_pos)s
                       AND id != %(bid)s
                    """,
                    {
                        "wid": workspace_id,
                        "new_pos": new_pos,
                        "old_pos": old_pos,
                        "bid": block_id,
                    },
                )
            else:
                conn.execute(
                    """
                    UPDATE app_workspace_blocks
                       SET position = position - 1, updated_at = now()
                     WHERE workspace_id = %(wid)s
                       AND position > %(old_pos)s
                       AND position <= %(new_pos)s
                       AND id != %(bid)s
                    """,
                    {
                        "wid": workspace_id,
                        "new_pos": new_pos,
                        "old_pos": old_pos,
                        "bid": block_id,
                    },
                )

        row = conn.execute(
            """
            UPDATE app_workspace_blocks
               SET content = %(content)s,
                   position = %(pos)s,
                   updated_at = now()
             WHERE id = %(bid)s
         RETURNING id, workspace_id, position, block_type, content, created_at, updated_at
            """,
            {
                "content": Jsonb(content),
                "pos": new_pos,
                "bid": block_id,
            },
        ).fetchone()
        conn.execute(
            "UPDATE app_workspaces SET updated_at = now() WHERE id = %(id)s",
            {"id": workspace_id},
        )
        conn.commit()

    assert row is not None
    return _block_row_to_dict(
        pool,
        bid=row[0],
        workspace_id=row[1],
        position=row[2],
        block_type=row[3],
        content=row[4],
        created_at=row[5],
        updated_at=row[6],
        hydrate=True,
    )


@router.delete("/workspaces/{workspace_id}/blocks/{block_id}", status_code=204)
def delete_block(
    workspace_id: UUID,
    block_id: UUID,
    request: Request,
    user: str = Depends(require_user),
) -> Response:
    pool: ConnectionPool = request.app.state.pool
    with pool.connection() as conn:
        row = conn.execute(
            """
            DELETE FROM app_workspace_blocks
             WHERE id = %(bid)s AND workspace_id = %(wid)s
         RETURNING id, position
            """,
            {"bid": block_id, "wid": workspace_id},
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Block not found")
        deleted_pos = int(row[1])
        conn.execute(
            """
            UPDATE app_workspace_blocks
               SET position = position - 1, updated_at = now()
             WHERE workspace_id = %(wid)s AND position > %(pos)s
            """,
            {"wid": workspace_id, "pos": deleted_pos},
        )
        conn.execute(
            "UPDATE app_workspaces SET updated_at = now() WHERE id = %(id)s",
            {"id": workspace_id},
        )
        conn.commit()
    audit(
        pool,
        username=user,
        action="workspace_block_delete",
        detail={"workspace_id": str(workspace_id), "block_id": str(block_id)},
    )
    return Response(status_code=204)


@router.post("/workspaces/{workspace_id}/export")
def export_workspace(
    workspace_id: UUID,
    request: Request,
    body: ExportBody | None = None,
    user: str = Depends(require_fresh_auth()),
) -> Response:
    """Generate a workspace export artifact (markdown / json / csv).

    Bulk export requires fresh authentication (§15.1). When redaction is
    enabled without confirmation, returns a review inventory (no file).
    Confirmed redaction produces a redacted *copy* only — original archive
    sources and workspace blocks are never overwritten (SEC-003 / §15.4).
    """
    pool: ConnectionPool = request.app.state.pool
    settings: ChronicleSettings = request.app.state.settings
    opts = body or ExportBody()
    fmt: ExportFormat = opts.format
    redact = opts.redact or RedactOptions()

    ws = _fetch_workspace_row(pool, workspace_id)
    if ws is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    blocks = _fetch_blocks(pool, workspace_id)

    kinds = redact.kinds or None
    custom_terms = redact.custom_terms or None

    # Review step: detection only, no file write (§15.4).
    if redact.enabled and not redact.confirmed:
        counts, samples, by_source = scan_workspace_pii(
            blocks,
            ws,
            kinds=kinds,
            custom_terms=custom_terms,
        )
        return JSONResponse(
            status_code=200,
            content={
                "review": True,
                "counts": counts,
                "samples": samples,
                "by_source": by_source,
                "format": fmt,
            },
        )

    redaction_counts: dict[str, int] = {}
    redactions_by_source: dict[str, int] = {}
    export_ws = ws
    export_blocks = blocks
    if redact.enabled and redact.confirmed:
        export_ws, export_blocks, redaction_counts, redactions_by_source = redact_workspace_copy(
            blocks,
            ws,
            kinds=kinds,
            custom_terms=custom_terms,
        )

    manifest = _build_manifest(pool, export_blocks)
    fingerprint = _manifest_fingerprint(manifest)
    generated_at = datetime.now(UTC).isoformat()

    if fmt == "markdown":
        rendered = _render_markdown(export_ws, export_blocks, manifest)
        media = "text/markdown; charset=utf-8"
        filename = _safe_filename(export_ws["name"], "md")
        payload: bytes = rendered.encode("utf-8")
    elif fmt == "csv":
        rendered = _render_csv(manifest)
        media = "text/csv; charset=utf-8"
        filename = _safe_filename(export_ws["name"], "csv")
        payload = rendered.encode("utf-8")
    else:
        export_doc: dict[str, Any] = {
            **export_ws,
            "blocks": export_blocks,
            "manifest": [
                {
                    "source_id": m.get("source_id"),
                    "source_type": m.get("source_type"),
                    "date": m.get("date"),
                    "sender": m.get("sender"),
                    "subject_or_filename": m.get("subject_or_filename") or m.get("title"),
                    "excerpt_hash": m.get("excerpt_hash"),
                }
                for m in manifest
            ],
            "export": {
                "generated_at": generated_at,
                "policy_versions": {"ask": settings.policy_version},
                "fingerprint": fingerprint,
                "redactions": redaction_counts,
                "redactions_by_source": redactions_by_source,
            },
        }
        if redaction_counts:
            export_doc["redactions"] = redaction_counts
        rendered = json.dumps(export_doc, default=str, indent=2)
        media = "application/json; charset=utf-8"
        filename = _safe_filename(export_ws["name"], "json")
        payload = rendered.encode("utf-8")

    audit(
        pool,
        username=user,
        action="workspace_export",
        detail={
            "workspace_id": str(workspace_id),
            "format": fmt,
            "source_count": len(manifest),
            "fingerprint": fingerprint,
            "redactions": redaction_counts,
            "redactions_by_source": redactions_by_source,
            "redact_enabled": redact.enabled,
        },
    )

    disposition = f'attachment; filename="{filename}"'
    return Response(
        content=payload,
        media_type=media,
        headers={
            "Content-Disposition": disposition,
            "X-Manifest-Fingerprint": fingerprint,
            "X-Source-Count": str(len(manifest)),
        },
    )
