import mailbox
from pathlib import Path

from maildb.ingest.split import split_mbox


def _make_mbox(path: Path, count: int) -> Path:
    """Create a test mbox with `count` messages."""
    mbox = mailbox.mbox(str(path))
    for i in range(count):
        msg = mailbox.mboxMessage()
        msg.set_payload(f"Body of message {i}")
        msg["From"] = f"sender{i}@example.com"
        msg["Subject"] = f"Message {i}"
        msg["Message-ID"] = f"<msg-{i}@example.com>"
        mbox.add(msg)
    mbox.close()
    return path


def test_split_mbox_creates_chunks(tmp_path):
    mbox_path = _make_mbox(tmp_path / "test.mbox", count=20)
    output_dir = tmp_path / "chunks"
    chunks = split_mbox(mbox_path, output_dir=output_dir, chunk_size_bytes=500)
    assert len(chunks) > 1
    total_messages = 0
    for chunk_path in chunks:
        assert chunk_path.exists()
        mbox = mailbox.mbox(str(chunk_path))
        total_messages += len(mbox)
        mbox.close()
    assert total_messages == 20


def test_split_mbox_single_chunk(tmp_path):
    mbox_path = _make_mbox(tmp_path / "small.mbox", count=3)
    output_dir = tmp_path / "chunks"
    chunks = split_mbox(mbox_path, output_dir=output_dir, chunk_size_bytes=10_000_000)
    assert len(chunks) == 1


def test_split_mbox_cleans_output_dir(tmp_path):
    mbox_path = _make_mbox(tmp_path / "test.mbox", count=5)
    output_dir = tmp_path / "chunks"
    output_dir.mkdir()
    (output_dir / "stale_chunk.mbox").write_text("old data")
    split_mbox(mbox_path, output_dir=output_dir, chunk_size_bytes=500)
    assert not (output_dir / "stale_chunk.mbox").exists()
