"""PII scrubbing structlog processor."""

from __future__ import annotations

import re
from collections.abc import Mapping, MutableMapping
from typing import Any

# --- Field-based redaction ---

SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "authorization",
        "auth",
        "credential",
        "ssn",
        "credit_card",
        "card_number",
        "phone",
        "address",
        "first_name",
        "last_name",
    }
)

REDACTED = "[REDACTED]"

# --- Regex-based scrubbing ---

_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CC_RE = re.compile(r"\b\d{13,19}\b")
_PHONE_RE = re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b")

MAX_VALUE_LENGTH = 100


def _luhn_check(digits: str) -> bool:
    """Validate a digit string with the Luhn algorithm."""
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _redact_cc(match: re.Match[str]) -> str:
    """Replace credit card numbers that pass Luhn validation."""
    digits = match.group()
    if _luhn_check(digits):
        return "[REDACTED-CC]"
    return digits


def _scrub_value(value: str) -> str:
    """Apply regex-based PII scrubbing to a string value."""
    value = _EMAIL_RE.sub("[REDACTED-EMAIL]", value)
    value = _SSN_RE.sub("[REDACTED-SSN]", value)
    value = _CC_RE.sub(_redact_cc, value)
    return _PHONE_RE.sub("[REDACTED-PHONE]", value)


def _truncate(value: str) -> str:
    """Truncate strings over MAX_VALUE_LENGTH."""
    if len(value) > MAX_VALUE_LENGTH:
        return value[:MAX_VALUE_LENGTH] + "..."
    return value


def _scrub_field(key: Any, value: Any) -> Any:
    """Scrub a value, redacting it outright when its key is sensitive."""
    if isinstance(key, str) and key.lower() in SENSITIVE_KEYS:
        return REDACTED
    return _scrub_nested_value(value)


def _scrub_mapping(mapping: Mapping[Any, Any]) -> dict[Any, Any]:
    """Scrub every value in a mapping, applying sensitive-key redaction."""
    return {key: _scrub_field(key, value) for key, value in mapping.items()}


def _scrub_nested_value(value: Any) -> Any:
    """Scrub PII recursively while preserving scalar values."""
    if isinstance(value, str):
        return _truncate(_scrub_value(value))
    if isinstance(value, Mapping):
        return _scrub_mapping(value)
    if isinstance(value, list):
        return [_scrub_nested_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_scrub_nested_value(item) for item in value)
    return value


def scrub_pii(
    logger: Any,
    method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """structlog processor: redact PII, then truncate long values."""
    for key in list(event_dict.keys()):
        event_dict[key] = _scrub_field(key, event_dict[key])

    return event_dict
