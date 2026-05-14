"""Attachment extraction: content-type routing + Marker wrapper.

route_content_type maps MIME types to an internal bucket name or None
(unsupported). Buckets are the granularity used by CLI --only filters
and by the dispatch table below.

Three legs handle the supported buckets:

  * passthrough — text-shaped formats (text, html, calendar, csv, json,
    xml, vcard) decoded as UTF-8 directly. LLMs handle these formats
    natively at embedding time, so converting them to markdown tables
    or other rich representations adds noise + risk (#82) without value.

  * markitdown  — binary formats Marker doesn't handle natively, namely
    .xls (OLE2 compound docs → multi-sheet markdown tables) and .pages
    (zip archives → inner XML).

  * marker (with docling fallback for office) — pdf/docx/xlsx/pptx and
    images. Marker is primary; on failure for an office bucket, Docling
    is tried as a fallback (issue #61).
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
    "calendar",
    "csv",
    "json",
    "xml",
    "vcard",
    "pages",
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
    "text/calendar": "calendar",
    "application/ics": "calendar",
    "text/csv": "csv",
    "application/json": "json",
    "application/xml": "xml",
    "text/x-vcard": "vcard",
    "application/x-iwork-pages-sffpages": "pages",
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

# Pre-formatted text buckets that are decoded UTF-8 and returned as-is.
# LLMs handle CSV/JSON/XML/iCal/vCard natively, so the markdown-table
# conversion layer adds noise (cell-by-cell `|` wrapping inflates the
# embedded text) and risk (#82) without value. text/html joins this set
# to keep the passthrough path consolidated.
_PASSTHROUGH_BUCKETS: Final[frozenset[str]] = frozenset(
    {"text", "html", "calendar", "csv", "json", "xml", "vcard"}
)

# Binary formats Marker doesn't handle. MarkItDown's value-add for these is
# real: .xls becomes multi-sheet markdown tables; .pages is unwrapped from
# its zip container.
_MARKITDOWN_BUCKETS: Final[frozenset[str]] = frozenset({"xls_legacy", "pages"})

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


def _markitdown_run(file_path_str: str) -> str:
    """Invoke MarkItDown on a path string; isolated for test patching.

    Mirrors the ``_marker_convert`` / ``_docling_convert`` wrapper pattern so
    tests can mock the heavy import without bringing the dep into scope. Used
    only for binary buckets (xls, pages); text-shaped formats go through the
    passthrough path in ``extract_markdown`` instead.
    """
    from markitdown import MarkItDown  # type: ignore[import-untyped]  # noqa: PLC0415

    return MarkItDown().convert(file_path_str).markdown or ""


def _markitdown_convert(path: Path) -> tuple[str, str]:
    """Run MarkItDown on a binary file; return (markdown, version_string)."""
    from importlib.metadata import PackageNotFoundError, version  # noqa: PLC0415

    markdown = _markitdown_run(str(path))
    try:
        ver = version("markitdown")
    except PackageNotFoundError:
        ver = "unknown"
    return markdown, f"markitdown=={ver}"


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
    """Extract markdown from an attachment. Raises ExtractionFailedError on
    unsupported types or when extraction fails.

    Dispatch by bucket:

      * passthrough buckets — decode bytes as UTF-8 and return.
      * doc_legacy — raise (LibreOffice/antiword route is Tier 5).
      * MarkItDown buckets (xls, pages) — binary formats only.
      * Everything else — Marker, with Docling fallback for office formats.
    """
    bucket = route_content_type(content_type)
    if bucket is None:
        raise ExtractionFailedError(f"content_type {content_type!r} is not supported by Marker")

    if bucket in _PASSTHROUGH_BUCKETS:
        return ExtractionResult(
            markdown=path.read_text(encoding="utf-8", errors="replace"),
            extractor_version="passthrough==1",
        )

    if bucket == "doc_legacy":
        raise ExtractionFailedError(
            "doc_legacy: legacy binary format requires LibreOffice pre-conversion "
            "(not implemented in v1)"
        )

    if bucket in _MARKITDOWN_BUCKETS:
        try:
            markdown, ver = _markitdown_convert(path)
        except Exception as exc:
            raise ExtractionFailedError(f"markitdown: {exc}") from exc
        return ExtractionResult(markdown=markdown, extractor_version=ver)

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
