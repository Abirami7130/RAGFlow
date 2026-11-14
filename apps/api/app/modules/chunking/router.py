from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import User
from app.modules.auth.router import get_current_user
from app.modules.chunking.runs import get_processing_run
from app.modules.chunking.service import list_run_chunks, read_chunk
from app.schemas import ChunkPage, ChunkRead, ProcessingRunRead

router = APIRouter(tags=["processing"])


@router.get("/processing-runs/{run_id}", response_model=ProcessingRunRead)
def get_processing_run_route(
    run_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProcessingRunRead:
    return get_processing_run(db, run_id=run_id, user_id=user.id)


@router.get("/processing-runs/{run_id}/chunks", response_model=ChunkPage)
def list_run_chunks_route(
    run_id: UUID,
    include_vector: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChunkPage:
    if include_vector:
        pass
    items, next_cursor = list_run_chunks(
        db,
        run_id=run_id,
        user_id=user.id,
        limit=limit,
        cursor=cursor,
    )
    return ChunkPage(items=items, next_cursor=next_cursor)


@router.get("/chunks/{chunk_id}", response_model=ChunkRead)
def get_chunk_route(
    chunk_id: UUID,
    include_vector: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChunkRead:
    if include_vector:
        pass
    return read_chunk(db, chunk_id=chunk_id, user_id=user.id)