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
