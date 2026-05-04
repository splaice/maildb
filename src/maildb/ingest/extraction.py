"""Attachment extraction: content-type routing + Marker wrapper.

route_content_type maps MIME types to an internal bucket name or None
(unsupported). Buckets are the granularity used by CLI --only filters
and by the Marker dispatch below.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import structlog

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
    # Tier 4: routed through MarkItDown — content types Marker doesn't handle.
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
    # Tier 4: MarkItDown buckets.
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

_MARKITDOWN_BUCKETS: Final[frozenset[str]] = frozenset(
    {"calendar", "csv", "json", "xml", "vcard", "pages", "xls_legacy"}
)

# Buckets whose content is text and needs the #82 workaround: UTF-8 byte
# normalization plus an explicit StreamInfo(charset='utf-8') so MarkItDown
# doesn't auto-classify ASCII-front-loaded files as ASCII and crash on the
# first non-ASCII byte. Driven by bucket (not suffix) so MIME-routed files
# without a matching extension still get the fix.
_MARKITDOWN_TEXT_BUCKETS: Final[frozenset[str]] = frozenset(
    {"calendar", "csv", "json", "xml", "vcard"}
)

# Canonical suffix per text bucket — used for the UTF-8-normalized temp file
# (so MarkItDown's converter routing has a stable signal) and as the explicit
# extension hint passed via StreamInfo. Independent of the input filename.
_MARKITDOWN_BUCKET_SUFFIX: Final[dict[str, str]] = {
    "calendar": ".ics",
    "csv": ".csv",
    "json": ".json",
    "xml": ".xml",
    "vcard": ".vcf",
}

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


def _normalize_to_utf8_temp(path: Path, *, suffix: str | None = None) -> Path:
    """Decode bytes as UTF-8 with errors='replace' and write to a temp file.

    Workaround for MarkItDown PlainTextConverter ASCII default (#82) — files
    containing common non-ASCII bytes (smart quotes, em-dashes) crash unless
    pre-normalized. Invalid bytes become U+FFFD; valid UTF-8 round-trips.
    The temp-file suffix defaults to the input's suffix; callers can pass
    ``suffix`` to override (used for MIME-routed files where the on-disk
    filename doesn't reflect the actual content shape — PR #84 review fix).
    Caller owns the returned path and must unlink when done.
    """
    import tempfile  # noqa: PLC0415

    text = path.read_bytes().decode("utf-8", errors="replace")
    fd, name = tempfile.mkstemp(suffix=suffix or path.suffix)
    os.close(fd)
    out = Path(name)
    out.write_text(text, encoding="utf-8")
    return out


def _markitdown_run(
    file_path_str: str,
    *,
    force_charset: str | None = None,
    force_extension: str | None = None,
) -> str:
    """Invoke MarkItDown on a path string; isolated for test patching.

    When ``force_charset`` or ``force_extension`` is set, opens the file as
    a binary stream and passes an explicit ``StreamInfo`` via
    ``convert_stream``. The explicit charset bypasses MarkItDown's
    4 KB-sample auto-detection (fatal for ASCII-front-loaded ICS — #82); the
    explicit extension overrides the on-disk filename so MIME-routed files
    pick the right converter (PR #84 review fix). Mirrors the
    ``_marker_convert`` / ``_docling_convert`` wrapper pattern so tests can
    mock the heavy import without bringing the dep into scope.
    """
    from markitdown import (  # type: ignore[import-untyped]  # noqa: PLC0415
        MarkItDown,
        StreamInfo,
    )

    md = MarkItDown()
    if force_charset is None and force_extension is None:
        return md.convert(file_path_str).markdown or ""
    p = Path(file_path_str)
    with p.open("rb") as f:
        result = md.convert_stream(
            f,
            stream_info=StreamInfo(
                extension=force_extension or p.suffix,
                charset=force_charset,
            ),
        )
    return result.markdown or ""


def _markitdown_convert(path: Path, bucket: str) -> tuple[str, str]:
    """Run MarkItDown on a single file; return (markdown, version_string).

    Used for content types Marker doesn't natively handle (ical, csv, json,
    xml, vcard, .pages, .xls). Dispatch is by *bucket* — not by file suffix
    — so MIME-routed attachments (e.g. ``content_type='text/calendar'`` with
    a generic filename like ``invite.dat``) still receive the #82 workaround
    (PR #84 review fix). For text buckets the file is UTF-8 normalized to a
    temp file with the bucket-canonical suffix *and* MarkItDown is forced to
    use UTF-8 via an explicit ``StreamInfo`` — both are needed: the temp-file
    step substitutes U+FFFD for genuinely-invalid bytes; the explicit charset
    bypasses the 4 KB-sample auto-detection that would otherwise misclassify
    an ASCII-front-loaded ICS as ASCII.
    """
    from importlib.metadata import PackageNotFoundError, version  # noqa: PLC0415

    if bucket in _MARKITDOWN_TEXT_BUCKETS:
        forced_suffix = _MARKITDOWN_BUCKET_SUFFIX[bucket]
        normalized = _normalize_to_utf8_temp(path, suffix=forced_suffix)
        try:
            markdown = _markitdown_run(
                str(normalized),
                force_charset="utf-8",
                force_extension=forced_suffix,
            )
        finally:
            normalized.unlink(missing_ok=True)
    else:
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

    # Legacy .doc still needs LibreOffice pre-conversion (Tier 5 candidate);
    # MarkItDown's UnsupportedFormatException on binary .doc means we can't
    # route it through the Tier 4 leg.
    if bucket == "doc_legacy":
        raise ExtractionFailedError(
            "doc_legacy: legacy binary format requires LibreOffice pre-conversion "
            "(not implemented in v1)"
        )

    # Tier 4 (#83): MarkItDown handles content types Marker doesn't —
    # ical/csv/json/xml/vcard/.pages and notably .xls (which it converts to
    # multi-sheet markdown tables natively). Bucket is passed through so the
    # text-bucket workaround for #82 fires regardless of filename suffix
    # (PR #84 review fix).
    if bucket in _MARKITDOWN_BUCKETS:
        try:
            markdown, ver = _markitdown_convert(path, bucket)
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
