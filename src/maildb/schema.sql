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

CREATE INDEX IF NOT EXISTS idx_email_sender_address ON emails (sender_address);
CREATE INDEX IF NOT EXISTS idx_email_sender_domain ON emails (sender_domain);
CREATE INDEX IF NOT EXISTS idx_email_date ON emails (date);
CREATE INDEX IF NOT EXISTS idx_email_thread_id ON emails (thread_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_email_message_id ON emails (message_id);
CREATE INDEX IF NOT EXISTS idx_email_in_reply_to ON emails (in_reply_to);
CREATE INDEX IF NOT EXISTS idx_email_has_attachment ON emails (has_attachment) WHERE has_attachment = TRUE;
CREATE INDEX IF NOT EXISTS idx_email_labels ON emails USING GIN (labels);
CREATE INDEX IF NOT EXISTS idx_email_recipients ON emails USING GIN (recipients);
CREATE INDEX IF NOT EXISTS idx_email_embedding ON emails USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
