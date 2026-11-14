"""Enqueue URL sources for worker-side fetch and extraction."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import Document, KnowledgeBase
from app.modules.ingestion import storage as blob_storage
from app.modules.ingestion.service import _require_editor, _to_document_read
from app.modules.ingestion.upload_phases import commit_incoming_records, commit_stored_and_enqueue, promote_version_bytes
from app.modules.ingestion.url_validation import validate_url_for_enqueue
from app.modules.ingestion.validation import ValidatedUpload
from app.schemas import DocumentUploadResult


def enqueue_url_source(
    db: Session,
    *,
    kb_id: uuid.UUID,
    user_id: uuid.UUID,
    url: str,
    title: str | None,
    max_pages: int,
    depth: int,
) -> DocumentUploadResult:
    canonical = validate_url_for_enqueue(url)
    max_pages = min(max_pages, 20)
    depth = min(depth, 1)
    display = (title or canonical).strip() or canonical
    kb = _require_editor(db, kb_id, user_id)

    placeholder = b""
    upload_id = uuid.uuid4()
    incoming_rel = blob_storage.write_incoming(placeholder, upload_id)
    content_hash = blob_storage.sha256_bytes(canonical.encode("utf-8"))

    document = Document(
        knowledge_base_id=kb.id,
        title=display[:500],
        source_type="web",
        source_uri=canonical,
        status="queued",
        created_by=user_id,
    )
    db.add(document)
    db.flush()
    db.commit()
    db.refresh(document)

    validated = ValidatedUpload(
        source_type="web",
        mime_type="text/html",
        display_filename=display[:500],
        safe_basename=canonical[:255],
    )
    version = commit_incoming_records(
        db,
        kb=kb,
        document=document,
        validated=validated,
        byte_size=0,
        incoming_rel=incoming_rel,
    )
    final_rel = promote_version_bytes(version)
    job = commit_stored_and_enqueue(
        db,
        kb=kb,
        document=document,
        version=version,
        final_rel=final_rel,
        content_hash=content_hash,
        user_id=user_id,
        source_type="web",
        display_filename=validated.display_filename,
        original_filename=validated.safe_basename,
        dedup_hash=None,
        payload_extras={
            "source_url": canonical,
            "max_pages": max_pages,
            "depth": depth,
        },
    )
    return DocumentUploadResult(document=_to_document_read(db, document), reused=False, job_id=job.id)