CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS emails (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id      TEXT UNIQUE NOT NULL,
    thread_id       TEXT NOT NULL,
    subject         TEXT,
    sender_name     TEXT,
    sender_address  TEXT,
    sender_domain   TEXT,
    recipients      JSONB,
    date            TIMESTAMPTZ,
    body_text       TEXT,
    body_html       TEXT,
    has_attachment   BOOLEAN DEFAULT FALSE,
    attachments     JSONB,
    labels          TEXT[],
    in_reply_to     TEXT,
    "references"    TEXT[],
    embedding       vector(768),
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ingest_tasks (
    id                    SERIAL PRIMARY KEY,
    phase                 TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'pending',
    chunk_path            TEXT,
    worker_id             TEXT,
    started_at            TIMESTAMPTZ,
    completed_at          TIMESTAMPTZ,
    error_message         TEXT,
    retry_count           INT DEFAULT 0,
    messages_total        INT DEFAULT 0,
    messages_inserted     INT DEFAULT 0,
    messages_skipped      INT DEFAULT 0,
    attachments_extracted INT DEFAULT 0,
    created_at            TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS attachments (
    id              SERIAL PRIMARY KEY,
    sha256          TEXT NOT NULL,
    filename        TEXT NOT NULL,
    content_type    TEXT,
    size            BIGINT NOT NULL,
    storage_path    TEXT NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (sha256)
);

CREATE TABLE IF NOT EXISTS email_attachments (
    email_id        UUID NOT NULL REFERENCES emails(id),
    attachment_id   INT NOT NULL REFERENCES attachments(id),
    filename        TEXT NOT NULL,
    PRIMARY KEY (email_id, attachment_id)
);
