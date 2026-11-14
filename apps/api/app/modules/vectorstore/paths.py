"""INDEX_DIR path helpers for vector indexes and run sidecars."""

from __future__ import annotations

import uuid
from pathlib import Path

from app.core.config import get_settings


def index_root() -> Path:
    root = Path(get_settings().index_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_under_index_root(rel_path: str) -> Path:
    root = index_root()
    normalized = rel_path.replace("\\", "/").lstrip("/")
    if not normalized or ".." in Path(normalized).parts:
        raise ValueError(f"Unsafe index path: {rel_path!r}")
    resolved = (root / normalized).resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"Index path escapes INDEX_DIR: {rel_path!r}")
    return resolved


def sidecar_rel_path(
    *,
    knowledge_base_id: uuid.UUID,
    index_id: uuid.UUID,
    processing_run_id: uuid.UUID,
) -> str:
    return f"{knowledge_base_id}/{index_id}/sidecars/{processing_run_id}.npz"


def sidecar_abs_path(
    *,
    knowledge_base_id: uuid.UUID,
    index_id: uuid.UUID,
    processing_run_id: uuid.UUID,
) -> Path:
    return resolve_under_index_root(
        sidecar_rel_path(
            knowledge_base_id=knowledge_base_id,
            index_id=index_id,
            processing_run_id=processing_run_id,
        )
    )


def staged_faiss_rel_path(
    *,
    knowledge_base_id: uuid.UUID,
    index_id: uuid.UUID,
    processing_run_id: uuid.UUID,
) -> str:
    return f"{knowledge_base_id}/{index_id}/staged/{processing_run_id}.faiss"


def live_faiss_rel_path(*, knowledge_base_id: uuid.UUID, index_id: uuid.UUID) -> str:
    return f"{knowledge_base_id}/{index_id}/index.faiss"


def live_faiss_abs_path(*, knowledge_base_id: uuid.UUID, index_id: uuid.UUID) -> Path:
    return resolve_under_index_root(live_faiss_rel_path(knowledge_base_id=knowledge_base_id, index_id=index_id))


def generation_stamp_rel_path(*, knowledge_base_id: uuid.UUID, index_id: uuid.UUID) -> str:
    return f"{knowledge_base_id}/{index_id}/generation"


def generation_stamp_abs_path(*, knowledge_base_id: uuid.UUID, index_id: uuid.UUID) -> Path:
    return resolve_under_index_root(
        generation_stamp_rel_path(knowledge_base_id=knowledge_base_id, index_id=index_id)
    )


def staged_faiss_abs_path(
    *,
    knowledge_base_id: uuid.UUID,
    index_id: uuid.UUID,
    processing_run_id: uuid.UUID,
) -> Path:
    return resolve_under_index_root(
        staged_faiss_rel_path(
            knowledge_base_id=knowledge_base_id,
            index_id=index_id,
            processing_run_id=processing_run_id,
        )
    )