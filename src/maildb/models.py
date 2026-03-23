# src/maildb/models.py
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass
class Attachment:
    filename: str
    content_type: str
    size: int


@dataclass
class Recipients:
    to: list[str]
    cc: list[str]
    bcc: list[str]


@dataclass
class Email:
    id: UUID
    message_id: str
    thread_id: str
    subject: str | None
    sender_name: str | None
    sender_address: str | None
    sender_domain: str | None
    recipients: Recipients | None
    date: datetime | None
    body_text: str | None
    body_html: str | None
    has_attachment: bool
    attachments: list[Attachment]
    labels: list[str]
    in_reply_to: str | None
    references: list[str]
    embedding: list[float] | None
    created_at: datetime

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Email:
        # Parse recipients JSONB
        raw_recipients = row["recipients"]
        if raw_recipients is None:
            recipients = None
        else:
            if isinstance(raw_recipients, str):
                raw_recipients = json.loads(raw_recipients)
            recipients = Recipients(
                to=raw_recipients.get("to", []),
                cc=raw_recipients.get("cc", []),
                bcc=raw_recipients.get("bcc", []),
            )

        # Parse attachments JSONB
        raw_attachments = row["attachments"]
        if raw_attachments is None:
            attachments_list: list[Attachment] = []
        else:
            if isinstance(raw_attachments, str):
                raw_attachments = json.loads(raw_attachments)
            attachments_list = [
                Attachment(
                    filename=a["filename"],
                    content_type=a["content_type"],
                    size=a["size"],
                )
                for a in raw_attachments
            ]

        return cls(
            id=row["id"],
            message_id=row["message_id"],
            thread_id=row["thread_id"],
            subject=row.get("subject"),
            sender_name=row.get("sender_name"),
            sender_address=row.get("sender_address"),
            sender_domain=row.get("sender_domain"),
            recipients=recipients,
            date=row.get("date"),
            body_text=row.get("body_text"),
            body_html=row.get("body_html"),
            has_attachment=row.get("has_attachment", False),
            attachments=attachments_list,
            labels=row.get("labels") or [],
            in_reply_to=row.get("in_reply_to"),
            references=row.get("references") or [],
            embedding=row.get("embedding"),
            created_at=row["created_at"],
        )


@dataclass
class SearchResult:
    email: Email
    similarity: float
