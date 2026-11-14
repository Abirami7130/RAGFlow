"""Document upload orchestration, deduplication, and lifecycle records."""

from __future__ import annotations

import base64
import logging
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import APIError
from app.models import CanonicalBlock, Document, DocumentVersion, Job, KnowledgeBase, KnowledgeBaseMember, ProcessingRun
from app.modules.chunking.service import chunk_count_for_version, processing_run_for_version
from app.modules.ingestion import storage as blob_storage
from app.modules.ingestion.recovery import recover_version_storage
from app.modules.ingestion.user_retry import request_user_retry_on_version
from app.modules.ingestion.upload_phases import (
    abandon_upload_version,
    commit_incoming_records,
    commit_stored_and_enqueue,
    promote_version_bytes,
)
from app.modules.ingestion.validation import ValidatedUpload, validate_byte_size, validate_magic_bytes, validate_upload_filename
from app.schemas import CanonicalBlockRead, DocumentRead, DocumentUploadResult

logger = logging.getLogger(__name__)

def _require_editor(db: Session, kb_id: uuid.UUID, user_id: uuid.UUID) -> KnowledgeBase:
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None or kb.deleted_at is not None:
        raise APIError(404, "not_found", "Knowledge base not found.")
    member = db.get(KnowledgeBaseMember, (kb_id, user_id))
    if member is None:
        raise APIError(404, "not_found", "Knowledge base not found.")
    if member.role == "viewer":
        raise APIError(403, "forbidden", "You do not have access to this action.")
    return kb


def _to_document_read(db: Session, document: Document) -> DocumentRead:
    pending_status = None
    if document.pending_run_id:
        run = db.get(ProcessingRun, document.pending_run_id)
        pending_status = run.status if run else None

    version_id = document.preview_version_id or document.pending_version_id or document.current_version_id
    byte_size = None
    if version_id:
        version = db.get(DocumentVersion, version_id)
        if version:
            byte_size = version.byte_size

    current_version_number = None
    if document.current_version_id:
        current = db.get(DocumentVersion, document.current_version_id)
        if current:
            current_version_number = current.version_number

    chunk_version_id = document.preview_version_id or document.current_version_id
    run_for_chunks = processing_run_for_version(db, chunk_version_id) if chunk_version_id else None
    chunk_preview_run_id = document.pending_run_id or (run_for_chunks.id if run_for_chunks else None)

    return DocumentRead(
        id=document.id,
        knowledge_base_id=document.knowledge_base_id,
        title=document.title,
        source_type=document.source_type,
        source_uri=document.source_uri,
        status=document.status,
        error_code=document.error_code,
        error_detail=document.error_detail,
        current_version_id=document.current_version_id,
        current_version_number=current_version_number,
        preview_version_id=document.preview_version_id,
        pending_version_id=document.pending_version_id,
        pending_run_id=document.pending_run_id,
        pending_status=pending_status,
        chunk_preview_run_id=chunk_preview_run_id,
        byte_size=byte_size,
        chunk_count=(
            chunk_count_for_version(db, document.preview_version_id)
            if document.preview_version_id
            else chunk_count_for_version(db, document.current_version_id)
            if document.current_version_id
            else 0
        ),
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


def _find_reusable_version(db: Session, *, document_id: uuid.UUID, content_hash: str) -> DocumentVersion | None:
    return db.scalar(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document_id, DocumentVersion.content_hash == content_hash)
        .order_by(DocumentVersion.version_number.desc())
    )


def _find_kb_duplicate_document(
    db: Session,
    *,
    kb_id: uuid.UUID,
    dedup_hash: str,
    excluding_document_id: uuid.UUID | None,
) -> Document | None:
    version = db.scalar(
        select(DocumentVersion)
        .where(DocumentVersion.knowledge_base_id == kb_id, DocumentVersion.dedup_hash == dedup_hash)
        .order_by(DocumentVersion.created_at.desc())
    )
    if version is None:
        return None
    if excluding_document_id and version.document_id == excluding_document_id:
        return None
    doc = db.get(Document, version.document_id)
    if doc is None or doc.deleted_at is not None:
        return None
    return doc


def _document_reused(db: Session, *, kb_id: uuid.UUID, content_hash: str) -> DocumentUploadResult:
    version = db.scalar(
        select(DocumentVersion)
        .where(DocumentVersion.knowledge_base_id == kb_id, DocumentVersion.dedup_hash == content_hash)
        .order_by(DocumentVersion.created_at.desc())
    )
    if version is None:
        raise APIError(409, "conflict", "Upload conflicted with a concurrent submission.")
    doc = db.get(Document, version.document_id)
    if doc is None:
        raise APIError(409, "conflict", "Upload conflicted with a concurrent submission.")
    return DocumentUploadResult(document=_to_document_read(db, doc), reused=True, job_id=None)


def _prepare_upload(filename: str, title: str | None, data: bytes) -> tuple[ValidatedUpload, str]:
    validated = validate_upload_filename(filename, title=title)
    settings = get_settings()
    validate_byte_size(len(data), limit=settings.max_upload_bytes)
    validate_magic_bytes(validated.source_type, data[:8192])
    return validated, blob_storage.sha256_bytes(data)


def _execute_upload_pipeline(
    db: Session,
    *,
    kb: KnowledgeBase,
    document: Document,
    validated: ValidatedUpload,
    content_hash: str,
    data: bytes,
    incoming_rel: str,
    user_id: uuid.UUID,
) -> DocumentUploadResult:
    version = commit_incoming_records(
        db,
        kb=kb,
        document=document,
        validated=validated,
        byte_size=len(data),
        incoming_rel=incoming_rel,
    )
    final_rel = promote_version_bytes(version)
    try:
        job = commit_stored_and_enqueue(
            db,
            kb=kb,
            document=document,
            version=version,
            final_rel=final_rel,
            content_hash=content_hash,
            user_id=user_id,
            source_type=validated.source_type,
            display_filename=validated.display_filename,
            original_filename=validated.safe_basename,
        )
        return DocumentUploadResult(document=_to_document_read(db, document), reused=False, job_id=job.id)
    except IntegrityError:
        db.rollback()
        abandon_upload_version(db, document=document, version=version, incoming_rel=incoming_rel)
        raise


def upload_new_document(
    db: Session,
    *,
    kb_id: uuid.UUID,
    user_id: uuid.UUID,
    filename: str,
    title: str | None,
    data: bytes,
    force: bool = False,
) -> DocumentUploadResult:
    if force:
        raise APIError(400, "validation_error", "force is only supported on replacement uploads.")

    validated, content_hash = _prepare_upload(filename, title, data)
    kb = _require_editor(db, kb_id, user_id)

    duplicate_doc = _find_kb_duplicate_document(db, kb_id=kb_id, dedup_hash=content_hash, excluding_document_id=None)
    if duplicate_doc is not None:
        raise APIError(
            409,
            "duplicate_content",
            "This content already exists in the knowledge base.",
            {"document_id": str(duplicate_doc.id)},
        )

    upload_id = uuid.uuid4()
    incoming_rel = blob_storage.write_incoming(data, upload_id)

    document = Document(
        knowledge_base_id=kb.id,
        title=validated.display_filename,
        source_type=validated.source_type,
        status="queued",
        created_by=user_id,
    )
    db.add(document)

    try:
        db.flush()
        db.commit()
        db.refresh(document)
    except Exception:
        db.rollback()
        blob_storage.unlink_if_unreferenced(blob_storage.abs_path(incoming_rel))
        raise

    try:
        return _execute_upload_pipeline(
            db,
            kb=kb,
            document=document,
            validated=validated,
            content_hash=content_hash,
            data=data,
            incoming_rel=incoming_rel,
            user_id=user_id,
        )
    except IntegrityError:
        return _document_reused(db, kb_id=kb_id, content_hash=content_hash)


def upload_replacement_version(
    db: Session,
    *,
    document_id: uuid.UUID,
    user_id: uuid.UUID,
    filename: str,
    title: str | None,
    data: bytes,
    force: bool = False,
) -> DocumentUploadResult:
    validated, content_hash = _prepare_upload(filename, title, data)

    document = db.scalar(select(Document).where(Document.id == document_id).with_for_update())
    if document is None or document.deleted_at is not None:
        raise APIError(404, "not_found", "Document not found.")
    kb = _require_editor(db, document.knowledge_base_id, user_id)

    if document.pending_run_id is not None:
        job = db.scalar(
            select(Job).where(
                Job.job_type == "ingest_document",
                Job.status.in_(("pending", "running")),
                Job.idempotency_key == f"processing_run:{document.pending_run_id}",
            )
        )
        details = {"job_id": str(job.id)} if job else {}
        raise APIError(409, "ingest_in_progress", "A document ingest is already in progress.", details)

    reusable = _find_reusable_version(db, document_id=document.id, content_hash=content_hash)
    if reusable is not None:
        if force:
            job = request_user_retry_on_version(
                db,
                document_id=document.id,
                version_id=reusable.id,
                user_id=user_id,
            )
            db.refresh(document)
            return DocumentUploadResult(
                document=_to_document_read(db, document),
                reused=True,
                job_id=job.id,
            )
        return DocumentUploadResult(document=_to_document_read(db, document), reused=True, job_id=None)

    duplicate_doc = _find_kb_duplicate_document(
        db,
        kb_id=document.knowledge_base_id,
        dedup_hash=content_hash,
        excluding_document_id=document.id,
    )
    if duplicate_doc is not None:
        raise APIError(
            409,
            "duplicate_content",
            "This content already exists in the knowledge base.",
            {"document_id": str(duplicate_doc.id)},
        )

    if title:
        document.title = validated.display_filename

    upload_id = uuid.uuid4()
    incoming_rel = blob_storage.write_incoming(data, upload_id)

    try:
        db.commit()
        db.refresh(document)
        return _execute_upload_pipeline(
            db,
            kb=kb,
            document=document,
            validated=validated,
            content_hash=content_hash,
            data=data,
            incoming_rel=incoming_rel,
            user_id=user_id,
        )
    except IntegrityError:
        db.rollback()
        document = db.scalar(select(Document).where(Document.id == document_id).with_for_update())
        if document is None:
            raise APIError(409, "conflict", "Upload conflicted with a concurrent submission.")
        reusable = _find_reusable_version(db, document_id=document.id, content_hash=content_hash)
        if reusable is not None:
            return DocumentUploadResult(document=_to_document_read(db, document), reused=True, job_id=None)
        raise APIError(409, "conflict", "Upload conflicted with a concurrent submission.")
    except Exception:
        db.rollback()
        blob_storage.unlink_if_unreferenced(blob_storage.abs_path(incoming_rel))
        raise


def get_document(db: Session, document_id: uuid.UUID, user_id: uuid.UUID) -> DocumentRead:
    document = db.get(Document, document_id)
    if document is None or document.deleted_at is not None:
        raise APIError(404, "not_found", "Document not found.")
    member = db.get(KnowledgeBaseMember, (document.knowledge_base_id, user_id))
    if member is None:
        raise APIError(404, "not_found", "Document not found.")
    return _to_document_read(db, document)


def list_version_blocks(
    db: Session,
    *,
    version_id: uuid.UUID,
    user_id: uuid.UUID,
    limit: int,
    cursor: str | None,
) -> tuple[list[CanonicalBlockRead], str | None]:
    version = db.get(DocumentVersion, version_id)
    if version is None:
        raise APIError(404, "not_found", "Document not found.")
    document = db.get(Document, version.document_id)
    if document is None or document.deleted_at is not None:
        raise APIError(404, "not_found", "Document not found.")
    member = db.get(KnowledgeBaseMember, (document.knowledge_base_id, user_id))
    if member is None:
        raise APIError(404, "not_found", "Document not found.")

    after_sequence = _decode_block_cursor(cursor) if cursor else None
    query = select(CanonicalBlock).where(CanonicalBlock.document_version_id == version.id)
    if after_sequence is not None:
        query = query.where(CanonicalBlock.sequence > after_sequence)
    rows = list(db.scalars(query.order_by(CanonicalBlock.sequence).limit(limit + 1)).all())
    page = rows[:limit]
    next_cursor = _encode_block_cursor(page[-1].sequence) if len(rows) > limit and page else None
    items = [
        CanonicalBlockRead(
            id=row.id,
            sequence=row.sequence,
            block_type=row.block_type,
            text=row.text,
            locator=row.locator,
            metadata=row.block_metadata or {},
        )
        for row in page
    ]
    return items, next_cursor


def _encode_block_cursor(sequence: int) -> str:
    return base64.urlsafe_b64encode(str(sequence).encode()).decode()


def _decode_block_cursor(cursor: str) -> int:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        sequence = int(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise APIError(400, "validation_error", "Cursor is invalid.") from exc
    if sequence < 0:
        raise APIError(400, "validation_error", "Cursor is invalid.")
    return sequence


def ensure_version_stored(db: Session, version: DocumentVersion) -> DocumentVersion:
    if version.storage_state == "stored":
        return version
    if recover_version_storage(db, version):
        db.refresh(version)
    return version
