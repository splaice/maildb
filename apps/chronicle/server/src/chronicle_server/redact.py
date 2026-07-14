"""Export redaction pipeline (spec §15.4, SEC-003).

Pure detection and replacement helpers. Exports are always *generated*
artifacts — redacted copies never overwrite original sources or workspace
blocks (SEC-003).
"""

from __future__ import annotations

import re
from typing import Any, TypedDict

# Built-in kinds when enabled with empty kinds list.
DEFAULT_PII_KINDS: tuple[str, ...] = (
    "email",
    "phone",
    "street_address",
    "account_number",
)

_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
)

# International-ish phone: starts with + or digit, then digits/separators, ends digit.
_PHONE_RE = re.compile(
    r"(?<!\w)[\+\d][\d\s\-().]{6,}\d(?!\w)",
)

_STREET_RE = re.compile(
    r"\b\d+\s+\w+\s+(?:St|Ave|Rd|Blvd|Lane|Dr|Court|Way)\b",
    re.IGNORECASE,
)

# 8–17 digit runs; years alone are 4 digits and won't match.
_ACCOUNT_RE = re.compile(r"\b\d{8,17}\b")

# YYYYMMDD / YYYYMMDD-like dates that must not count as account numbers.
_YYYYMMDD_RE = re.compile(r"^(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])$")


class PiiMatch(TypedDict):
    kind: str
    value: str
    start: int
    end: int


def _phone_digit_count(value: str) -> int:
    return sum(1 for c in value if c.isdigit())


def _is_plausible_phone(value: str) -> bool:
    """Reject short / date-like / account-like runs that match the phone skeleton."""
    digits = _phone_digit_count(value)
    if digits < 7 or digits > 15:
        return False
    # Pure digit runs of 8+ (no separators) are account numbers, not phones.
    if value.isdigit() and digits >= 8:
        return False
    # Pure 8-digit YYYYMMDD is not a phone.
    compact = re.sub(r"\D", "", value)
    return not _YYYYMMDD_RE.match(compact)


def _is_plausible_account(value: str, text: str, start: int, end: int) -> bool:
    """False-positive guards: dates / years context must not match as accounts."""
    if _YYYYMMDD_RE.match(value):
        return False
    # Adjacent to date separators (ISO / slash dates around the span).
    # Note: use membership in a set of single chars — ``"" in "-/"`` is True in Python.
    before = text[max(0, start - 1) : start]
    after = text[end : min(len(text), end + 1)]
    return before not in {"-", "/"} and after not in {"-", "/"}


def detect_pii(
    text: str,
    *,
    kinds: list[str] | None = None,
    custom_terms: list[str] | None = None,
) -> list[PiiMatch]:
    """Detect PII spans in *text*.

    Returns list of ``{kind, value, start, end}`` (end exclusive).
    Overlapping matches are resolved left-to-right, longer first.
    """
    if not text:
        return []

    active = list(kinds) if kinds else list(DEFAULT_PII_KINDS)
    raw: list[PiiMatch] = []

    if "email" in active:
        for m in _EMAIL_RE.finditer(text):
            raw.append(
                {
                    "kind": "email",
                    "value": m.group(0),
                    "start": m.start(),
                    "end": m.end(),
                }
            )

    if "phone" in active:
        for m in _PHONE_RE.finditer(text):
            val = m.group(0)
            if _is_plausible_phone(val):
                raw.append(
                    {
                        "kind": "phone",
                        "value": val,
                        "start": m.start(),
                        "end": m.end(),
                    }
                )

    if "street_address" in active:
        for m in _STREET_RE.finditer(text):
            raw.append(
                {
                    "kind": "street_address",
                    "value": m.group(0),
                    "start": m.start(),
                    "end": m.end(),
                }
            )

    if "account_number" in active:
        for m in _ACCOUNT_RE.finditer(text):
            val = m.group(0)
            if _is_plausible_account(val, text, m.start(), m.end()):
                raw.append(
                    {
                        "kind": "account_number",
                        "value": val,
                        "start": m.start(),
                        "end": m.end(),
                    }
                )

    for term in custom_terms or []:
        if not term:
            continue
        # Literal case-insensitive match for user-defined terms.
        for m in re.finditer(re.escape(term), text, flags=re.IGNORECASE):
            raw.append(
                {
                    "kind": "custom",
                    "value": m.group(0),
                    "start": m.start(),
                    "end": m.end(),
                }
            )

    return _resolve_overlaps(raw)


def _resolve_overlaps(matches: list[PiiMatch]) -> list[PiiMatch]:
    """Keep non-overlapping matches; prefer earlier then longer spans."""
    if not matches:
        return []
    ordered = sorted(matches, key=lambda m: (m["start"], -(m["end"] - m["start"])))
    kept: list[PiiMatch] = []
    cursor = -1
    for m in ordered:
        if m["start"] < cursor:
            continue
        kept.append(m)
        cursor = m["end"]
    return kept


def apply_redactions(text: str, matches: list[PiiMatch]) -> str:
    """Replace each match with ``[REDACTED:kind]`` (right-to-left)."""
    if not matches:
        return text
    out = text
    for m in sorted(matches, key=lambda x: x["start"], reverse=True):
        out = out[: m["start"]] + f"[REDACTED:{m['kind']}]" + out[m["end"] :]
    return out


def redact_text(
    text: str,
    *,
    kinds: list[str] | None = None,
    custom_terms: list[str] | None = None,
) -> tuple[str, list[PiiMatch]]:
    """Detect and replace; returns (redacted_text, matches)."""
    matches = detect_pii(text, kinds=kinds, custom_terms=custom_terms)
    return apply_redactions(text, matches), matches


def count_by_kind(matches: list[PiiMatch]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for m in matches:
        counts[m["kind"]] = counts.get(m["kind"], 0) + 1
    return counts


def sample_matches(
    text: str,
    matches: list[PiiMatch],
    *,
    limit: int = 50,
    context: int = 40,
) -> list[dict[str, Any]]:
    """Up to *limit* sample rows with surrounding context for review UI."""
    samples: list[dict[str, Any]] = []
    for m in matches[:limit]:
        lo = max(0, m["start"] - context)
        hi = min(len(text), m["end"] + context)
        samples.append(
            {
                "kind": m["kind"],
                "value": m["value"],
                "start": m["start"],
                "end": m["end"],
                "context": text[lo:hi],
            }
        )
    return samples


def collect_export_text_fields(
    blocks: list[dict[str, Any]],
    workspace: dict[str, Any],
) -> list[tuple[str, str]]:
    """Yield (source_key, text) pairs from workspace + blocks for scanning.

    *source_key* is ``workspace`` or a block/source id used for per-source counts.
    """
    fields: list[tuple[str, str]] = []
    if workspace.get("name"):
        fields.append(("workspace:name", str(workspace["name"])))
    if workspace.get("description"):
        fields.append(("workspace:description", str(workspace["description"])))

    for block in blocks:
        bid = str(block.get("id") or "")
        btype = block.get("block_type")
        content = block.get("content") or {}
        if btype in ("heading", "note"):
            text = content.get("text")
            if text:
                fields.append((f"block:{bid}", str(text)))
        elif btype == "pin":
            sid = str(content.get("source_id") or bid)
            for key in ("title", "sender", "excerpt"):
                val = content.get(key)
                if val:
                    fields.append((sid, str(val)))
        elif btype == "answer":
            answer = block.get("answer") or {}
            if answer.get("answer_text"):
                fields.append((f"answer:{bid}", str(answer["answer_text"])))
            for cit in answer.get("citations") or []:
                sid = str(cit.get("source_id") or f"answer:{bid}")
                if cit.get("excerpt"):
                    fields.append((sid, str(cit["excerpt"])))
    return fields


def scan_workspace_pii(
    blocks: list[dict[str, Any]],
    workspace: dict[str, Any],
    *,
    kinds: list[str] | None = None,
    custom_terms: list[str] | None = None,
) -> tuple[dict[str, int], list[dict[str, Any]], dict[str, int]]:
    """Scan all export text fields.

    Returns (counts_by_kind, samples≤50, counts_by_source).
    """
    counts: dict[str, int] = {}
    by_source: dict[str, int] = {}
    samples: list[dict[str, Any]] = []

    for source_key, text in collect_export_text_fields(blocks, workspace):
        matches = detect_pii(text, kinds=kinds, custom_terms=custom_terms)
        if not matches:
            continue
        for kind, n in count_by_kind(matches).items():
            counts[kind] = counts.get(kind, 0) + n
        by_source[source_key] = by_source.get(source_key, 0) + len(matches)
        if len(samples) < 50:
            for s in sample_matches(text, matches, limit=50 - len(samples)):
                s["source"] = source_key
                samples.append(s)
    return counts, samples, by_source


def redact_workspace_copy(
    blocks: list[dict[str, Any]],
    workspace: dict[str, Any],
    *,
    kinds: list[str] | None = None,
    custom_terms: list[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, int], dict[str, int]]:
    """Deep-copy workspace + blocks with PII replaced.

    Originals are never mutated. Returns
    (workspace_copy, blocks_copy, counts_by_kind, counts_by_source).
    """
    import copy

    ws = copy.deepcopy(workspace)
    blks = copy.deepcopy(blocks)
    counts: dict[str, int] = {}
    by_source: dict[str, int] = {}

    def _redact_field(source_key: str, text: str) -> str:
        redacted, matches = redact_text(text, kinds=kinds, custom_terms=custom_terms)
        if matches:
            for kind, n in count_by_kind(matches).items():
                counts[kind] = counts.get(kind, 0) + n
            by_source[source_key] = by_source.get(source_key, 0) + len(matches)
        return redacted

    if ws.get("name"):
        ws["name"] = _redact_field("workspace:name", str(ws["name"]))
    if ws.get("description"):
        ws["description"] = _redact_field("workspace:description", str(ws["description"]))

    for block in blks:
        bid = str(block.get("id") or "")
        btype = block.get("block_type")
        content = block.get("content") or {}
        if btype in ("heading", "note") and content.get("text"):
            content["text"] = _redact_field(f"block:{bid}", str(content["text"]))
            block["content"] = content
        elif btype == "pin":
            sid = str(content.get("source_id") or bid)
            for key in ("title", "sender", "excerpt"):
                if content.get(key):
                    content[key] = _redact_field(sid, str(content[key]))
            block["content"] = content
        elif btype == "answer":
            answer = block.get("answer")
            if answer:
                if answer.get("answer_text"):
                    answer["answer_text"] = _redact_field(
                        f"answer:{bid}", str(answer["answer_text"])
                    )
                for cit in answer.get("citations") or []:
                    sid = str(cit.get("source_id") or f"answer:{bid}")
                    if cit.get("excerpt"):
                        cit["excerpt"] = _redact_field(sid, str(cit["excerpt"]))
                block["answer"] = answer

    return ws, blks, counts, by_source
