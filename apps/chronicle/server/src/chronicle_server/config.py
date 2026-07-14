# src/chronicle_server/config.py
from __future__ import annotations

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
