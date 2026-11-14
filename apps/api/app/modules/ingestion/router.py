from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import APIError
from app.models import User
from app.modules.auth.router import get_current_user
from app.modules.ingestion.reprocess import reprocess_document
from app.modules.activation import rollback_document_serving, soft_delete_document
from app.modules.ingestion.service import (
    get_document,
    list_version_blocks,
    upload_new_document,
    upload_replacement_version,
    _to_document_read,
)
from app.modules.ingestion.url_service import enqueue_url_source
from app.schemas import CanonicalBlockPage, DocumentDeleteResult, DocumentRead, DocumentUploadResult, ReprocessResult, UrlSourceRequest

router = APIRouter(tags=["documents"])


def _read_upload(file: UploadFile) -> tuple[str, bytes]:
    filename = file.filename or ""
    limit = get_settings().max_upload_bytes
    chunks: list[bytes] = []
    total = 0
    while True:
        block = file.file.read(1024 * 1024)
        if not block:
            break
        total += len(block)
        if total > limit:
            raise APIError(413, "file_too_large", "The file exceeds the 25 MB upload limit.")
        chunks.append(block)
    return filename, b"".join(chunks)


@router.post("/knowledge-bases/{kb_id}/sources/url", response_model=DocumentUploadResult, status_code=201)
def enqueue_url_source_route(
    kb_id: UUID,
    body: UrlSourceRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DocumentUploadResult:
    return enqueue_url_source(
        db,
        kb_id=kb_id,
        user_id=user.id,
        url=body.url,
        title=body.title,
        max_pages=body.max_pages,
        depth=body.depth,
    )


@router.post("/knowledge-bases/{kb_id}/documents", response_model=DocumentUploadResult)
def upload_document_route(
    kb_id: UUID,
    response: Response,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    force: bool = Form(default=False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DocumentUploadResult:
    filename, data = _read_upload(file)
    result = upload_new_document(
        db,
        kb_id=kb_id,
        user_id=user.id,
        filename=filename,
        title=title,
        data=data,
        force=force,
    )
    response.status_code = 200 if result.reused else 201
    return result


@router.post("/documents/{document_id}/versions", response_model=DocumentUploadResult)
def upload_replacement_route(
    document_id: UUID,
    response: Response,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    force: bool = Form(default=False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DocumentUploadResult:
    filename, data = _read_upload(file)
    result = upload_replacement_version(
        db,
        document_id=document_id,
        user_id=user.id,
        filename=filename,
        title=title,
        data=data,
        force=force,
    )
    response.status_code = 200 if result.reused else 201
    return result


@router.post("/documents/{document_id}/reprocess", response_model=ReprocessResult, status_code=202)
def reprocess_document_route(
    document_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReprocessResult:
    run_id, trace_id, job_id = reprocess_document(db, document_id=document_id, user_id=user.id)
    return ReprocessResult(processing_run_id=run_id, trace_id=trace_id, job_id=job_id)


@router.get("/document-versions/{version_id}/blocks", response_model=CanonicalBlockPage)
def list_version_blocks_route(
    version_id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CanonicalBlockPage:
    items, next_cursor = list_version_blocks(
        db,
        version_id=version_id,
        user_id=user.id,
        limit=limit,
        cursor=cursor,
    )
    return CanonicalBlockPage(items=items, next_cursor=next_cursor)


@router.get("/documents/{document_id}", response_model=DocumentRead)
def get_document_route(
    document_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DocumentRead:
    return get_document(db, document_id, user.id)


@router.post("/documents/{document_id}/rollback", response_model=DocumentRead)
def rollback_document_route(
    document_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DocumentRead:
    document = rollback_document_serving(db, document_id=document_id, user_id=user.id)
    db.commit()
    return _to_document_read(db, document)


@router.delete("/documents/{document_id}", response_model=DocumentDeleteResult, status_code=202)
def delete_document_route(
    document_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DocumentDeleteResult:
    job = soft_delete_document(db, document_id=document_id, user_id=user.id)
    db.commit()
    return DocumentDeleteResult(job_id=job.id)
