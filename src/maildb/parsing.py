# src/maildb/parsing.py
from __future__ import annotations

import re


def remove_quoted_replies(text: str) -> str:
    """Remove lines starting with > and Outlook-style quoted blocks."""
    lines = text.split("\n")
    result: list[str] = []
    for line in lines:
        if line.startswith(">"):
            continue
        if line.strip() == "-----Original Message-----":
            break
        result.append(line)
    return "\n".join(result)


def remove_signature(text: str) -> str:
    """Remove everything below the standard '-- ' signature delimiter."""
    parts = text.split("\n-- \n")
    return parts[0]


def normalize_whitespace(text: str) -> str:
    """Collapse multiple blank lines and strip trailing whitespace."""
    # Strip trailing whitespace from each line
    lines = [line.rstrip() for line in text.split("\n")]
    text = "\n".join(lines)
    # Collapse 3+ newlines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Strip leading/trailing whitespace
    return text.strip()


def clean_body(text: str | None) -> str:
    """Full body cleaning pipeline."""
    if not text:
        return ""
    text = remove_quoted_replies(text)
    text = remove_signature(text)
    text = normalize_whitespace(text)
    return text
