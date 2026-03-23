# tests/unit/test_config.py
from __future__ import annotations

from typing import TYPE_CHECKING

from maildb.config import Settings

if TYPE_CHECKING:
    import pytest


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAILDB_DATABASE_URL", raising=False)
    monkeypatch.delenv("MAILDB_OLLAMA_URL", raising=False)
    monkeypatch.delenv("MAILDB_USER_EMAIL", raising=False)
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
    )
    assert settings.database_url == "postgresql://localhost:5432/maildb"
    assert settings.ollama_url == "http://localhost:11434"
    assert settings.embedding_model == "nomic-embed-text"
    assert settings.embedding_dimensions == 768
    assert settings.user_email is None


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAILDB_DATABASE_URL", "postgresql://custom:5432/mydb")
    monkeypatch.setenv("MAILDB_USER_EMAIL", "me@example.com")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.database_url == "postgresql://custom:5432/mydb"
    assert settings.user_email == "me@example.com"
