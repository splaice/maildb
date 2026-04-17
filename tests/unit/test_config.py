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
    assert settings.database_url == "postgresql://maildb@localhost:5432/maildb"
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


def test_debug_log_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAILDB_DEBUG_LOG", raising=False)
    monkeypatch.delenv("MAILDB_DEBUG_LOG_LEVEL", raising=False)
    monkeypatch.delenv("MAILDB_DEBUG_LOG_MAX_BYTES", raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.debug_log.endswith(".maildb/debug.log")
    assert "~" not in settings.debug_log  # path should be expanded
    assert settings.debug_log_level == "DEBUG"
    assert settings.debug_log_max_bytes == 10_485_760


def test_debug_log_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAILDB_DEBUG_LOG", "/tmp/custom-debug.log")
    monkeypatch.setenv("MAILDB_DEBUG_LOG_LEVEL", "INFO")
    monkeypatch.setenv("MAILDB_DEBUG_LOG_MAX_BYTES", "5242880")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.debug_log == "/tmp/custom-debug.log"
    assert settings.debug_log_level == "INFO"
    assert settings.debug_log_max_bytes == 5_242_880


def test_user_emails_parses_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAILDB_USER_EMAILS", "a@example.com,b@example.com")
    monkeypatch.delenv("MAILDB_USER_EMAIL", raising=False)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.user_emails == ["a@example.com", "b@example.com"]


def test_legacy_user_email_merges_into_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAILDB_USER_EMAIL", "legacy@example.com")
    monkeypatch.delenv("MAILDB_USER_EMAILS", raising=False)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.user_emails == ["legacy@example.com"]


def test_legacy_user_email_prepended_when_not_already_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAILDB_USER_EMAIL", "legacy@example.com")
    monkeypatch.setenv("MAILDB_USER_EMAILS", "a@example.com,b@example.com")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.user_emails == ["legacy@example.com", "a@example.com", "b@example.com"]


def test_legacy_user_email_not_duplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAILDB_USER_EMAIL", "a@example.com")
    monkeypatch.setenv("MAILDB_USER_EMAILS", "a@example.com,b@example.com")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.user_emails == ["a@example.com", "b@example.com"]


def test_no_user_emails_is_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAILDB_USER_EMAIL", raising=False)
    monkeypatch.delenv("MAILDB_USER_EMAILS", raising=False)
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.user_emails == []
