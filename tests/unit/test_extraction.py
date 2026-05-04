from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from maildb.ingest.extraction import (
    SUPPORTED,
    ExtractionFailedError,
    ExtractionResult,
    extract_markdown,
    route_content_type,
)


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


_BUCKET_SUFFIXES = {
    "calendar": ".ics",
    "csv": ".csv",
    "json": ".json",
    "xml": ".xml",
    "vcard": ".vcf",
    "pages": ".pages",
}


@pytest.mark.parametrize(("content_type", "bucket"), _NEW_ROUTES)
def test_extract_calls_markitdown_for_new_buckets(tmp_path: Path, content_type: str, bucket: str):
    """For each Tier 4 bucket, extract_markdown dispatches to _markitdown_convert
    and returns its output tagged with the markitdown version. Marker and
    Docling are not consulted — these formats are MarkItDown's territory."""
    p = tmp_path / f"sample{_BUCKET_SUFFIXES[bucket]}"
    p.write_bytes(b"placeholder")
    with (
        patch(
            "maildb.ingest.extraction._markitdown_convert",
            return_value=("# md output", "markitdown==0.1.0"),
        ) as md,
        patch("maildb.ingest.extraction._marker_convert") as marker,
        patch("maildb.ingest.extraction._docling_convert") as docling,
    ):
        result = extract_markdown(p, content_type=content_type)
    md.assert_called_once()
    marker.assert_not_called()
    docling.assert_not_called()
    assert result.markdown == "# md output"
    assert result.extractor_version.startswith("markitdown==")


def test_extract_calls_markitdown_for_xls_legacy(tmp_path: Path):
    """application/vnd.ms-excel was previously raising 'not implemented in v1'.
    Tier 4 routes it through MarkItDown (which natively converts .xls → markdown
    tables in seconds) — the spike confirmed multi-sheet .xls files produce
    clean tables, obsoleting the planned LibreOffice route."""
    p = tmp_path / "report.xls"
    p.write_bytes(b"\xd0\xcf\x11\xe0fake")  # OLE2 compound-document magic
    with patch(
        "maildb.ingest.extraction._markitdown_convert",
        return_value=("## Sheet1\n| A | B |", "markitdown==0.1.0"),
    ) as md:
        result = extract_markdown(p, content_type="application/vnd.ms-excel")
    md.assert_called_once()
    assert "Sheet1" in result.markdown
    assert result.extractor_version.startswith("markitdown==")


def test_extract_doc_legacy_still_raises(tmp_path: Path):
    """Regression: MarkItDown does not support binary .doc (returns
    UnsupportedFormatException). doc_legacy must continue to raise so we
    don't pretend to handle it; LibreOffice/antiword is a separate Tier 5."""
    p = tmp_path / "old.doc"
    p.write_bytes(b"\xd0\xcf\x11\xe0fake")
    with (
        patch("maildb.ingest.extraction._markitdown_convert") as md,
        pytest.raises(ExtractionFailedError, match="doc_legacy"),
    ):
        extract_markdown(p, content_type="application/msword")
    md.assert_not_called()


def test_extract_markitdown_failure_surfaces_as_extraction_failed(tmp_path: Path):
    """If MarkItDown raises, the error surfaces as ExtractionFailedError tagged
    'markitdown:' so ops telemetry can distinguish it from marker:/docling:
    failures and route follow-up actions accordingly."""
    p = tmp_path / "thing.csv"
    p.write_bytes(b"a,b\n1,2\n")
    with (
        patch(
            "maildb.ingest.extraction._markitdown_convert",
            side_effect=RuntimeError("boom"),
        ),
        pytest.raises(ExtractionFailedError, match="markitdown:"),
    ):
        extract_markdown(p, content_type="text/csv")


# --- Tier 4: UTF-8 normalization workaround for #82 ------------------------


def test_text_shaped_files_are_utf8_normalized_before_markitdown(tmp_path: Path):
    """Regression for #82: text-shaped files (.ics/.vcf/.csv/.json/.xml) get
    normalized to UTF-8 before MarkItDown sees them, so the converter chain's
    PlainTextConverter (which defaults to ASCII) doesn't crash on common
    non-ASCII bytes from real calendar exports."""
    from maildb.ingest.extraction import _markitdown_convert  # noqa: PLC0415

    p = tmp_path / "invite.ics"
    # \xe2\x80\x99 is the UTF-8 right-single-quotation-mark — exact byte that
    # crashes upstream.
    p.write_bytes(b"BEGIN:VCALENDAR\r\nSUMMARY:Q3 \xe2\x80\x99 review\r\nEND:VCALENDAR\r\n")

    captured_calls: list[tuple[str, str | None]] = []

    def fake_run(
        path_str: str,
        *,
        force_charset: str | None = None,
        force_extension: str | None = None,
    ) -> str:
        captured_calls.append((path_str, force_charset))
        # The file MarkItDown sees must decode cleanly as UTF-8.
        text = Path(path_str).read_text(encoding="utf-8")
        assert "Q3" in text
        return "# normalized"

    with patch("maildb.ingest.extraction._markitdown_run", side_effect=fake_run):
        markdown, ver = _markitdown_convert(p, "calendar")

    assert markdown == "# normalized"
    assert ver.startswith("markitdown==")
    assert len(captured_calls) == 1, "_markitdown_run was not called once"
    captured_path, captured_charset = captured_calls[0]
    # MarkItDown sees a different (normalized) path than the input.
    assert captured_path != str(p)
    assert captured_path.endswith(".ics")
    # And UTF-8 was forced explicitly — this is the production-bug-catching
    # assertion that the byte-normalization alone was not.
    assert captured_charset == "utf-8"
    # The temp file is cleaned up after the call.
    assert not Path(captured_path).exists()


def test_binary_files_skip_utf8_normalization(tmp_path: Path):
    """Binary formats (.xls, .pages) must NOT be UTF-8 normalized — decoding
    and re-encoding bytes would corrupt the format. They pass through to
    MarkItDown without forcing a charset."""
    from maildb.ingest.extraction import _markitdown_convert  # noqa: PLC0415

    p = tmp_path / "data.xls"
    p.write_bytes(b"\xd0\xcf\x11\xe0fake")  # OLE2 magic, not valid UTF-8

    captured_calls: list[tuple[str, str | None]] = []

    def fake_run(
        path_str: str,
        *,
        force_charset: str | None = None,
        force_extension: str | None = None,
    ) -> str:
        captured_calls.append((path_str, force_charset))
        return "# xls content"

    with patch("maildb.ingest.extraction._markitdown_run", side_effect=fake_run):
        _markitdown_convert(p, "xls_legacy")

    assert captured_calls == [(str(p), None)]


def test_markitdown_run_text_passes_explicit_utf8_streaminfo(tmp_path: Path):
    """End-to-end against real MarkItDown: when force_charset='utf-8' is set,
    a tiny ICS containing a non-ASCII byte parses without crashing — even
    though auto-detection on small input might settle on ASCII. This is the
    direct regression test for #82."""
    from maildb.ingest.extraction import _markitdown_run  # noqa: PLC0415

    p = tmp_path / "x.ics"
    p.write_bytes(b"BEGIN:VCALENDAR\r\nSUMMARY:Q3 \xe2\x80\x99 review\r\nEND:VCALENDAR\r\n")
    md = _markitdown_run(str(p), force_charset="utf-8")
    assert "Q3" in md or "VCALENDAR" in md


# --- PR #84 review fix: dispatch by bucket, not by file suffix ------------


def test_markitdown_convert_applies_workaround_for_mime_routed_files(tmp_path: Path):
    """PR #84 review finding: the UTF-8 workaround was keyed on path.suffix,
    but extract_markdown routes by MIME content_type. An attachment with
    content_type='text/calendar' but a non-ICS filename (e.g. 'invite.dat')
    skipped the workaround and crashed on the first non-ASCII byte. The fix
    drives the workaround off the bucket regardless of input filename."""
    from maildb.ingest.extraction import _markitdown_convert  # noqa: PLC0415

    p = tmp_path / "invite.dat"  # deliberately wrong-shaped suffix
    p.write_bytes(b"BEGIN:VCALENDAR\r\nSUMMARY:Q3 \xe2\x80\x99 review\r\nEND:VCALENDAR\r\n")

    captured: list[tuple[str, str | None, str | None]] = []

    def fake_run(
        path_str: str,
        *,
        force_charset: str | None = None,
        force_extension: str | None = None,
    ) -> str:
        captured.append((path_str, force_charset, force_extension))
        return "# ok"

    with patch("maildb.ingest.extraction._markitdown_run", side_effect=fake_run):
        _markitdown_convert(p, "calendar")

    assert len(captured) == 1
    captured_path, charset, ext = captured[0]
    # Temp file uses bucket-canonical suffix, not the misleading input suffix.
    assert captured_path.endswith(".ics")
    # Workaround applied regardless of input filename.
    assert charset == "utf-8"
    # Explicit extension passed to MarkItDown so converter routing is robust.
    assert ext == ".ics"


@pytest.mark.parametrize(
    ("bucket", "expected_suffix"),
    [
        ("calendar", ".ics"),
        ("csv", ".csv"),
        ("json", ".json"),
        ("xml", ".xml"),
        ("vcard", ".vcf"),
    ],
)
def test_markitdown_convert_uses_bucket_canonical_suffix_for_text_buckets(
    tmp_path: Path, bucket: str, expected_suffix: str
):
    """For every text-shaped bucket, the workaround applies regardless of
    input filename: temp file gets the canonical suffix, charset is forced
    to utf-8, and extension is passed through to MarkItDown explicitly."""
    from maildb.ingest.extraction import _markitdown_convert  # noqa: PLC0415

    p = tmp_path / "wrongname.bin"
    p.write_bytes(b"placeholder")

    captured: list[tuple[str, str | None, str | None]] = []

    def fake_run(
        path_str: str,
        *,
        force_charset: str | None = None,
        force_extension: str | None = None,
    ) -> str:
        captured.append((path_str, force_charset, force_extension))
        return "# ok"

    with patch("maildb.ingest.extraction._markitdown_run", side_effect=fake_run):
        _markitdown_convert(p, bucket)

    captured_path, charset, ext = captured[0]
    assert captured_path.endswith(expected_suffix)
    assert charset == "utf-8"
    assert ext == expected_suffix


def test_markitdown_convert_binary_buckets_unaffected(tmp_path: Path):
    """Binary buckets (xls_legacy, pages) must continue to pass through to
    MarkItDown without forcing charset or extension — the workaround would
    corrupt their bytes."""
    from maildb.ingest.extraction import _markitdown_convert  # noqa: PLC0415

    p = tmp_path / "data.xls"
    p.write_bytes(b"\xd0\xcf\x11\xe0fake")

    captured: list[tuple[str, str | None, str | None]] = []

    def fake_run(
        path_str: str,
        *,
        force_charset: str | None = None,
        force_extension: str | None = None,
    ) -> str:
        captured.append((path_str, force_charset, force_extension))
        return "# ok"

    with patch("maildb.ingest.extraction._markitdown_run", side_effect=fake_run):
        _markitdown_convert(p, "xls_legacy")

    assert captured == [(str(p), None, None)]


def test_extract_markdown_passes_bucket_to_markitdown_convert(tmp_path: Path):
    """extract_markdown invokes _markitdown_convert with the resolved bucket
    so the suffix-independent workaround can fire correctly. Without this,
    MIME-routed files skip the #82 workaround (PR #84 review finding)."""
    p = tmp_path / "weird_name.dat"
    p.write_bytes(b"placeholder")

    captured: list[tuple[Path, str]] = []

    def fake_convert(path: Path, bucket: str) -> tuple[str, str]:
        captured.append((path, bucket))
        return ("# md", "markitdown==0.1.0")

    with patch("maildb.ingest.extraction._markitdown_convert", side_effect=fake_convert):
        extract_markdown(p, content_type="text/calendar")

    assert captured == [(p, "calendar")]


def test_normalize_to_utf8_temp_replaces_invalid_bytes(tmp_path: Path):
    """The UTF-8 normalizer round-trips valid UTF-8 unchanged and substitutes
    U+FFFD for genuinely invalid byte sequences (errors='replace' contract)."""
    from maildb.ingest.extraction import _normalize_to_utf8_temp  # noqa: PLC0415

    valid = tmp_path / "valid.ics"
    valid.write_bytes("Q3 \u2019 review".encode())
    out1 = _normalize_to_utf8_temp(valid)
    try:
        assert out1.read_text(encoding="utf-8") == "Q3 \u2019 review"
    finally:
        out1.unlink(missing_ok=True)

    invalid = tmp_path / "bad.ics"
    invalid.write_bytes(b"hello\xffworld")
    out2 = _normalize_to_utf8_temp(invalid)
    try:
        text = out2.read_text(encoding="utf-8")
        assert "hello" in text
        assert "world" in text
        assert "�" in text
    finally:
        out2.unlink(missing_ok=True)
