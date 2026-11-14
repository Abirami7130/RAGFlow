"""Upload filename, size, extension, and magic-byte validation."""

from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from app.core.errors import APIError

UPLOAD_SOURCE_TYPES = frozenset({"pdf", "docx", "txt", "md", "pptx", "csv", "xlsx", "json"})

_EXTENSION_TO_SOURCE: dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".txt": "txt",
    ".md": "md",
    ".markdown": "md",
    ".pptx": "pptx",
    ".csv": "csv",
    ".xlsx": "xlsx",
    ".json": "json",
}

_SOURCE_TO_MIME: dict[str, str] = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
    "md": "text/markdown",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "json": "application/json",
}

_UNSAFE_FILENAME = re.compile(r"[\x00\\/]+")


@dataclass(frozen=True, slots=True)
class ValidatedUpload:
    source_type: str
    mime_type: str
    display_filename: str
    safe_basename: str


def _reject_filename(filename: str) -> None:
    name = (filename or "").strip()
    if not name:
        raise APIError(400, "validation_error", "A filename is required.")
    if _UNSAFE_FILENAME.search(name):
        raise APIError(400, "validation_error", "The filename contains invalid characters.")
    if name in {".", ".."} or ".." in Path(name).parts:
        raise APIError(400, "validation_error", "The filename is not allowed.")


def _extension(filename: str) -> str:
    return Path(filename).suffix.lower()


def validate_upload_filename(filename: str, *, title: str | None) -> ValidatedUpload:
    _reject_filename(filename)
    ext = _extension(filename)
    source_type = _EXTENSION_TO_SOURCE.get(ext)
    if source_type is None:
        raise APIError(415, "unsupported_type", "This file type is not supported.")
    display = (title or Path(filename).name).strip() or Path(filename).name
    if not display:
        raise APIError(400, "validation_error", "A display title is required.")
    return ValidatedUpload(
        source_type=source_type,
        mime_type=_SOURCE_TO_MIME[source_type],
        display_filename=display[:500],
        safe_basename=Path(filename).name[:255],
    )


def validate_byte_size(byte_size: int, *, limit: int) -> None:
    if byte_size <= 0:
        raise APIError(400, "validation_error", "The uploaded file is empty.")
    if byte_size > limit:
        raise APIError(413, "file_too_large", "The file exceeds the 25 MB upload limit.")


def validate_magic_bytes(source_type: str, data: bytes) -> None:
    if source_type == "pdf":
        if not data.startswith(b"%PDF-"):
            raise APIError(415, "unsupported_type", "The file is not a valid PDF.")
        return
    if source_type in {"docx", "pptx", "xlsx"}:
        if not data.startswith(b"PK\x03\x04"):
            raise APIError(415, "mime_mismatch", "The file content does not match its extension.")
        try:
            with zipfile.ZipFile(BytesIO(data)) as archive:
                names = set(archive.namelist())
        except zipfile.BadZipFile as exc:
            raise APIError(415, "mime_mismatch", "The file content does not match its extension.") from exc
        required = {
            "docx": "[Content_Types].xml",
            "pptx": "[Content_Types].xml",
            "xlsx": "[Content_Types].xml",
        }[source_type]
        if required not in names:
            raise APIError(415, "mime_mismatch", "The file content does not match its extension.")
        return
    if source_type == "json":
        try:
            json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise APIError(415, "unsupported_type", "The file is not valid JSON.") from exc
