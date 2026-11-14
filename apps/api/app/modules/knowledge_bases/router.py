from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import User
from app.modules.auth.router import get_current_user
from app.modules.knowledge_bases.service import (
    create_knowledge_base,
    delete_knowledge_base,
    get_knowledge_base,
    list_knowledge_bases,
    list_members,
    remove_member,
    update_knowledge_base,
    upsert_member,
)
from app.schemas import (
    KnowledgeBaseCreate,
    KnowledgeBaseDeleted,
    KnowledgeBasePage,
    KnowledgeBaseRead,
    KnowledgeBaseUpdate,
    MemberList,
    MemberRead,
    MemberWrite,
)

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])


@router.get("", response_model=KnowledgeBasePage)
def list_route(
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> KnowledgeBasePage:
    items, next_cursor = list_knowledge_bases(db, user.id, limit, cursor)
    return KnowledgeBasePage(items=items, next_cursor=next_cursor)


@router.post("", response_model=KnowledgeBaseRead, status_code=201)
def create_route(
    payload: KnowledgeBaseCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> KnowledgeBaseRead:
    return create_knowledge_base(db, user, payload)


@router.get("/{kb_id}", response_model=KnowledgeBaseRead)
def get_route(
    kb_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> KnowledgeBaseRead:
    return get_knowledge_base(db, kb_id, user.id)


@router.patch("/{kb_id}", response_model=KnowledgeBaseRead)
def patch_route(
    kb_id: UUID,
    payload: KnowledgeBaseUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> KnowledgeBaseRead:
    return update_knowledge_base(db, kb_id, user.id, payload)


@router.delete("/{kb_id}", response_model=KnowledgeBaseDeleted)
def delete_route(
    kb_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> KnowledgeBaseDeleted:
    delete_knowledge_base(db, kb_id, user.id)
    return KnowledgeBaseDeleted(id=kb_id)


@router.get("/{kb_id}/members", response_model=MemberList)
def list_members_route(
    kb_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MemberList:
    return MemberList(items=list_members(db, kb_id, user.id))


@router.put("/{kb_id}/members", response_model=MemberRead)
def put_member_route(
    kb_id: UUID,
    payload: MemberWrite,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MemberRead:
    return upsert_member(db, kb_id, user.id, payload.email, payload.role)


@router.delete("/{kb_id}/members/{user_id}", status_code=204)
def delete_member_route(
    kb_id: UUID,
    user_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    remove_member(db, kb_id, user.id, user_id)
    return Response(status_code=204)
