"""PostgreSQL schema tests for Phase 3C vector index tables."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.configs import DEFAULT_CHUNK_CONFIG
from app.models import (
    CanonicalBlock,
    Chunk,
    Document,
    DocumentVersion,
    KnowledgeBase,
    KnowledgeBaseMember,
    ProcessingRun,
    User,
    VectorIndex,
    VectorMapping,
)
from app.modules.embeddings import FAKE_EMBEDDING_DIMENSION, FAKE_EMBEDDING_MODEL_ID, FAKE_EMBEDDING_REVISION


def _password_hash() -> str:
    from argon2 import PasswordHasher

    return PasswordHasher().hash("password-long-enough")


@pytest.fixture
def db_session() -> Session:
    from app.core.db import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def kb_context(db_session: Session) -> dict:
    user_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    db_session.add(
        User(
            id=user_id,
            email=f"{uuid.uuid4().hex[:12]}@example.com",
            password_hash=_password_hash(),
            display_name="Tester",
        )
    )
    db_session.flush()
    db_session.add(
        KnowledgeBase(
            id=kb_id,
            owner_id=user_id,
            name="KB",
            description="",
            retrieval_config={},
            chunk_config=DEFAULT_CHUNK_CONFIG,
        )
    )
    db_session.add(KnowledgeBaseMember(knowledge_base_id=kb_id, user_id=user_id, role="owner"))
    db_session.commit()
    return {"user_id": user_id, "kb_id": kb_id}


def _insert_document(session: Session, *, kb_id: uuid.UUID, user_id: uuid.UUID) -> Document:
    doc = Document(
        id=uuid.uuid4(),
        knowledge_base_id=kb_id,
        title="sample.pdf",
        source_type="pdf",
        status="queued",
        created_by=user_id,
    )
    session.add(doc)
    session.flush()
    return doc


def _insert_version(session: Session, *, document: Document) -> DocumentVersion:
    version = DocumentVersion(
        id=uuid.uuid4(),
        document_id=document.id,
        knowledge_base_id=document.knowledge_base_id,
        version_number=1,
        content_hash="abc123",
        dedup_hash="abc123",
        storage_path=f"incoming/{uuid.uuid4()}",
        storage_state="stored",
        byte_size=12,
        mime_type="application/pdf",
    )
    session.add(version)
    session.flush()
    return version


def _insert_run(session: Session, *, document: Document, version: DocumentVersion) -> ProcessingRun:
    run = ProcessingRun(
        id=uuid.uuid4(),
        document_id=document.id,
        document_version_id=version.id,
        knowledge_base_id=document.knowledge_base_id,
        status="extracted",
        chunk_strategy=DEFAULT_CHUNK_CONFIG,
    )
    session.add(run)
    session.flush()
    return run


def _insert_block(session: Session, *, version: DocumentVersion) -> CanonicalBlock:
    block = CanonicalBlock(
        id=uuid.uuid4(),
        document_version_id=version.id,
        sequence=0,
        block_type="paragraph",
        text="hello",
        locator={"source_type": "txt", "char_start": 0, "char_end": 5},
    )
    session.add(block)
    session.flush()
    return block


def _insert_chunk(
    session: Session,
    *,
    run: ProcessingRun,
    version: DocumentVersion,
    document: Document,
    block: CanonicalBlock,
) -> Chunk:
    chunk = Chunk(
        id=uuid.uuid4(),
        processing_run_id=run.id,
        document_version_id=version.id,
        knowledge_base_id=document.knowledge_base_id,
        document_id=document.id,
        sequence=0,
        text="hello",
        token_count=1,
        block_start_id=block.id,
        block_end_id=block.id,
        locator_start=block.locator,
        locator_end=block.locator,
    )
    session.add(chunk)
    session.flush()
    return chunk


def _insert_index(session: Session, *, kb_id: uuid.UUID, **overrides) -> VectorIndex:
    values = {
        "id": uuid.uuid4(),
        "knowledge_base_id": kb_id,
        "embedding_model": FAKE_EMBEDDING_MODEL_ID,
        "embedding_revision": FAKE_EMBEDDING_REVISION,
        "dimension": FAKE_EMBEDDING_DIMENSION,
        "metric": "ip",
        "faiss_path": f"indexes/{kb_id}/faiss.bin",
    }
    values.update(overrides)
    index = VectorIndex(**values)
    session.add(index)
    return index


def _chunk_context(session: Session, kb_context: dict) -> dict:
    doc = _insert_document(session, kb_id=kb_context["kb_id"], user_id=kb_context["user_id"])
    version = _insert_version(session, document=doc)
    block = _insert_block(session, version=version)
    run = _insert_run(session, document=doc, version=version)
    chunk = _insert_chunk(session, run=run, version=version, document=doc, block=block)
    session.commit()
    return {"index_kb_id": kb_context["kb_id"], "chunk": chunk}


class TestVectorIndexConstraints:
    def test_kb_identity_unique(self, db_session: Session, kb_context: dict) -> None:
        _insert_index(db_session, kb_id=kb_context["kb_id"])
        db_session.commit()
        _insert_index(db_session, kb_id=kb_context["kb_id"])
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_same_identity_different_dimension_allowed(self, db_session: Session, kb_context: dict) -> None:
        _insert_index(db_session, kb_id=kb_context["kb_id"], dimension=128)
        db_session.commit()
        _insert_index(db_session, kb_id=kb_context["kb_id"], dimension=256)
        db_session.commit()

    def test_invalid_metric_rejected(self, db_session: Session, kb_context: dict) -> None:
        _insert_index(db_session, kb_id=kb_context["kb_id"], metric="l2")
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_non_positive_dimension_rejected(self, db_session: Session, kb_context: dict) -> None:
        _insert_index(db_session, kb_id=kb_context["kb_id"], dimension=0)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_defaults_applied(self, db_session: Session, kb_context: dict) -> None:
        index = _insert_index(db_session, kb_id=kb_context["kb_id"])
        db_session.commit()
        db_session.refresh(index)
        assert index.generation == 0
        assert index.vector_count == 0
        assert index.next_vector_id == 1


class TestVectorMappingKeys:
    def test_composite_primary_key_allows_distinct_vector_ids(self, db_session: Session, kb_context: dict) -> None:
        ctx = _chunk_context(db_session, kb_context)
        index = _insert_index(db_session, kb_id=ctx["index_kb_id"])
        db_session.add(
            VectorMapping(
                index_id=index.id,
                vector_id=1,
                chunk_id=ctx["chunk"].id,
                state="staged",
            )
        )
        other_chunk_id = uuid.uuid4()
        db_session.add(
            Chunk(
                id=other_chunk_id,
                processing_run_id=ctx["chunk"].processing_run_id,
                document_version_id=ctx["chunk"].document_version_id,
                knowledge_base_id=ctx["chunk"].knowledge_base_id,
                document_id=ctx["chunk"].document_id,
                sequence=1,
                text="second",
                token_count=1,
                block_start_id=ctx["chunk"].block_start_id,
                block_end_id=ctx["chunk"].block_end_id,
                locator_start=ctx["chunk"].locator_start,
                locator_end=ctx["chunk"].locator_end,
            )
        )
        db_session.add(
            VectorMapping(
                index_id=index.id,
                vector_id=2,
                chunk_id=other_chunk_id,
                state="staged",
            )
        )
        db_session.commit()

    def test_duplicate_vector_id_rejected(self, db_session: Session, kb_context: dict) -> None:
        ctx = _chunk_context(db_session, kb_context)
        index = _insert_index(db_session, kb_id=ctx["index_kb_id"])
        db_session.add(
            VectorMapping(
                index_id=index.id,
                vector_id=7,
                chunk_id=ctx["chunk"].id,
                state="staged",
            )
        )
        db_session.commit()
        other_chunk_id = uuid.uuid4()
        db_session.add(
            Chunk(
                id=other_chunk_id,
                processing_run_id=ctx["chunk"].processing_run_id,
                document_version_id=ctx["chunk"].document_version_id,
                knowledge_base_id=ctx["chunk"].knowledge_base_id,
                document_id=ctx["chunk"].document_id,
                sequence=2,
                text="another",
                token_count=1,
                block_start_id=ctx["chunk"].block_start_id,
                block_end_id=ctx["chunk"].block_end_id,
                locator_start=ctx["chunk"].locator_start,
                locator_end=ctx["chunk"].locator_end,
            )
        )
        db_session.add(
            VectorMapping(
                index_id=index.id,
                vector_id=7,
                chunk_id=other_chunk_id,
                state="staged",
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_index_chunk_unique(self, db_session: Session, kb_context: dict) -> None:
        ctx = _chunk_context(db_session, kb_context)
        index = _insert_index(db_session, kb_id=ctx["index_kb_id"])
        db_session.add(
            VectorMapping(
                index_id=index.id,
                vector_id=1,
                chunk_id=ctx["chunk"].id,
                state="staged",
            )
        )
        db_session.commit()
        db_session.add(
            VectorMapping(
                index_id=index.id,
                vector_id=2,
                chunk_id=ctx["chunk"].id,
                state="committed",
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()


class TestVectorMappingForeignKeys:
    def test_rejects_unknown_index_id(self, db_session: Session, kb_context: dict) -> None:
        ctx = _chunk_context(db_session, kb_context)
        db_session.add(
            VectorMapping(
                index_id=uuid.uuid4(),
                vector_id=1,
                chunk_id=ctx["chunk"].id,
                state="staged",
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_rejects_unknown_chunk_id(self, db_session: Session, kb_context: dict) -> None:
        index = _insert_index(db_session, kb_id=kb_context["kb_id"])
        db_session.add(
            VectorMapping(
                index_id=index.id,
                vector_id=1,
                chunk_id=uuid.uuid4(),
                state="staged",
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_cascade_delete_mappings_with_index(self, db_session: Session, kb_context: dict) -> None:
        ctx = _chunk_context(db_session, kb_context)
        index = _insert_index(db_session, kb_id=ctx["index_kb_id"])
        db_session.add(
            VectorMapping(
                index_id=index.id,
                vector_id=1,
                chunk_id=ctx["chunk"].id,
                state="committed",
            )
        )
        db_session.commit()
        db_session.delete(index)
        db_session.commit()
        assert db_session.get(VectorMapping, {"index_id": index.id, "vector_id": 1}) is None


class TestVectorMappingStates:
    @pytest.mark.parametrize("state", ["staged", "committed", "retired"])
    def test_allowed_states(self, db_session: Session, kb_context: dict, state: str) -> None:
        ctx = _chunk_context(db_session, kb_context)
        index = _insert_index(db_session, kb_id=ctx["index_kb_id"])
        db_session.add(
            VectorMapping(
                index_id=index.id,
                vector_id=99,
                chunk_id=ctx["chunk"].id,
                state=state,
                l2_norm=1.0 if state == "committed" else None,
            )
        )
        db_session.commit()

    @pytest.mark.parametrize("state", ["pending", "active", ""])
    def test_invalid_state_rejected(self, db_session: Session, kb_context: dict, state: str) -> None:
        ctx = _chunk_context(db_session, kb_context)
        index = _insert_index(db_session, kb_id=ctx["index_kb_id"])
        db_session.add(
            VectorMapping(
                index_id=index.id,
                vector_id=1,
                chunk_id=ctx["chunk"].id,
                state=state,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()