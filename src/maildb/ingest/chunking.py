"""Structure-aware markdown chunker.

Parses markdown into heading-scoped sections, emits chunks respecting
token bounds. Soft floor merges adjacent small sections; hard cap
triggers paragraph/sentence splits on oversized sections.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from maildb.tokenizer import count_tokens

DEFAULT_MAX_TOKENS = 1024
DEFAULT_MIN_TOKENS = 128

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_PARA_SPLIT_RE = re.compile(r"\n\s*\n+")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


@dataclass
class Chunk:
    chunk_index: int
    heading_path: str | None
    page_number: int | None
    token_count: int
    text: str

    __hash__ = None  # type: ignore[assignment]  # mutable dataclass; eq defined

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Chunk):
            return NotImplemented
        return (
            self.chunk_index == other.chunk_index
            and self.heading_path == other.heading_path
            and self.page_number == other.page_number
            and self.token_count == other.token_count
            and self.text == other.text
        )


@dataclass
class _Section:
    heading_path: str | None
    body: str


def _parse_sections(markdown: str) -> list[_Section]:
    """Walk headings top-to-bottom, producing sections with full heading paths."""
    sections: list[_Section] = []
    stack: list[tuple[int, str]] = []  # (heading_level, heading_text)

    # Split text into heading markers + bodies
    positions = [
        (m.start(), m.end(), len(m.group(1)), m.group(2).strip())
        for m in _HEADING_RE.finditer(markdown)
    ]

    # Preamble before the first heading (if any)
    first_start = positions[0][0] if positions else len(markdown)
    preamble = markdown[:first_start].strip()
    if preamble:
        sections.append(_Section(heading_path=None, body=preamble))

    for i, (_, end, level, heading) in enumerate(positions):
        # Pop to the parent level
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, heading))
        heading_path = " > ".join(h for _, h in stack)
        body_start = end
        body_end = positions[i + 1][0] if i + 1 < len(positions) else len(markdown)
        body = markdown[body_start:body_end].strip()
        if body:
            sections.append(_Section(heading_path=heading_path, body=body))

    return sections


def _split_oversized(body: str, max_tokens: int) -> list[str]:
    """Split a body that exceeds max_tokens into smaller pieces."""
    parts = [p.strip() for p in _PARA_SPLIT_RE.split(body) if p.strip()]
    out: list[str] = []
    for p in parts:
        if count_tokens(p) <= max_tokens:
            out.append(p)
            continue
        # Fall back to sentence splits
        for raw_s in _SENT_SPLIT_RE.split(p):
            sent = raw_s.strip()
            if not sent:
                continue
            if count_tokens(sent) <= max_tokens:
                out.append(sent)
            else:
                # Very long sentence — hard-split by word count as last resort
                words = sent.split()
                current: list[str] = []
                for w in words:
                    current.append(w)
                    if count_tokens(" ".join(current)) > max_tokens:
                        # Back off one word, emit, reset
                        current.pop()
                        out.append(" ".join(current))
                        current = [w]
                if current:
                    out.append(" ".join(current))
    return out


def chunk_markdown(
    markdown: str,
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    min_tokens: int = DEFAULT_MIN_TOKENS,
    page_number: int | None = None,
) -> list[Chunk]:
    """Split markdown into heading-scoped chunks.

    Order-preserving. Deterministic for the same input.
    """
    if not markdown.strip():
        return []

    sections = _parse_sections(markdown)

    # Step 1: expand oversized sections
    prepared: list[tuple[str | None, str, int]] = []  # (heading_path, text, token_count)
    for sec in sections:
        tokens = count_tokens(sec.body)
        if tokens <= max_tokens:
            prepared.append((sec.heading_path, sec.body, tokens))
            continue
        pieces = _split_oversized(sec.body, max_tokens)
        prepared.extend((sec.heading_path, piece, count_tokens(piece)) for piece in pieces)

    def _parent_path(path: str | None) -> str:
        """Return the parent heading path (everything before the last ' > ')."""
        if path is None:
            return ""
        sep = " > "
        idx = path.rfind(sep)
        return path[:idx] if idx >= 0 else ""

    # Step 2: merge adjacent sections when both are under the soft floor AND share the same parent path
    merged: list[tuple[str | None, str, int]] = []
    for heading_path, text, tokens in prepared:
        if (
            merged
            and _parent_path(merged[-1][0]) == _parent_path(heading_path)  # same parent
            and merged[-1][2] < min_tokens
            and tokens < min_tokens
            and (merged[-1][2] + tokens) <= max_tokens
        ):
            prev_path, prev_text, _prev_tokens = merged[-1]
            merged[-1] = (
                prev_path,  # keep the earlier path; this is a merge, not a demotion
                prev_text + "\n\n" + text,
                count_tokens(prev_text + "\n\n" + text),
            )
        else:
            merged.append((heading_path, text, tokens))

    return [
        Chunk(
            chunk_index=i,
            heading_path=path,
            page_number=page_number,
            token_count=tokens,
            text=text,
        )
        for i, (path, text, tokens) in enumerate(merged)
    ]
