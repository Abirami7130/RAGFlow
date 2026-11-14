"""Phase 3E vector index creation and staged id allocation."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
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
from app.modules.vectorstore import get_or_create_vector_index, stage_vector_mappings_for_run


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


def _run_with_chunks(
    session: Session,
    kb_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    chunk_texts: list[str],
) -> tuple[ProcessingRun, list[Chunk]]:
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
    version = DocumentVersion(
        id=uuid.uuid4(),
        document_id=doc.id,
        knowledge_base_id=kb_id,
        version_number=1,
        content_hash=uuid.uuid4().hex,
        dedup_hash=uuid.uuid4().hex,
        storage_path=f"incoming/{uuid.uuid4()}",
        storage_state="stored",
        byte_size=12,
        mime_type="application/pdf",
    )
    session.add(version)
    session.flush()
    block = CanonicalBlock(
        id=uuid.uuid4(),
        document_version_id=version.id,
        sequence=0,
        block_type="paragraph",
        text="block",
        locator={"source_type": "txt", "char_start": 0, "char_end": 5},
    )
    session.add(block)
    session.flush()
    run = ProcessingRun(
        id=uuid.uuid4(),
        document_id=doc.id,
        document_version_id=version.id,
        knowledge_base_id=kb_id,
        status="extracted",
        chunk_strategy=DEFAULT_CHUNK_CONFIG,
    )
    session.add(run)
    session.flush()
    chunks: list[Chunk] = []
    for sequence, text in enumerate(chunk_texts):
        chunk = Chunk(
            id=uuid.uuid4(),
            processing_run_id=run.id,
            document_version_id=version.id,
            knowledge_base_id=kb_id,
            document_id=doc.id,
            sequence=sequence,
            text=text,
            token_count=len(text.split()),
            block_start_id=block.id,
            block_end_id=block.id,
            locator_start=block.locator,
            locator_end=block.locator,
        )
        session.add(chunk)
        chunks.append(chunk)
    session.flush()
    return run, chunks


class TestVectorIndexRegistry:
    def test_get_or_create_is_stable(self, db_session: Session, kb_context: dict) -> None:
        first = get_or_create_vector_index(
            db_session,
            knowledge_base_id=kb_context["kb_id"],
            embedding_model=FAKE_EMBEDDING_MODEL_ID,
            embedding_revision=FAKE_EMBEDDING_REVISION,
            dimension=FAKE_EMBEDDING_DIMENSION,
        )
        db_session.commit()
        second = get_or_create_vector_index(
            db_session,
            knowledge_base_id=kb_context["kb_id"],
            embedding_model=FAKE_EMBEDDING_MODEL_ID,
            embedding_revision=FAKE_EMBEDDING_REVISION,
            dimension=FAKE_EMBEDDING_DIMENSION,
        )
        assert first.id == second.id
        assert second.faiss_path == f"{kb_context['kb_id']}/{first.id}/index.faiss"

    def test_different_dimensions_are_isolated(self, db_session: Session, kb_context: dict) -> None:
        dim_a = get_or_create_vector_index(
            db_session,
            knowledge_base_id=kb_context["kb_id"],
            embedding_model=FAKE_EMBEDDING_MODEL_ID,
            embedding_revision=FAKE_EMBEDDING_REVISION,
            dimension=128,
        )
        dim_b = get_or_create_vector_index(
            db_session,
            knowledge_base_id=kb_context["kb_id"],
            embedding_model=FAKE_EMBEDDING_MODEL_ID,
            embedding_revision=FAKE_EMBEDDING_REVISION,
            dimension=256,
        )
        db_session.commit()
        assert dim_a.id != dim_b.id


class TestStagedVectorAllocation:
    def test_allocation_order_and_state(self, db_session: Session, kb_context: dict) -> None:
        run, chunks = _run_with_chunks(
            db_session,
            kb_context["kb_id"],
            kb_context["user_id"],
            chunk_texts=["alpha", "beta", "gamma"],
        )
        db_session.commit()

        result = stage_vector_mappings_for_run(
            db_session,
            run_id=run.id,
            embedding_model=FAKE_EMBEDDING_MODEL_ID,
            embedding_revision=FAKE_EMBEDDING_REVISION,
            dimension=FAKE_EMBEDDING_DIMENSION,
        )
        db_session.commit()

        assert [vector_id for _, vector_id in result.pairs] == [1, 2, 3]
        assert [chunk_id for chunk_id, _ in result.pairs] == [chunk.id for chunk in chunks]

        index = db_session.get(VectorIndex, result.index_id)
        assert index is not None
        assert index.next_vector_id == 4

        rows = db_session.scalars(
            select(VectorMapping).where(VectorMapping.index_id == result.index_id).order_by(VectorMapping.vector_id)
        ).all()
        assert all(row.state == "staged" for row in rows)
        assert [row.vector_id for row in rows] == [1, 2, 3]

    def test_retry_is_idempotent(self, db_session: Session, kb_context: dict) -> None:
        run, _chunks = _run_with_chunks(
            db_session,
            kb_context["kb_id"],
            kb_context["user_id"],
            chunk_texts=["one", "two"],
        )
        db_session.commit()

        first = stage_vector_mappings_for_run(
            db_session,
            run_id=run.id,
            embedding_model=FAKE_EMBEDDING_MODEL_ID,
            embedding_revision=FAKE_EMBEDDING_REVISION,
            dimension=FAKE_EMBEDDING_DIMENSION,
        )
        db_session.commit()
        index = db_session.get(VectorIndex, first.index_id)
        assert index is not None
        after_first = index.next_vector_id

        second = stage_vector_mappings_for_run(
            db_session,
            run_id=run.id,
            embedding_model=FAKE_EMBEDDING_MODEL_ID,
            embedding_revision=FAKE_EMBEDDING_REVISION,
            dimension=FAKE_EMBEDDING_DIMENSION,
        )
        db_session.commit()
        db_session.refresh(index)

        assert second.pairs == first.pairs
        assert index.next_vector_id == after_first
        count = db_session.scalar(
            select(func.count())
            .select_from(VectorMapping)
            .where(VectorMapping.index_id == first.index_id)
        )
        assert count == 2

    def test_partial_existing_mappings_resume_allocation(self, db_session: Session, kb_context: dict) -> None:
        run, chunks = _run_with_chunks(
            db_session,
            kb_context["kb_id"],
            kb_context["user_id"],
            chunk_texts=["a", "b", "c"],
        )
        db_session.commit()

        index = get_or_create_vector_index(
            db_session,
            knowledge_base_id=kb_context["kb_id"],
            embedding_model=FAKE_EMBEDDING_MODEL_ID,
            embedding_revision=FAKE_EMBEDDING_REVISION,
            dimension=FAKE_EMBEDDING_DIMENSION,
        )
        db_session.add(
            VectorMapping(
                index_id=index.id,
                vector_id=10,
                chunk_id=chunks[0].id,
                state="staged",
            )
        )
        index.next_vector_id = 11
        db_session.commit()

        result = stage_vector_mappings_for_run(
            db_session,
            run_id=run.id,
            embedding_model=FAKE_EMBEDDING_MODEL_ID,
            embedding_revision=FAKE_EMBEDDING_REVISION,
            dimension=FAKE_EMBEDDING_DIMENSION,
        )
        db_session.commit()
        db_session.refresh(index)

        assert [vector_id for _, vector_id in result.pairs] == [10, 11, 12]
        assert index.next_vector_id == 13

    def test_indexes_isolate_counters_across_knowledge_bases(self, db_session: Session) -> None:
        kb_a = uuid.uuid4()
        kb_b = uuid.uuid4()
        user_id = uuid.uuid4()
        db_session.add(
            User(
                id=user_id,
                email=f"{uuid.uuid4().hex[:12]}@example.com",
                password_hash=_password_hash(),
                display_name="Owner",
            )
        )
        db_session.flush()
        for kb_id in (kb_a, kb_b):
            db_session.add(
                KnowledgeBase(
                    id=kb_id,
                    owner_id=user_id,
                    name=f"KB-{kb_id.hex[:6]}",
                    description="",
                    retrieval_config={},
                    chunk_config=DEFAULT_CHUNK_CONFIG,
                )
            )
            db_session.add(KnowledgeBaseMember(knowledge_base_id=kb_id, user_id=user_id, role="owner"))
        db_session.commit()

        run_a, _ = _run_with_chunks(db_session, kb_a, user_id, chunk_texts=["only"])
        run_b, _ = _run_with_chunks(db_session, kb_b, user_id, chunk_texts=["solo"])
        db_session.commit()

        result_a = stage_vector_mappings_for_run(
            db_session,
            run_id=run_a.id,
            embedding_model=FAKE_EMBEDDING_MODEL_ID,
            embedding_revision=FAKE_EMBEDDING_REVISION,
            dimension=FAKE_EMBEDDING_DIMENSION,
        )
        result_b = stage_vector_mappings_for_run(
            db_session,
            run_id=run_b.id,
            embedding_model=FAKE_EMBEDDING_MODEL_ID,
            embedding_revision=FAKE_EMBEDDING_REVISION,
            dimension=FAKE_EMBEDDING_DIMENSION,
        )
        db_session.commit()

        assert result_a.index_id != result_b.index_id
        assert result_a.pairs[0][1] == 1
        assert result_b.pairs[0][1] == 1