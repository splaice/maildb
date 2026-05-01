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


_OFFICE_BUCKETS: Final[frozenset[str]] = frozenset({"docx", "xlsx", "pptx"})

# Below these thresholds an image is signature/icon-sized and won't yield useful OCR.
# Skip them with an explicit reason rather than burning Marker time on an empty result.
_MIN_IMAGE_DIMENSION_PX: Final[int] = 100
_MIN_IMAGE_FILESIZE_BYTES: Final[int] = 5 * 1024


def _too_small_to_extract(path: Path) -> str | None:
    """Return a non-empty reason if ``path`` is below useful-OCR thresholds, else None.

    Cheap pre-filter on image attachments — Pillow reads the header without
    decoding the full pixel buffer (microseconds). Errors fall through so a
    truly broken image still hits Marker and surfaces a real failure.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size < _MIN_IMAGE_FILESIZE_BYTES:
        return f"below-minimum-useful-size: {size}B (<{_MIN_IMAGE_FILESIZE_BYTES}B)"
    try:
        from PIL import Image  # noqa: PLC0415

        with Image.open(path) as im:
            w, h = im.size
    except Exception:
        return None
    if w < _MIN_IMAGE_DIMENSION_PX or h < _MIN_IMAGE_DIMENSION_PX:
        return f"below-minimum-useful-size: {w}x{h}px (<{_MIN_IMAGE_DIMENSION_PX}px)"
    return None


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


def _docling_convert(path: Path) -> tuple[str, str]:
    """Run Docling on a single file; return (markdown, version_string).

    Isolated so tests can monkeypatch it without importing docling.
    """
    from importlib.metadata import PackageNotFoundError, version  # noqa: PLC0415

    from docling.document_converter import (  # type: ignore[import-untyped]  # noqa: PLC0415
        DocumentConverter,
    )

    converter = DocumentConverter()
    result = converter.convert(str(path))
    text = result.document.export_to_markdown()
    try:
        ver = version("docling")
    except PackageNotFoundError:
        ver = "unknown"
    return text, f"docling=={ver}"


def extract_markdown(path: Path, *, content_type: str | None) -> ExtractionResult:
    """Extract markdown from an attachment. Raises ExtractionFailedError on unsupported
    types or when extraction fails.

    For office formats (DOCX/XLSX/PPTX), Marker is tried first; on failure, Docling
    is tried as a fallback (issue #61) — Marker routes these through
    LibreOffice→PDF→Surya and fails on a wide range of office files, while Docling
    handles them natively.
    """
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

    if bucket == "image":
        reason = _too_small_to_extract(path)
        if reason is not None:
            raise ExtractionFailedError(reason)

    try:
        markdown, version = _marker_convert(path)
    except Exception as marker_exc:
        if bucket not in _OFFICE_BUCKETS:
            raise ExtractionFailedError(f"marker: {marker_exc}") from marker_exc
        try:
            markdown, version = _docling_convert(path)
        except Exception as docling_exc:
            raise ExtractionFailedError(
                f"marker: {marker_exc}; docling: {docling_exc}"
            ) from docling_exc

    return ExtractionResult(markdown=markdown, extractor_version=version)
