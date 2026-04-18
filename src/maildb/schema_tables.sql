CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS imports (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_account    TEXT NOT NULL,
    source_file       TEXT,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at      TIMESTAMPTZ,
    messages_total    INT NOT NULL DEFAULT 0,
    messages_inserted INT NOT NULL DEFAULT 0,
    messages_skipped  INT NOT NULL DEFAULT 0,
    status            TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'completed', 'failed'))
);

CREATE TABLE IF NOT EXISTS emails (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id      TEXT UNIQUE NOT NULL,
    thread_id       TEXT NOT NULL,
    source_account  TEXT,
    import_id       UUID REFERENCES imports(id),
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

-- For databases created before multi-account support, add the columns idempotently.
ALTER TABLE emails ADD COLUMN IF NOT EXISTS source_account TEXT;
ALTER TABLE emails ADD COLUMN IF NOT EXISTS import_id UUID REFERENCES imports(id);

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
    import_id             UUID REFERENCES imports(id),
    created_at            TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE ingest_tasks ADD COLUMN IF NOT EXISTS import_id UUID REFERENCES imports(id);

CREATE TABLE IF NOT EXISTS attachments (
    id              SERIAL PRIMARY KEY,
    sha256          TEXT NOT NULL,
    filename        TEXT NOT NULL,
    content_type    TEXT,
    size            BIGINT NOT NULL,
    storage_path    TEXT NOT NULL,
    reference_count INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (sha256)
);

ALTER TABLE attachments ADD COLUMN IF NOT EXISTS reference_count INT NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS email_attachments (
    email_id        UUID NOT NULL REFERENCES emails(id),
    attachment_id   INT NOT NULL REFERENCES attachments(id),
    filename        TEXT NOT NULL,
    PRIMARY KEY (email_id, attachment_id)
);

CREATE TABLE IF NOT EXISTS email_accounts (
    email_id       UUID NOT NULL REFERENCES emails(id) ON DELETE CASCADE,
    source_account TEXT NOT NULL,
    import_id      UUID NOT NULL REFERENCES imports(id),
    first_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (email_id, source_account)
);

CREATE TABLE IF NOT EXISTS attachment_contents (
    attachment_id     INT PRIMARY KEY REFERENCES attachments(id) ON DELETE CASCADE,
    status            TEXT NOT NULL
                      CHECK (status IN ('pending','extracting','extracted','failed','skipped')),
    markdown          TEXT,
    markdown_bytes    INT,
    reason            TEXT,
    extracted_at      TIMESTAMPTZ,
    extraction_ms     INT,
    extractor_version TEXT
);

CREATE TABLE IF NOT EXISTS attachment_chunks (
    id             BIGSERIAL PRIMARY KEY,
    attachment_id  INT NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
    chunk_index    INT NOT NULL,
    heading_path   TEXT,
    page_number    INT,
    token_count    INT NOT NULL,
    text           TEXT NOT NULL,
    embedding      vector(768),
    UNIQUE (attachment_id, chunk_index)
);
