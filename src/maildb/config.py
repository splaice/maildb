# src/maildb/config.py
from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "MAILDB_", "env_file": ".env", "env_file_encoding": "utf-8"}

    database_url: str = "postgresql://localhost:5432/maildb"
    ollama_url: str = "http://localhost:11434"
    embedding_model: str = "nomic-embed-text"
    embedding_dimensions: int = 768
    user_email: str | None = None
    attachment_dir: str = "./attachments"
    ingest_chunk_size_mb: int = 50
    ingest_tmp_dir: str = "./ingest_tmp"
    ingest_workers: int = -1
    embed_workers: int = 4
    embed_batch_size: int = 50
