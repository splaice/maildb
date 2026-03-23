from pathlib import Path

from maildb.ingest.attachments import hash_attachment, storage_path_for, store_attachment


def test_hash_attachment():
    data = b"hello world"
    h = hash_attachment(data)
    assert len(h) == 64  # SHA-256 hex
    assert h == hash_attachment(data)  # deterministic


def test_storage_path_for():
    h = "abcdef1234567890" + "0" * 48
    path = storage_path_for(h, "report.pdf")
    assert path == Path("ab/cd") / f"{h}.pdf"


def test_storage_path_for_no_extension():
    h = "abcdef1234567890" + "0" * 48
    path = storage_path_for(h, "README")
    assert path == Path("ab/cd") / f"{h}"


def test_store_attachment_writes_file(tmp_path):
    data = b"test content"
    h = hash_attachment(data)
    rel_path = store_attachment(data, h, "test.txt", base_dir=tmp_path)
    full_path = tmp_path / rel_path
    assert full_path.exists()
    assert full_path.read_bytes() == data


def test_store_attachment_deduplicates(tmp_path):
    data = b"same content"
    h = hash_attachment(data)
    path1 = store_attachment(data, h, "first.txt", base_dir=tmp_path)
    path2 = store_attachment(data, h, "second.txt", base_dir=tmp_path)
    assert path1 == path2  # same hash = same path
