# src/chronicle_server/db.py
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

if TYPE_CHECKING:
    from chronicle_server.config import ChronicleSettings

logger = structlog.get_logger()

_APP_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS app_users (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username   TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS app_audit (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    username   TEXT NOT NULL,
    action     TEXT NOT NULL,
    detail     JSONB NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS app_answers (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    question          TEXT NOT NULL,
    scope_fingerprint TEXT NOT NULL,
    model_route       TEXT NOT NULL,
    policy_version    TEXT NOT NULL,
    status            TEXT NOT NULL CHECK (status IN ('complete','error','cancelled')),
    answer_text       TEXT,
    retrieval         JSONB NOT NULL DEFAULT '[]',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS app_citations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    answer_id   UUID NOT NULL REFERENCES app_answers(id) ON DELETE CASCADE,
    marker      TEXT NOT NULL,
    source_id   TEXT NOT NULL,
    source_type TEXT NOT NULL,
    location    JSONB,
    excerpt     TEXT,
    excerpt_hash TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS app_workspaces (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    description TEXT,
    scope       JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    version     INT NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS app_workspace_blocks (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES app_workspaces(id) ON DELETE CASCADE,
    position     INT NOT NULL,
    block_type   TEXT NOT NULL CHECK (block_type IN ('heading','note','pin','answer')),
    content      JSONB NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS app_events (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title          TEXT NOT NULL,
    time_start     TIMESTAMPTZ NOT NULL,
    time_end       TIMESTAMPTZ,
    time_precision TEXT NOT NULL DEFAULT 'day'
        CHECK (time_precision IN (
            'year','quarter','month','week','day','hour')),
    origin         TEXT NOT NULL
        CHECK (origin IN ('source','imported','automatic','analyst')),
    event_type     TEXT NOT NULL DEFAULT 'communication'
        CHECK (event_type IN (
            'decision','meeting','travel','purchase','deadline',
            'transition','document','communication','user_defined')),
    status         TEXT NOT NULL DEFAULT 'unreviewed'
        CHECK (status IN (
            'unreviewed','confirmed','edited','dismissed',
            'superseded','unresolved')),
    evidence_strength TEXT
        CHECK (evidence_strength IN ('low','medium','high')),
    scope_fingerprint TEXT,
    current_version   INT NOT NULL DEFAULT 1,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS app_event_versions (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id     UUID NOT NULL REFERENCES app_events(id) ON DELETE CASCADE,
    version      INT NOT NULL,
    author       TEXT NOT NULL CHECK (author IN ('automatic','analyst')),
    title        TEXT NOT NULL,
    summary      TEXT,
    derivation   JSONB NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (event_id, version)
);
CREATE TABLE IF NOT EXISTS app_event_claims (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id   UUID NOT NULL REFERENCES app_events(id) ON DELETE CASCADE,
    version    INT NOT NULL,
    position   INT NOT NULL,
    text       TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'direct'
        CHECK (status IN (
            'direct','supported','conflicting','unresolved')),
    citations  JSONB NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS app_topics (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    label       TEXT NOT NULL,
    description TEXT,
    origin      TEXT NOT NULL DEFAULT 'automatic'
                    CHECK (origin IN ('automatic','curated','manual')),
    parent_id   UUID REFERENCES app_topics(id),
    hidden      BOOLEAN NOT NULL DEFAULT FALSE,
    centroid    vector(768),
    top_terms   TEXT[] NOT NULL DEFAULT '{}',
    generation  INT NOT NULL DEFAULT 1,
    member_count INT NOT NULL DEFAULT 0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS app_topic_members (
    topic_id   UUID NOT NULL REFERENCES app_topics(id) ON DELETE CASCADE,
    email_id   UUID NOT NULL,
    distance   REAL,
    origin     TEXT NOT NULL DEFAULT 'automatic' CHECK (origin IN ('automatic','manual')),
    PRIMARY KEY (topic_id, email_id)
);
CREATE INDEX IF NOT EXISTS app_topic_members_email_idx ON app_topic_members (email_id);
CREATE TABLE IF NOT EXISTS app_settings (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS app_cache (
    key          TEXT PRIMARY KEY,
    data_version TEXT NOT NULL,
    value        JSONB NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def create_pool(settings: ChronicleSettings) -> ConnectionPool:
    """Create a psycopg3 connection pool from settings (open, autocommit off)."""
    return ConnectionPool(
        conninfo=settings.database_url,
        min_size=1,
        max_size=5,
        open=True,
        kwargs={"autocommit": False},
    )


def init_app_tables(pool: ConnectionPool) -> None:
    """Idempotent CREATE TABLE IF NOT EXISTS for app_users and app_audit."""
    with pool.connection() as conn:
        conn.execute(_APP_TABLES_SQL)
        conn.commit()
    logger.info("app_tables_initialized")


def ensure_user(pool: ConnectionPool, username: str) -> None:
    """Upsert the configured single-user username into app_users."""
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO app_users (username)
            VALUES (%(username)s)
            ON CONFLICT (username) DO NOTHING
            """,
            {"username": username},
        )
        conn.commit()
    logger.info("app_user_ensured", username=username)


def audit(
    pool: ConnectionPool,
    *,
    username: str,
    action: str,
    detail: dict[str, Any] | None = None,
) -> None:
    """Insert one row into app_audit."""
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO app_audit (username, action, detail)
            VALUES (%(username)s, %(action)s, %(detail)s)
            """,
            {
                "username": username,
                "action": action,
                "detail": Jsonb(detail if detail is not None else {}),
            },
        )
        conn.commit()


def update_last_login(pool: ConnectionPool, username: str) -> None:
    """Set last_login = now() for the given app_users row."""
    with pool.connection() as conn:
        conn.execute(
            """
            UPDATE app_users
               SET last_login = now()
             WHERE username = %(username)s
            """,
            {"username": username},
        )
        conn.commit()
