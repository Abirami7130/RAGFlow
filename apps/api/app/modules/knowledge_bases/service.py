import base64
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import APIError
from app.domain.configs import DEFAULT_CHUNK_CONFIG, DEFAULT_RETRIEVAL_CONFIG
from app.models import KnowledgeBase, KnowledgeBaseMember, User
from app.schemas import (
    ChunkConfig,
    KnowledgeBaseCreate,
    KnowledgeBaseRead,
    KnowledgeBaseUpdate,
    MemberRead,
    RetrievalConfig,
)

_ROLE_RANK = {"viewer": 1, "editor": 2, "owner": 3}


def _read(kb: KnowledgeBase, role: str) -> KnowledgeBaseRead:
    return KnowledgeBaseRead(
        id=kb.id,
        name=kb.name,
        description=kb.description,
        role=role,
        document_count=0,
        ready_count=0,
        retrieval_config=RetrievalConfig.model_validate(kb.retrieval_config),
        chunk_config=ChunkConfig.model_validate(kb.chunk_config),
        created_at=kb.created_at,
        updated_at=kb.updated_at,
    )


def _membership(db: Session, kb_id: UUID, user_id: UUID) -> tuple[KnowledgeBase, KnowledgeBaseMember]:
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None or kb.deleted_at is not None:
        raise APIError(404, "not_found", "Knowledge base not found.")
    member = db.get(KnowledgeBaseMember, (kb_id, user_id))
    if member is None:
        raise APIError(404, "not_found", "Knowledge base not found.")
    return kb, member


def _require_role(member: KnowledgeBaseMember, minimum: str) -> None:
    if _ROLE_RANK[member.role] < _ROLE_RANK[minimum]:
        raise APIError(403, "forbidden", "You do not have access to this action.")


def list_knowledge_bases(db: Session, user_id: UUID, limit: int, cursor: str | None) -> tuple[list[KnowledgeBaseRead], str | None]:
    statement = (
        select(KnowledgeBase, KnowledgeBaseMember.role)
        .join(
            KnowledgeBaseMember,
            (KnowledgeBaseMember.knowledge_base_id == KnowledgeBase.id)
            & (KnowledgeBaseMember.user_id == user_id),
        )
        .where(KnowledgeBase.deleted_at.is_(None))
        .order_by(KnowledgeBase.created_at.desc(), KnowledgeBase.id.desc())
        .limit(limit + 1)
    )
    if cursor:
        created_at, kb_id = _decode_cursor(cursor)
        statement = statement.where(
            (KnowledgeBase.created_at < created_at)
            | ((KnowledgeBase.created_at == created_at) & (KnowledgeBase.id < kb_id))
        )
    rows = db.execute(statement).all()
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit and page:
        last = page[-1][0]
        next_cursor = _encode_cursor(last.created_at, last.id)
    return [_read(kb, role) for kb, role in page], next_cursor


def create_knowledge_base(db: Session, user: User, payload: KnowledgeBaseCreate) -> KnowledgeBaseRead:
    kb = KnowledgeBase(
        owner_id=user.id,
        name=payload.name.strip(),
        description=payload.description.strip(),
        retrieval_config=DEFAULT_RETRIEVAL_CONFIG,
        chunk_config=DEFAULT_CHUNK_CONFIG,
    )
    db.add(kb)
    db.flush()
    db.add(KnowledgeBaseMember(knowledge_base_id=kb.id, user_id=user.id, role="owner"))
    db.commit()
    db.refresh(kb)
    return _read(kb, "owner")


def get_knowledge_base(db: Session, kb_id: UUID, user_id: UUID) -> KnowledgeBaseRead:
    kb, member = _membership(db, kb_id, user_id)
    return _read(kb, member.role)


def update_knowledge_base(db: Session, kb_id: UUID, user_id: UUID, payload: KnowledgeBaseUpdate) -> KnowledgeBaseRead:
    kb, member = _membership(db, kb_id, user_id)
    _require_role(member, "editor")
    if payload.name is not None:
        kb.name = payload.name.strip()
    if payload.description is not None:
        kb.description = payload.description.strip()
    if payload.retrieval_config is not None:
        kb.retrieval_config = payload.retrieval_config.model_dump()
    if payload.chunk_config is not None:
        kb.chunk_config = payload.chunk_config.model_dump()
    kb.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(kb)
    return _read(kb, member.role)


def delete_knowledge_base(db: Session, kb_id: UUID, user_id: UUID) -> None:
    kb, member = _membership(db, kb_id, user_id)
    _require_role(member, "owner")
    kb.deleted_at = datetime.now(UTC)
    kb.updated_at = kb.deleted_at
    db.commit()


def list_members(db: Session, kb_id: UUID, user_id: UUID) -> list[MemberRead]:
    _membership(db, kb_id, user_id)
    rows = db.execute(
        select(KnowledgeBaseMember, User)
        .join(User, User.id == KnowledgeBaseMember.user_id)
        .where(KnowledgeBaseMember.knowledge_base_id == kb_id)
        .order_by(KnowledgeBaseMember.created_at.asc())
    ).all()
    return [
        MemberRead(user_id=user.id, email=user.email, display_name=user.display_name, role=member.role)
        for member, user in rows
    ]


def upsert_member(db: Session, kb_id: UUID, actor_id: UUID, email: str, role: str) -> MemberRead:
    _kb, actor = _membership(db, kb_id, actor_id)
    _require_role(actor, "owner")
    target = db.scalar(select(User).where(User.email == email.lower()))
    if target is None:
        raise APIError(404, "not_found", "No account exists for that email.")
    member = db.get(KnowledgeBaseMember, (kb_id, target.id))
    if member is not None and member.role == "owner" and role != "owner":
        _ensure_remaining_owner(db, kb_id, target.id)
    if member is None:
        member = KnowledgeBaseMember(knowledge_base_id=kb_id, user_id=target.id, role=role)
        db.add(member)
    else:
        member.role = role
    db.commit()
    db.refresh(target)
    return MemberRead(user_id=target.id, email=target.email, display_name=target.display_name, role=role)


def remove_member(db: Session, kb_id: UUID, actor_id: UUID, target_id: UUID) -> None:
    _kb, actor = _membership(db, kb_id, actor_id)
    _require_role(actor, "owner")
    member = db.get(KnowledgeBaseMember, (kb_id, target_id))
    if member is None:
        raise APIError(404, "not_found", "Member not found.")
    if member.role == "owner":
        _ensure_remaining_owner(db, kb_id, target_id)
    db.delete(member)
    db.commit()


def _ensure_remaining_owner(db: Session, kb_id: UUID, excluding: UUID) -> None:
    owners = db.scalars(
        select(KnowledgeBaseMember.user_id).where(
            KnowledgeBaseMember.knowledge_base_id == kb_id,
            KnowledgeBaseMember.role == "owner",
            KnowledgeBaseMember.user_id != excluding,
        )
    ).all()
    if not owners:
        raise APIError(409, "conflict", "A knowledge base must keep an owner.")


def _encode_cursor(created_at: datetime, kb_id: UUID) -> str:
    raw = f"{created_at.isoformat()}|{kb_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        stamp, kb_id = raw.split("|", 1)
        created_at = datetime.fromisoformat(stamp)
        return created_at, UUID(kb_id)
    except (ValueError, UnicodeDecodeError) as exc:
        raise APIError(400, "validation_error", "Cursor is invalid.") from exc
