from __future__ import annotations

import hashlib
from pathlib import Path


def hash_attachment(data: bytes) -> str:
    """Return SHA-256 hex digest of attachment bytes."""
    return hashlib.sha256(data).hexdigest()


def storage_path_for(sha256: str, filename: str) -> Path:
    """Compute the relative storage path for a content-addressed file."""
    ext = Path(filename).suffix  # includes the dot, or empty string
    prefix = Path(sha256[:2]) / sha256[2:4]
    return prefix / f"{sha256}{ext}"


def store_attachment(
    data: bytes,
    sha256: str,
    filename: str,
    *,
    base_dir: Path,
) -> Path:
    """Write attachment to disk if not already present. Returns relative path."""
    rel_path = storage_path_for(sha256, filename)
    full_path = base_dir / rel_path
    if not full_path.exists():
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_bytes(data)
    return rel_path
