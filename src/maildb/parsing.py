# src/maildb/parsing.py
from __future__ import annotations

import email.utils
import mailbox
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from collections.abc import Iterator


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
    return normalize_whitespace(text)


logger = structlog.get_logger()


def _strip_angles(value: str) -> str:
    return value.strip().strip("<>")


def _parse_references(header: str | None) -> list[str]:
    if not header:
        return []
    return [_strip_angles(ref) for ref in header.split() if ref.strip()]


def _derive_thread_id(message_id: str, references: list[str], in_reply_to: str | None) -> str:
    if references:
        return references[0]
    if in_reply_to:
        return in_reply_to
    return message_id


def _extract_body(msg: mailbox.mboxMessage) -> tuple[str | None, str | None]:
    text_body: str | None = None
    html_body: str | None = None

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if "attachment" in disposition:
                continue
            if content_type == "text/plain" and text_body is None:
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    text_body = payload.decode("utf-8", errors="replace")
            elif content_type == "text/html" and html_body is None:
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    html_body = payload.decode("utf-8", errors="replace")
    else:
        content_type = msg.get_content_type()
        payload = msg.get_payload(decode=True)
        if isinstance(payload, bytes):
            decoded = payload.decode("utf-8", errors="replace")
            if content_type == "text/html":
                html_body = decoded
            else:
                text_body = decoded

    if text_body is None and html_body is not None:
        soup = BeautifulSoup(html_body, "html.parser")
        text_body = soup.get_text(separator="\n", strip=True)

    return text_body, html_body


def _extract_attachments(msg: mailbox.mboxMessage) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    if not msg.is_multipart():
        return attachments

    for part in msg.walk():
        disposition = str(part.get("Content-Disposition", ""))
        if "attachment" not in disposition:
            continue
        filename = part.get_filename() or "unknown"
        content_type = part.get_content_type()
        payload = part.get_payload(decode=True)
        data = payload or b""
        attachments.append(
            {
                "filename": filename,
                "content_type": content_type,
                "size": len(data),
                "data": data,
            }
        )
    return attachments


def parse_message(msg: mailbox.mboxMessage) -> dict[str, Any] | None:
    """Parse a single mbox message into a structured dictionary."""
    raw_message_id = msg.get("Message-ID")
    if not raw_message_id:
        logger.warning("skipping_message_no_id", subject=msg.get("Subject"))
        return None

    message_id = _strip_angles(raw_message_id)

    sender_name, sender_address = email.utils.parseaddr(msg.get("From", ""))
    sender_domain = sender_address.split("@")[1] if "@" in sender_address else None

    to_addrs = [addr for _, addr in email.utils.getaddresses(msg.get_all("To", []))]
    cc_addrs = [addr for _, addr in email.utils.getaddresses(msg.get_all("Cc", []))]
    bcc_addrs = [addr for _, addr in email.utils.getaddresses(msg.get_all("Bcc", []))]
    recipients = {"to": to_addrs, "cc": cc_addrs, "bcc": bcc_addrs}

    date: datetime | None = None
    raw_date = msg.get("Date")
    if raw_date:
        try:
            date = email.utils.parsedate_to_datetime(raw_date)
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
        except (ValueError, TypeError):
            logger.warning("unparseable_date", message_id=message_id, raw_date=raw_date)

    in_reply_to_raw = msg.get("In-Reply-To")
    in_reply_to = _strip_angles(in_reply_to_raw) if in_reply_to_raw else None
    references = _parse_references(msg.get("References"))
    thread_id = _derive_thread_id(message_id, references, in_reply_to)

    raw_text, raw_html = _extract_body(msg)
    body_text = clean_body(raw_text) if raw_text else None

    gmail_labels_raw = msg.get("X-Gmail-Labels")
    labels = (
        [label.strip() for label in gmail_labels_raw.split(",") if label.strip()]
        if gmail_labels_raw
        else []
    )

    attachments_raw = _extract_attachments(msg)
    attachments_metadata = [
        {"filename": a["filename"], "content_type": a["content_type"], "size": a["size"]}
        for a in attachments_raw
    ]

    return {
        "message_id": message_id,
        "thread_id": thread_id,
        "subject": str(msg.get("Subject")) if msg.get("Subject") is not None else None,
        "sender_name": sender_name or None,
        "sender_address": sender_address or None,
        "sender_domain": sender_domain,
        "recipients": recipients,
        "date": date,
        "body_text": body_text or None,
        "body_html": raw_html,
        "has_attachment": len(attachments_raw) > 0,
        "attachments": attachments_metadata,
        "labels": labels,
        "in_reply_to": in_reply_to,
        "references": references,
        "_attachments_with_data": attachments_raw,
    }


def parse_mbox(mbox_path: Path | str) -> Iterator[dict[str, Any]]:
    """Parse all messages from an mbox file, yielding structured dictionaries."""
    mbox_path = Path(mbox_path)
    mbox_file = mailbox.mbox(str(mbox_path))

    for msg in mbox_file:
        try:
            parsed = parse_message(msg)
            if parsed is not None:
                yield parsed
        except Exception:
            logger.exception("failed_to_parse_message", subject=msg.get("Subject"))

    mbox_file.close()
