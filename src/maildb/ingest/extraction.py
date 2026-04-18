"""Attachment extraction: content-type routing + Marker wrapper.

route_content_type maps MIME types to an internal bucket name or None
(unsupported). Buckets are the granularity used by CLI --only filters
and by the Marker dispatch below.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import structlog

if TYPE_CHECKING:
    from pathlib import Path

logger = structlog.get_logger()

SUPPORTED: Final[set[str]] = {
    "pdf",
    "doc_legacy",
    "docx",
    "xls_legacy",
    "xlsx",
    "pptx",
    "text",
    "html",
    "image",
}

_ROUTES: Final[dict[str, str]] = {
    "application/pdf": "pdf",
    "application/msword": "doc_legacy",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.ms-excel": "xls_legacy",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "text/plain": "text",
    "text/html": "html",
    "image/png": "image",
    "image/jpeg": "image",
    "image/jpg": "image",
    "image/gif": "image",
    "image/tiff": "image",
    "image/webp": "image",
}


def route_content_type(content_type: str | None) -> str | None:
    """Return the bucket for a content-type, or None if unsupported."""
    if not content_type:
        return None
    return _ROUTES.get(content_type.lower())


class ExtractionFailedError(Exception):
    """Raised when extraction cannot proceed. The message is recorded as `reason`."""


@dataclass
class ExtractionResult:
    markdown: str
    extractor_version: str  # e.g. "marker==1.2.3" or "passthrough==1"


def _marker_convert(path: Path) -> tuple[str, str]:
    """Run Marker on a single file; return (markdown, version_string).

    Isolated so tests can monkeypatch it without importing marker-pdf.
    """
    import marker  # type: ignore[import-untyped]  # noqa: PLC0415
    from marker.converters.pdf import PdfConverter  # type: ignore[import-untyped]  # noqa: PLC0415
    from marker.models import create_model_dict  # type: ignore[import-untyped]  # noqa: PLC0415
    from marker.output import text_from_rendered  # type: ignore[import-untyped]  # noqa: PLC0415

    converter = PdfConverter(artifact_dict=create_model_dict())
    rendered = converter(str(path))
    text, _, _ = text_from_rendered(rendered)
    return text, f"marker=={getattr(marker, '__version__', 'unknown')}"


def extract_markdown(path: Path, *, content_type: str | None) -> ExtractionResult:
    """Extract markdown from an attachment. Raises ExtractionFailedError on unsupported
    types or when Marker errors out."""
    bucket = route_content_type(content_type)
    if bucket is None:
        raise ExtractionFailedError(f"content_type {content_type!r} is not supported by Marker")

    if bucket == "text":
        return ExtractionResult(
            markdown=path.read_text(encoding="utf-8", errors="replace"),
            extractor_version="passthrough==1",
        )

    if bucket == "html":
        # Pass HTML through as-is; Marker can handle conversion downstream if needed,
        # but for v1 we preserve the original markup so agents can see tags.
        return ExtractionResult(
            markdown=path.read_text(encoding="utf-8", errors="replace"),
            extractor_version="passthrough==1",
        )

    # Legacy .doc / .xls need LibreOffice pre-conversion. Defer to Marker for the
    # rest — Marker handles PDF, DOCX, XLSX, PPTX, and images natively.
    if bucket in ("doc_legacy", "xls_legacy"):
        raise ExtractionFailedError(
            f"{bucket}: legacy binary format requires LibreOffice pre-conversion "
            "(not implemented in v1)"
        )

    try:
        markdown, version = _marker_convert(path)
    except Exception as exc:
        raise ExtractionFailedError(f"marker: {exc}") from exc

    return ExtractionResult(markdown=markdown, extractor_version=version)
