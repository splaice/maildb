from __future__ import annotations

from maildb.pii import scrub_pii


def _event(event: str = "test", **kwargs: object) -> dict[str, object]:
    """Build a minimal structlog event dict."""
    return {"event": event, **kwargs}


class TestFieldBasedRedaction:
    def test_sensitive_key_password(self) -> None:
        result = scrub_pii(None, "debug", _event(password="hunter2"))  # noqa: S106
        assert result["password"] == "[REDACTED]"  # noqa: S105

    def test_sensitive_key_token(self) -> None:
        result = scrub_pii(None, "debug", _event(token="abc123xyz"))  # noqa: S106
        assert result["token"] == "[REDACTED]"  # noqa: S105

    def test_sensitive_key_api_key(self) -> None:
        result = scrub_pii(None, "debug", _event(api_key="sk-1234"))
        assert result["api_key"] == "[REDACTED]"

    def test_sensitive_key_authorization(self) -> None:
        result = scrub_pii(None, "debug", _event(authorization="Bearer xyz"))
        assert result["authorization"] == "[REDACTED]"

    def test_non_sensitive_key_preserved(self) -> None:
        result = scrub_pii(None, "debug", _event(tool="find"))
        assert result["tool"] == "find"


class TestRegexScrubbing:
    def test_email_in_value(self) -> None:
        result = scrub_pii(None, "debug", _event(sender="alice@example.com"))
        assert result["sender"] == "[REDACTED-EMAIL]"

    def test_email_in_event_message(self) -> None:
        result = scrub_pii(None, "debug", _event("query for alice@example.com"))
        assert "alice@example.com" not in result["event"]
        assert "[REDACTED-EMAIL]" in result["event"]

    def test_ssn_redacted(self) -> None:
        result = scrub_pii(None, "debug", _event(data="SSN is 123-45-6789"))
        assert "123-45-6789" not in result["data"]
        assert "[REDACTED-SSN]" in result["data"]

    def test_credit_card_redacted(self) -> None:
        # 4111111111111111 is a standard test Visa number (passes Luhn)
        result = scrub_pii(None, "debug", _event(data="card 4111111111111111"))
        assert "4111111111111111" not in result["data"]
        assert "[REDACTED-CC]" in result["data"]

    def test_phone_redacted(self) -> None:
        result = scrub_pii(None, "debug", _event(data="call 555-123-4567"))
        assert "555-123-4567" not in result["data"]
        assert "[REDACTED-PHONE]" in result["data"]

    def test_phone_no_dashes(self) -> None:
        result = scrub_pii(None, "debug", _event(data="call 5551234567"))
        assert "5551234567" not in result["data"]
        assert "[REDACTED-PHONE]" in result["data"]


class TestValueTruncation:
    def test_short_value_unchanged(self) -> None:
        result = scrub_pii(None, "debug", _event(sql="SELECT 1"))
        assert result["sql"] == "SELECT 1"

    def test_long_value_truncated(self) -> None:
        long_val = "x" * 200
        result = scrub_pii(None, "debug", _event(sql=long_val))
        assert len(result["sql"]) < 200
        assert result["sql"].endswith("...")

    def test_non_string_value_unchanged(self) -> None:
        result = scrub_pii(None, "debug", _event(rows=42))
        assert result["rows"] == 42


class TestCombined:
    def test_pii_scrubbed_before_truncation(self) -> None:
        """If a long string contains PII, PII is scrubbed first, then truncated."""
        long_val = "prefix " + "alice@example.com " * 20
        result = scrub_pii(None, "debug", _event(data=long_val))
        assert "alice@example.com" not in result["data"]

    def test_event_key_preserved(self) -> None:
        """The 'event' key is always present and scrubbed but never field-redacted."""
        result = scrub_pii(None, "debug", _event("hello alice@example.com"))
        assert "[REDACTED-EMAIL]" in result["event"]
