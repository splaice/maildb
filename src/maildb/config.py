# src/maildb/config.py
from __future__ import annotations

from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {
        "env_prefix": "MAILDB_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    database_url: str = "postgresql://maildb@localhost:5432/maildb"
    ollama_url: str = "http://localhost:11434"
    embedding_model: str = "nomic-embed-text"
    embedding_dimensions: int = 768
    user_email: str | None = None
    attachment_dir: str = "~/maildb/attachments"
    ingest_chunk_size_mb: int = 50
    ingest_tmp_dir: str = "/tmp/maildb-ingest-tmp-dir"  # noqa: S108
    ingest_workers: int = -1
    embed_workers: int = 4
    embed_batch_size: int = 50

    # Debug logging
    debug_log: str = "~/.maildb/debug.log"
    debug_log_level: str = "DEBUG"
    debug_log_max_bytes: int = 10_485_760  # 10MB

    @model_validator(mode="after")
    def _expand_paths(self) -> Settings:
        """Expand ~ and resolve relative paths for directory settings."""
        self.attachment_dir = str(Path(self.attachment_dir).expanduser())
        self.ingest_tmp_dir = str(Path(self.ingest_tmp_dir).expanduser())
        self.debug_log = str(Path(self.debug_log).expanduser())
        return self
