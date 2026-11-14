"""Multi-commit upload transaction phases (incoming → promote → stored + enqueue)."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Document, DocumentVersion, Job, KnowledgeBase
from app.modules.ingestion import storage as blob_storage
from app.modules.ingestion.enqueue import enqueue_ingest
from app.modules.ingestion.validation import ValidatedUpload


def commit_incoming_records(
    db: Session,
    *,
    kb: KnowledgeBase,
    document: Document,
    validated: ValidatedUpload,
    byte_size: int,
    incoming_rel: str,
) -> DocumentVersion:
    current = db.scalar(
        select(func.max(DocumentVersion.version_number)).where(DocumentVersion.document_id == document.id)
    )
    version_number = (current or 0) + 1
    version = DocumentVersion(
        document_id=document.id,
        knowledge_base_id=kb.id,
        version_number=version_number,
        storage_path=incoming_rel,
        storage_state="incoming",
        byte_size=byte_size,
        mime_type=validated.mime_type,
        original_filename=validated.safe_basename,
    )
    db.add(version)
    db.flush()
    db.commit()
    db.refresh(version)
    db.refresh(document)
    return version


def promote_version_bytes(version: DocumentVersion) -> str:
    final_rel = blob_storage.rel_final_path(version.knowledge_base_id, version.document_id, version.id)
    blob_storage.promote_incoming_to_final(incoming_rel=version.storage_path, final_rel=final_rel)
    return final_rel


def commit_stored_and_enqueue(
    db: Session,
    *,
    kb: KnowledgeBase,
    document: Document,
    version: DocumentVersion,
    final_rel: str,
    content_hash: str,
    user_id: uuid.UUID,
    source_type: str,
    display_filename: str,
    original_filename: str,
    dedup_hash: str | None = "__upload__",
    payload_extras: dict | None = None,
) -> Job:
    locked_doc = db.scalar(select(Document).where(Document.id == document.id).with_for_update())
    locked_version = db.get(DocumentVersion, version.id)
    if locked_doc is None or locked_version is None:
        raise RuntimeError("upload phase lost committed version or document row")
    locked_version.storage_path = final_rel
    locked_version.content_hash = content_hash
    locked_version.dedup_hash = content_hash if dedup_hash == "__upload__" else dedup_hash
    locked_version.storage_state = "stored"
    job = enqueue_ingest(
        db,
        kb=kb,
        document=locked_doc,
        version=locked_version,
        user_id=user_id,
        source_type=source_type,
        display_filename=display_filename,
        original_filename=original_filename,
        payload_extras=payload_extras,
    )
    db.commit()
    db.refresh(locked_doc)
    db.refresh(locked_version)
    return job


def abandon_upload_version(
    db: Session,
    *,
    document: Document,
    version: DocumentVersion,
    incoming_rel: str,
) -> None:
    kb_id = version.knowledge_base_id
    document_id = document.id
    version_id = version.id
    db.delete(version)
    db.flush()
    remaining = db.scalar(
        select(func.count()).select_from(DocumentVersion).where(DocumentVersion.document_id == document_id)
    )
    if remaining == 0:
        db.delete(document)
    db.commit()
    blob_storage.unlink_promoted_artifacts(
        kb_id=kb_id,
        document_id=document_id,
        version_id=version_id,
        incoming_rel=incoming_rel,
    )
