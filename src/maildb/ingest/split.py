from __future__ import annotations

import shutil
from pathlib import Path

import structlog

logger = structlog.get_logger()

MBOX_FROM_PREFIX = b"From "


def split_mbox(
    mbox_path: Path | str,
    *,
    output_dir: Path | str,
    chunk_size_bytes: int = 50 * 1024 * 1024,
) -> list[Path]:
    """Split an mbox file into chunks of approximately chunk_size_bytes.

    Returns list of chunk file paths.
    """
    mbox_path = Path(mbox_path)
    output_dir = Path(output_dir)

    # Clean and recreate output directory
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    chunks: list[Path] = []
    chunk_idx = 0
    current_chunk: list[bytes] = []
    current_size = 0
    message_buffer: list[bytes] = []
    in_message = False

    with mbox_path.open("rb") as f:
        for line in f:
            if line.startswith(MBOX_FROM_PREFIX) and in_message:
                # End of previous message — flush it to current chunk
                msg_bytes = b"".join(message_buffer)
                current_chunk.append(msg_bytes)
                current_size += len(msg_bytes)
                message_buffer = []

                # Check if chunk is full
                if current_size >= chunk_size_bytes:
                    chunk_path = _write_chunk(output_dir, chunk_idx, current_chunk)
                    chunks.append(chunk_path)
                    chunk_idx += 1
                    current_chunk = []
                    current_size = 0

            message_buffer.append(line)
            in_message = True

    # Flush last message
    if message_buffer:
        current_chunk.append(b"".join(message_buffer))

    # Flush last chunk
    if current_chunk:
        chunk_path = _write_chunk(output_dir, chunk_idx, current_chunk)
        chunks.append(chunk_path)

    logger.info("split_complete", chunks=len(chunks), source=str(mbox_path))
    return chunks


def _write_chunk(output_dir: Path, idx: int, messages: list[bytes]) -> Path:
    chunk_path = output_dir / f"chunk_{idx:06d}.mbox"
    with chunk_path.open("wb") as f:
        for msg in messages:
            f.write(msg)
    return chunk_path
