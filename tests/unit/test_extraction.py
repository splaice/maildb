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
        "application/ics",
        "application/json",
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
