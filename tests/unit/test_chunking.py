from __future__ import annotations

import pytest

from maildb.ingest.chunking import chunk_markdown


def test_chunk_flat_short_doc_single_chunk():
    md = "Just a few words here, no headings."
    chunks = chunk_markdown(md)
    assert len(chunks) == 1
    assert chunks[0].chunk_index == 0
    assert chunks[0].heading_path is None
    assert chunks[0].text.startswith("Just a few")


def test_chunk_with_headings_preserves_path():
    md = (
        "# Overview\n\n"
        "Top-level text.\n\n"
        "## Payment Terms\n\n"
        "Net 30 days.\n\n"
        "### Late Fees\n\n"
        "5% per month.\n"
    )
    chunks = chunk_markdown(md)
    # We expect at least three chunks (the three sections) with heading paths.
    paths = [c.heading_path for c in chunks]
    assert "Overview" in paths
    assert any(p and p.startswith("Overview > Payment Terms") for p in paths)
    assert any(p and "Late Fees" in p for p in paths)


def test_chunk_respects_token_cap():
    # Very long single paragraph exceeds the cap; chunker must split.
    para = "word " * 5000
    chunks = chunk_markdown(para, max_tokens=256)
    assert len(chunks) > 1
    for c in chunks:
        assert c.token_count <= 256


def test_chunk_small_sections_merge_under_soft_floor():
    md = "## A\n\ntiny\n\n## B\n\ntiny\n\n## C\n\ntiny\n"
    chunks = chunk_markdown(md, max_tokens=1024, min_tokens=128)
    # Expect a single merged chunk since each section is tiny.
    assert len(chunks) == 1


def test_chunk_determinism():
    md = "# H\n\nSection text.\n\n## Sub\n\nMore text.\n"
    assert chunk_markdown(md) == chunk_markdown(md)


def test_chunk_indexes_are_sequential():
    md = "# A\n\n" + ("word " * 2000) + "\n\n# B\n\nshort tail\n"
    chunks = chunk_markdown(md, max_tokens=256)
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))


def test_chunk_empty_input_returns_empty_list():
    assert chunk_markdown("") == []


@pytest.mark.parametrize("md", ["   \n\n   \n", "\n\n\n"])
def test_chunk_whitespace_only_returns_empty(md):
    assert chunk_markdown(md) == []
