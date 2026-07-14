# src/chronicle_server/config.py
from __future__ import annotations

from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings


class ChronicleSettings(BaseSettings):
    model_config = {
        "env_prefix": "CHRONICLE_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    database_url: str = "postgresql://localhost/maildb"
    secret_key: str
    password_hash: str
    username: str = "owner"
    session_max_age_s: int = 43200  # 12h
    cookie_secure: bool = True
    cookie_name: str = "chronicle_session"
    # Login rate limiting (fixed window; single-user app)
    login_max_failures: int = 5
    login_window_s: int = 900  # 15 min
    # Ask / model gateway (Phase 2 Task 2.4)
    answer_model: str = "llama3.2"
    ollama_host: str | None = None  # None → ollama client default
    ask_enabled: bool = True
    interpret_enabled: bool = True
    generate_enabled: bool = True
    ask_source_limit: int = 12
    policy_version: str = "ask-v1"
    # Displayed in settings / confirmation surfaces (§15.3 retention statement)
    retention_note: str = "Local Ollama route; prompts are not retained by an external provider."
    # Attachment binaries (mirrors maildb attachment_dir)
    attachment_root: str = "~/maildb/attachments"

    @model_validator(mode="after")
    def _expand_paths(self) -> ChronicleSettings:
        """Expand ~ in path settings at load."""
        self.attachment_root = str(Path(self.attachment_root).expanduser())
        return self
