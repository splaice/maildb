from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from maildb.ingest.extraction import (
    SUPPORTED,
    ExtractionFailedError,
    ExtractionResult,
    extract_markdown,
    route_content_type,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    "content_type,expected_bucket",
    [
        ("application/pdf", "pdf"),
        ("application/msword", "doc_legacy"),
        ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
        ("application/vnd.ms-excel", "xls_legacy"),
        ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
        (
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "pptx",
        ),
        ("text/plain", "text"),
        ("text/html", "html"),
        ("image/png", "image"),
        ("image/jpeg", "image"),
        ("image/jpg", "image"),
        ("image/gif", "image"),
        ("image/tiff", "image"),
    ],
)
def test_supported_types_route_to_known_buckets(content_type, expected_bucket):
    assert route_content_type(content_type) == expected_bucket


@pytest.mark.parametrize(
    "content_type",
    [
        "audio/mpeg",
        "application/zip",
        "video/quicktime",
        "application/octet-stream",
        "application/rtf",
        "image/svg+xml",
        "",
        None,
    ],
)
def test_unsupported_types_return_none(content_type):
    assert route_content_type(content_type) is None


def test_supported_set_matches_router():
    """Every bucket named by SUPPORTED is reachable via route_content_type."""
    reachable = {
        route_content_type(t)
        for t in [
            "application/pdf",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.ms-excel",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "text/plain",
            "text/html",
            "image/png",
        ]
    }
    reachable.discard(None)
    assert reachable <= SUPPORTED


def test_extract_passes_through_text_file(tmp_path: Path):
    p = tmp_path / "hello.txt"
    p.write_text("Hello world\nA second line")
    result = extract_markdown(p, content_type="text/plain")
    assert isinstance(result, ExtractionResult)
    assert "Hello world" in result.markdown
    assert result.extractor_version.startswith("passthrough")


def test_extract_passes_through_html(tmp_path: Path):
    p = tmp_path / "page.html"
    p.write_text("<html><body><h1>Hi</h1><p>there</p></body></html>")
    result = extract_markdown(p, content_type="text/html")
    # Passthrough preserves the raw content; it's not Marker's job.
    assert "<h1>Hi</h1>" in result.markdown or "Hi" in result.markdown


def test_extract_calls_marker_for_pdf(tmp_path: Path):
    fake_pdf = tmp_path / "fake.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4\n...")
    with patch(
        "maildb.ingest.extraction._marker_convert",
        return_value=("# Fake extracted markdown\n\nBody.", "marker==1.10.2"),
    ) as m:
        result = extract_markdown(fake_pdf, content_type="application/pdf")
    assert m.called
    assert result.markdown.startswith("# Fake extracted markdown")
    assert result.extractor_version.startswith("marker==")


def test_extract_unsupported_raises_extraction_failed(tmp_path: Path):
    p = tmp_path / "a.mp3"
    p.write_bytes(b"ID3\x00")
    with pytest.raises(ExtractionFailedError) as exc:
        extract_markdown(p, content_type="audio/mpeg")
    assert "not supported" in str(exc.value).lower()


# --- Docling fallback for office formats (issue #61) -------------------------


_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


@pytest.mark.parametrize("content_type", [_DOCX, _XLSX, _PPTX])
def test_extract_falls_back_to_docling_for_office_when_marker_fails(
    tmp_path: Path, content_type: str
):
    """Marker routes office formats through LibreOffice→PDF→Surya and fails on a
    wide range of files. Docling handles DOCX/XLSX/PPTX natively. On Marker
    failure for an office bucket, fall back to Docling and tag the result."""
    p = tmp_path / "doc.bin"
    p.write_bytes(b"PK\x03\x04fake")
    with (
        patch(
            "maildb.ingest.extraction._marker_convert",
            side_effect=RuntimeError("Failed to convert"),
        ),
        patch(
            "maildb.ingest.extraction._docling_convert",
            return_value=("# Docling\n\nbody", "docling==2.0.0"),
        ) as docling,
    ):
        result = extract_markdown(p, content_type=content_type)
    docling.assert_called_once()
    assert result.markdown.startswith("# Docling")
    assert result.extractor_version.startswith("docling==")


def test_extract_does_not_fall_back_to_docling_for_pdf(tmp_path: Path):
    """PDFs stay Marker-only — Marker's layout/heading fidelity is materially
    better for PDFs and Docling is opt-in for office formats only."""
    p = tmp_path / "fake.pdf"
    p.write_bytes(b"%PDF-1.4\n...")
    with (
        patch(
            "maildb.ingest.extraction._marker_convert",
            side_effect=RuntimeError("marker boom"),
        ),
        patch("maildb.ingest.extraction._docling_convert") as docling,
        pytest.raises(ExtractionFailedError, match="marker:"),
    ):
        extract_markdown(p, content_type="application/pdf")
    docling.assert_not_called()


def test_extract_when_both_marker_and_docling_fail_raises(tmp_path: Path):
    """If Docling also fails, surface a combined error so ops see the full chain."""
    p = tmp_path / "doc.docx"
    p.write_bytes(b"PK\x03\x04fake")
    with (
        patch(
            "maildb.ingest.extraction._marker_convert",
            side_effect=RuntimeError("Failed to convert"),
        ),
        patch(
            "maildb.ingest.extraction._docling_convert",
            side_effect=RuntimeError("docling boom"),
        ),
        pytest.raises(ExtractionFailedError) as exc,
    ):
        extract_markdown(p, content_type=_DOCX)
    msg = str(exc.value)
    assert "marker" in msg.lower()
    assert "docling" in msg.lower()


# --- Tiny-image filter (issue #65) ------------------------------------------


def _write_png(path: Path, width: int, height: int) -> None:
    from PIL import Image  # noqa: PLC0415

    Image.new("RGB", (width, height), color=(255, 0, 0)).save(path, format="PNG")


def test_extract_skips_tiny_image_by_dimensions(tmp_path: Path):
    """Images below 100px on either axis are skipped, not extracted — Marker
    pipeline is too heavy for content that can't yield useful OCR (signatures,
    icons, decorative pixels)."""
    p = tmp_path / "tiny.png"
    _write_png(p, 50, 50)
    with (
        patch("maildb.ingest.extraction._marker_convert") as marker,
        pytest.raises(ExtractionFailedError, match="below-minimum-useful-size"),
    ):
        extract_markdown(p, content_type="image/png")
    marker.assert_not_called()


def test_extract_skips_tiny_image_by_filesize(tmp_path: Path):
    """Files below 5 KB skip extraction with the same reason — covers
    decorative gifs/jpegs that pass the dimension check but have no payload."""
    p = tmp_path / "tiny.gif"
    p.write_bytes(b"GIF89a" + b"\x00" * 200)  # ~206 bytes
    with (
        patch("maildb.ingest.extraction._marker_convert") as marker,
        pytest.raises(ExtractionFailedError, match="below-minimum-useful-size"),
    ):
        extract_markdown(p, content_type="image/gif")
    marker.assert_not_called()


def test_extract_processes_normal_image(tmp_path: Path):
    """Images that clear both thresholds proceed to Marker as before."""
    import secrets as _secrets  # noqa: PLC0415

    from PIL import Image  # noqa: PLC0415

    p = tmp_path / "ok.png"
    # Random pixels defeat PNG compression so the file lands well above 5KB.
    img = Image.frombytes("RGB", (200, 200), _secrets.token_bytes(200 * 200 * 3))
    img.save(p, format="PNG")
    assert p.stat().st_size > 5 * 1024
    with patch(
        "maildb.ingest.extraction._marker_convert",
        return_value=("# extracted", "marker==1.10.2"),
    ) as marker:
        result = extract_markdown(p, content_type="image/png")
    marker.assert_called_once()
    assert result.markdown.startswith("# extracted")


def test_extract_marker_success_for_office_does_not_call_docling(tmp_path: Path):
    """When Marker succeeds, Docling must not be invoked — Marker is primary."""
    p = tmp_path / "doc.docx"
    p.write_bytes(b"PK\x03\x04fake")
    with (
        patch(
            "maildb.ingest.extraction._marker_convert",
            return_value=("# Marker out", "marker==1.10.2"),
        ),
        patch("maildb.ingest.extraction._docling_convert") as docling,
    ):
        result = extract_markdown(p, content_type=_DOCX)
    docling.assert_not_called()
    assert result.extractor_version.startswith("marker==")


# --- Tier 4: MarkItDown integration (issue #83) ----------------------------

_NEW_ROUTES = [
    ("text/calendar", "calendar"),
    ("application/ics", "calendar"),
    ("text/csv", "csv"),
    ("application/json", "json"),
    ("application/xml", "xml"),
    ("text/x-vcard", "vcard"),
    ("application/x-iwork-pages-sffpages", "pages"),
]


@pytest.mark.parametrize(("content_type", "bucket"), _NEW_ROUTES)
def test_new_text_buckets_route_correctly(content_type: str, bucket: str):
    """The Tier 4 MIME types map to dedicated buckets so the CLI --only filter
    and the yield-by-content-type dashboard can name them distinctly."""
    assert route_content_type(content_type) == bucket


def test_supported_includes_new_buckets():
    """SUPPORTED is the contract for what extract_markdown can route. New
    Tier 4 buckets must be in it so callers can opt-in via --only filters."""
    assert {"calendar", "csv", "json", "xml", "vcard", "pages"} <= SUPPORTED


# --- Tier 4: text-shaped buckets passthrough as UTF-8 ---------------------
#
# CSV/JSON/XML/iCal/vCard are pre-formatted text. LLMs handle them natively
# at embedding time, so the markdown-table conversion (originally via
# MarkItDown) added noise + risk (#82) without value, and choked on large
# CSVs (200k rows x 23 cols, pandas.to_markdown timed out at 900s).
# Pass them through as UTF-8 instead. Binary formats (xls, pages) still
# need MarkItDown.

_PASSTHROUGH_TIER4 = [
    ("text/calendar", "calendar"),
    ("application/ics", "calendar"),
    ("text/csv", "csv"),
    ("application/json", "json"),
    ("application/xml", "xml"),
    ("text/x-vcard", "vcard"),
]


@pytest.mark.parametrize(("content_type", "bucket"), _PASSTHROUGH_TIER4)
def test_text_shaped_buckets_passthrough_as_utf8(tmp_path: Path, content_type: str, bucket: str):
    """Each text-shaped Tier 4 bucket reads bytes as UTF-8 and returns a
    passthrough result. None of the heavy converters (Marker/Docling/
    MarkItDown) is invoked - these formats are already structured text."""
    p = tmp_path / "name_doesnt_matter.bin"
    p.write_bytes(b"hello \xe2\x80\x99 world\n")
    with (
        patch("maildb.ingest.extraction._marker_convert") as marker,
        patch("maildb.ingest.extraction._docling_convert") as docling,
        patch("maildb.ingest.extraction._markitdown_run") as markitdown,
    ):
        result = extract_markdown(p, content_type=content_type)
    marker.assert_not_called()
    docling.assert_not_called()
    markitdown.assert_not_called()
    assert result.extractor_version == "passthrough==1"
    # Valid UTF-8 round-trips intact.
    assert "\u2019" in result.markdown
    assert "hello" in result.markdown
    assert "world" in result.markdown


def test_passthrough_handles_invalid_bytes_with_replacement(tmp_path: Path):
    """A file containing genuinely invalid UTF-8 still extracts -
    ``errors='replace'`` substitutes U+FFFD for invalid sequences. Mirrors
    the existing text/html behavior so ingest never aborts on a bad byte."""
    p = tmp_path / "weird.csv"
    p.write_bytes(b"good,\xff,bad\n")
    result = extract_markdown(p, content_type="text/csv")
    assert "good" in result.markdown
    assert "bad" in result.markdown
    assert "\ufffd" in result.markdown


def test_passthrough_handles_huge_csv_quickly(tmp_path: Path):
    """Regression: a 200k-row CSV previously hit MarkItDown's
    pandas.to_markdown() and timed out at 900s. Passthrough is O(filesize)
    so multi-MB CSVs return immediately. Synthetic 50k-row file proves the
    path doesn't degrade on volume."""
    import time  # noqa: PLC0415

    p = tmp_path / "big.csv"
    rows = ["a,b,c,d,e\n"] + [f"{i},{i + 1},{i + 2},{i + 3},{i + 4}\n" for i in range(50_000)]
    p.write_bytes("".join(rows).encode())
    started = time.perf_counter()
    result = extract_markdown(p, content_type="text/csv")
    elapsed = time.perf_counter() - started
    assert elapsed < 5.0, f"passthrough took {elapsed:.1f}s on 50k rows - far too slow"
    assert result.extractor_version == "passthrough==1"
    assert "a,b,c,d,e" in result.markdown
    assert "49999" in result.markdown  # last row preserved


# --- Tier 4: binary buckets - MarkItDown still required -------------------


def test_extract_calls_markitdown_for_xls_legacy(tmp_path: Path):
    """application/vnd.ms-excel is a binary OLE2 compound document; MarkItDown
    natively converts to multi-sheet markdown tables in seconds."""
    p = tmp_path / "report.xls"
    p.write_bytes(b"\xd0\xcf\x11\xe0fake")
    with patch(
        "maildb.ingest.extraction._markitdown_convert",
        return_value=("## Sheet1\n| A | B |", "markitdown==0.1.5"),
    ) as md:
        result = extract_markdown(p, content_type="application/vnd.ms-excel")
    md.assert_called_once()
    assert "Sheet1" in result.markdown
    assert result.extractor_version.startswith("markitdown==")


def test_extract_calls_markitdown_for_pages(tmp_path: Path):
    """application/x-iwork-pages-sffpages is a zip archive; MarkItDown
    unwraps the zip and pulls text out of the inner XML representation."""
    p = tmp_path / "doc.pages"
    p.write_bytes(b"PK\x03\x04fake")
    with patch(
        "maildb.ingest.extraction._markitdown_convert",
        return_value=("# Doc body", "markitdown==0.1.5"),
    ) as md:
        result = extract_markdown(p, content_type="application/x-iwork-pages-sffpages")
    md.assert_called_once()
    assert result.extractor_version.startswith("markitdown==")


def test_extract_doc_legacy_still_raises(tmp_path: Path):
    """Regression: MarkItDown does not support binary .doc; doc_legacy
    continues to raise so we don't pretend to handle it. LibreOffice/antiword
    is a separate Tier 5."""
    p = tmp_path / "old.doc"
    p.write_bytes(b"\xd0\xcf\x11\xe0fake")
    with (
        patch("maildb.ingest.extraction._markitdown_convert") as md,
        pytest.raises(ExtractionFailedError, match="doc_legacy"),
    ):
        extract_markdown(p, content_type="application/msword")
    md.assert_not_called()


def test_extract_markitdown_failure_surfaces_as_extraction_failed(tmp_path: Path):
    """If MarkItDown raises on a binary bucket, the error surfaces as
    ExtractionFailedError tagged 'markitdown:' so ops telemetry can
    distinguish it from marker:/docling: failures."""
    p = tmp_path / "report.xls"
    p.write_bytes(b"\xd0\xcf\x11\xe0fake")
    with (
        patch(
            "maildb.ingest.extraction._markitdown_convert",
            side_effect=RuntimeError("boom"),
        ),
        pytest.raises(ExtractionFailedError, match="markitdown:"),
    ):
        extract_markdown(p, content_type="application/vnd.ms-excel")
