"""Content-addressed blob storage. Raw bytes are never evicted — this is the historical dataset."""
from __future__ import annotations

import hashlib
from pathlib import Path

from indian_mf_mcp import config


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def blob_path_for(sha256: str) -> Path:
    return config.BLOB_DIR / sha256[:2] / sha256[2:4] / sha256


def put(data: bytes) -> tuple[str, Path]:
    """Store bytes, return (sha256, path). Idempotent."""
    digest = sha256_of(data)
    path = blob_path_for(digest)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return digest, path


def get(sha256: str) -> bytes:
    return blob_path_for(sha256).read_bytes()


def exists(sha256: str) -> bool:
    return blob_path_for(sha256).exists()
