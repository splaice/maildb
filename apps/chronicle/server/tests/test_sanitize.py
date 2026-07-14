# tests/test_sanitize.py
from __future__ import annotations

import nh3

from chronicle_server.sanitize import sanitize_email_html


def _re_clean(html: str) -> str:
    """Apply the same nh3 allowlist used by the sanitizer for idempotency checks."""
    from chronicle_server.sanitize import (
        _ALLOWED_ATTRIBUTES,
        _ALLOWED_TAGS,
        _URL_SCHEMES,
    )

    return nh3.clean(
        html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes=_URL_SCHEMES,
        link_rel="noopener noreferrer",
    )


def test_script_stripped() -> None:
    raw = "<p>Hello<script>alert(1)</script> world</p>"
    out = sanitize_email_html(raw)
    assert "<script" not in out["html"].lower()
    assert "alert" not in out["html"]
    assert out["had_active_content"] is True
    assert "Hello" in out["html"]


def test_onerror_img_gone() -> None:
    raw = '<p>x<img src="http://evil.test/t.gif" onerror="alert(1)">y</p>'
    out = sanitize_email_html(raw)
    assert "<img" not in out["html"].lower()
    assert "onerror" not in out["html"].lower()
    assert out["had_active_content"] is True
    assert out["remote_resources_blocked"] >= 1


def test_javascript_href_stripped() -> None:
    raw = '<a href="javascript:alert(1)">click</a>'
    out = sanitize_email_html(raw)
    assert "javascript:" not in out["html"].lower()
    assert "click" in out["html"]


def test_style_import_stripped() -> None:
    raw = "<style>@import url('http://evil.test/x.css');</style><p>ok</p>"
    out = sanitize_email_html(raw)
    assert "<style" not in out["html"].lower()
    assert "@import" not in out["html"].lower()
    assert "ok" in out["html"]
    assert out["remote_resources_blocked"] >= 1


def test_remote_img_blocked() -> None:
    raw = '<p><img src="http://tracker.test/pixel.gif"></p>'
    out = sanitize_email_html(raw)
    assert "<img" not in out["html"].lower()
    assert "tracker.test" not in out["html"]
    assert out["remote_resources_blocked"] >= 1


def test_form_stripped() -> None:
    raw = '<form action="http://evil.test"><input name="p"><button>go</button></form><p>body</p>'
    out = sanitize_email_html(raw)
    assert "<form" not in out["html"].lower()
    assert "<input" not in out["html"].lower()
    assert out["had_active_content"] is True
    assert "body" in out["html"]


def test_nested_quoted_html() -> None:
    raw = "<blockquote><p>Earlier: <b>yes</b> &amp; <i>no</i></p></blockquote><p>Reply</p>"
    out = sanitize_email_html(raw)
    assert "<blockquote>" in out["html"]
    assert "<b>yes</b>" in out["html"]
    assert "Reply" in out["html"]
    assert out["had_active_content"] is False


def test_svg_payload_gone() -> None:
    raw = '<svg onload="alert(1)"><script>alert(1)</script></svg><p>safe</p>'
    out = sanitize_email_html(raw)
    assert "<svg" not in out["html"].lower()
    assert "<script" not in out["html"].lower()
    assert out["had_active_content"] is True
    assert "safe" in out["html"]


def test_safe_link_preserved_with_rel() -> None:
    raw = '<a href="https://example.com/path" title="t" target="_blank">go</a>'
    out = sanitize_email_html(raw)
    assert 'href="https://example.com/path"' in out["html"]
    assert 'rel="noopener noreferrer"' in out["html"]
    assert "target" not in out["html"].lower()
    assert out["had_active_content"] is False


def test_mailto_allowed() -> None:
    raw = '<a href="mailto:a@example.com">mail</a>'
    out = sanitize_email_html(raw)
    assert "mailto:a@example.com" in out["html"]


def test_idempotent() -> None:
    raw = (
        '<p>Hi</p><script>x</script><img src="http://x/y">'
        '<a href="https://ok.test" target="_blank">ok</a>'
        '<div onclick="x">d</div>'
    )
    once = sanitize_email_html(raw)
    twice = sanitize_email_html(once["html"])
    assert once["html"] == twice["html"]
    # Property: output passes nh3.clean idempotently
    assert once["html"] == _re_clean(once["html"])


def test_structural_tags_kept() -> None:
    raw = (
        "<h1>T</h1><ul><li>a</li></ul>"
        "<table><thead><tr><th colspan='2'>H</th></tr></thead>"
        "<tbody><tr><td rowspan='1'>c</td></tr></tbody></table>"
        "<pre><code>x=1</code></pre><hr>"
    )
    out = sanitize_email_html(raw)
    for tag in ("h1", "ul", "li", "table", "th", "td", "pre", "code", "hr"):
        assert f"<{tag}" in out["html"] or f"<{tag}>" in out["html"] or tag == "hr"
    assert "colspan" in out["html"]
