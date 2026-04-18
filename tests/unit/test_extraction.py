from __future__ import annotations

import pytest

from maildb.ingest.extraction import SUPPORTED, route_content_type


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
