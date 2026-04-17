CREATE INDEX IF NOT EXISTS idx_email_sender_address ON emails (sender_address);
CREATE INDEX IF NOT EXISTS idx_email_sender_domain ON emails (sender_domain);
CREATE INDEX IF NOT EXISTS idx_email_date ON emails (date);
CREATE INDEX IF NOT EXISTS idx_email_thread_id ON emails (thread_id);
CREATE INDEX IF NOT EXISTS idx_email_in_reply_to ON emails (in_reply_to);
CREATE INDEX IF NOT EXISTS idx_email_has_attachment ON emails (has_attachment) WHERE has_attachment = TRUE;
CREATE INDEX IF NOT EXISTS idx_email_labels ON emails USING GIN (labels);
CREATE INDEX IF NOT EXISTS idx_email_recipients ON emails USING GIN (recipients);
CREATE INDEX IF NOT EXISTS idx_email_thread_sender_date ON emails (thread_id, sender_address, date);
CREATE INDEX IF NOT EXISTS idx_email_attachments_email_id ON email_attachments (email_id);
CREATE INDEX IF NOT EXISTS idx_email_attachments_attachment_id ON email_attachments (attachment_id);
CREATE INDEX IF NOT EXISTS idx_email_source_account ON emails (source_account);
CREATE INDEX IF NOT EXISTS idx_email_import_id ON emails (import_id);
CREATE INDEX IF NOT EXISTS idx_imports_source_account ON imports (source_account);
CREATE INDEX IF NOT EXISTS idx_imports_started_at ON imports (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_email_accounts_source_account ON email_accounts (source_account);
CREATE INDEX IF NOT EXISTS idx_email_accounts_import_id ON email_accounts (import_id);
-- HNSW index created separately after embed phase:
-- CREATE INDEX IF NOT EXISTS idx_email_embedding ON emails USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
