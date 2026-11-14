"""Durable filesystem storage for uploaded document bytes."""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.core.config import get_settings

logger = logging.getLogger(__name__)

INCOMING_SUBDIR = "incoming"


@dataclass(frozen=True, slots=True)
class FileIdentity:
    st_dev: int
    st_ino: int


def storage_root() -> Path:
    root = Path(get_settings().storage_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    incoming = root / INCOMING_SUBDIR
    incoming.mkdir(parents=True, exist_ok=True)
    return root


def rel_incoming_path(upload_id: uuid.UUID) -> str:
    return f"{INCOMING_SUBDIR}/{upload_id}"


def rel_final_path(kb_id: uuid.UUID, document_id: uuid.UUID, version_id: uuid.UUID) -> str:
    return f"{kb_id}/{document_id}/{version_id}"


def abs_path(rel_path: str) -> Path:
    root = storage_root()
    normalized = rel_path.replace("\\", "/").lstrip("/")
    if not normalized or ".." in Path(normalized).parts:
        raise ValueError(f"Unsafe storage path: {rel_path!r}")
    resolved = (root / normalized).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"Storage path escapes STORAGE_DIR: {rel_path!r}")
    return resolved


def fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_incoming(data: bytes, upload_id: uuid.UUID) -> str:
    root = storage_root()
    rel = rel_incoming_path(upload_id)
    dest = abs_path(rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    fsync_file(dest)
    fsync_directory(dest.parent)
    return rel


def file_identity(path: Path) -> FileIdentity | None:
    if not path.exists():
        return None
    stat = path.stat()
    return FileIdentity(st_dev=stat.st_dev, st_ino=stat.st_ino)


def same_inode(left: Path, right: Path) -> bool:
    left_id = file_identity(left)
    right_id = file_identity(right)
    if left_id is None or right_id is None:
        return False
    return left_id.st_dev == right_id.st_dev and left_id.st_ino == right_id.st_ino


def promote_incoming_to_final(
    *,
    incoming_rel: str,
    final_rel: str,
) -> None:
    incoming = abs_path(incoming_rel)
    final = abs_path(final_rel)
    final.parent.mkdir(parents=True, exist_ok=True)
    if incoming.exists():
        os.replace(incoming, final)
    elif not final.exists():
        raise FileNotFoundError(f"Neither incoming nor final file exists for promotion: {incoming_rel} -> {final_rel}")
    fsync_file(final)
    fsync_directory(final.parent)
    incoming_dir = incoming.parent
    if incoming_dir.exists():
        fsync_directory(incoming_dir)


def finalize_same_inode_cleanup(*, incoming_rel: str, final_rel: str) -> None:
    """After a crash left hard-linked or duplicated incoming + final names."""
    incoming = abs_path(incoming_rel)
    final = abs_path(final_rel)
    if incoming.exists() and final.exists() and same_inode(incoming, final):
        fsync_directory(final.parent)
        if incoming_dir := incoming.parent:
            if incoming_dir.exists():
                fsync_directory(incoming_dir)
        incoming.unlink(missing_ok=True)
        fsync_directory(incoming.parent)
    elif final.exists():
        fsync_directory(final.parent)
        if incoming.parent.exists():
            fsync_directory(incoming.parent)


def unlink_if_unreferenced(path: Path) -> None:
    if path.exists():
        path.unlink()


def unlink_promoted_artifacts(
    *,
    kb_id: uuid.UUID,
    document_id: uuid.UUID,
    version_id: uuid.UUID,
    incoming_rel: str,
) -> None:
    """Remove filesystem bytes left behind when a DB transaction rolls back after promotion."""
    unlink_if_unreferenced(abs_path(incoming_rel))
    final_rel = rel_final_path(kb_id, document_id, version_id)
    unlink_if_unreferenced(abs_path(final_rel))


def read_bytes(rel_path: str) -> bytes:
    return abs_path(rel_path).read_bytes()
