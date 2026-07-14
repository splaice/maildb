# src/chronicle_server/sanitize.py
"""Server-side HTML sanitization for email bodies (spec §9.3)."""

from __future__ import annotations

import re
from typing import TypedDict

import nh3

# Structural/text tags only — no img, style, svg, form, script, iframe, object.
_ALLOWED_TAGS: set[str] = {
    "p",
    "br",
    "div",
    "span",
    "blockquote",
    "pre",
    "code",
    "ul",
    "ol",
    "li",
    "table",
    "thead",
    "tbody",
    "tr",
    "td",
    "th",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "b",
    "strong",
    "i",
    "em",
    "u",
    "a",
    "hr",
}

_ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    "a": {"href", "title"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}

_URL_SCHEMES: set[str] = {"http", "https", "mailto"}

# Active content probes (case-insensitive) on raw HTML.
_ACTIVE_TAG_RE = re.compile(
    r"<\s*(?:script|iframe|object|embed|form)\b",
    re.IGNORECASE,
)
_ON_HANDLER_RE = re.compile(r"\bon\w+\s*=", re.IGNORECASE)

# Remote resource references used for the blocked-count approximation.
_REMOTE_REF_RE = re.compile(r"src\s*=|background\s*=|url\s*\(", re.IGNORECASE)

# rel is set natively by nh3 (link_rel); target/rel are not in the attribute
# allowlist, so nh3 strips any inbound values before link_rel applies.


class SanitizedBody(TypedDict):
    html: str
    remote_resources_blocked: int
    had_active_content: bool


def _count_remote_refs(html: str) -> int:
    return len(_REMOTE_REF_RE.findall(html))


def _had_active_content(html: str) -> bool:
    return bool(_ACTIVE_TAG_RE.search(html) or _ON_HANDLER_RE.search(html))


def _nh3_clean(html: str) -> str:
    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes=_URL_SCHEMES,
        link_rel="noopener noreferrer",
    )


def sanitize_email_html(html: str) -> SanitizedBody:
    """Sanitize email HTML; never return raw markup.

    Pure function, no I/O. Output is idempotent under re-clean.
    """
    before = _count_remote_refs(html)
    had_active = _had_active_content(html)
    cleaned = _nh3_clean(html)
    after = _count_remote_refs(cleaned)
    blocked = max(0, before - after)
    return {
        "html": cleaned,
        "remote_resources_blocked": blocked,
        "had_active_content": had_active,
    }
