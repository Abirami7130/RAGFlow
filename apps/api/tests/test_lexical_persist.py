"""Phase 3J lexical postings and chunk_stats persistence."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.configs import DEFAULT_CHUNK_CONFIG
from app.models import (
    CanonicalBlock,
    Chunk,
    ChunkStats,
    Document,
    DocumentVersion,
    KnowledgeBase,
    KnowledgeBaseMember,
    LexicalPosting,
    ProcessingRun,
    User,
)
from app.modules.lexical import persist_lexical_for_run


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
            token_count=1,
            block_start_id=block.id,
            block_end_id=block.id,
            locator_start=block.locator,
            locator_end=block.locator,
        )
        session.add(chunk)
        chunks.append(chunk)
    session.flush()
    return run, chunks


class TestLexicalPersist:
    def test_persists_stats_and_postings(self, db_session: Session, kb_context: dict) -> None:
        run, chunks = _run_with_chunks(
            db_session,
            kb_context["kb_id"],
            kb_context["user_id"],
            chunk_texts=["alpha beta", "beta beta"],
        )
        db_session.commit()

        result = persist_lexical_for_run(db_session, run.id)
        db_session.commit()

        assert result.replaced is True
        assert result.chunk_stats_count == 2
        assert result.posting_count == 3

        stats = db_session.get(ChunkStats, chunks[0].id)
        assert stats is not None
        assert stats.token_count == 2
        assert stats.processing_run_id == run.id

        rows = list(
            db_session.scalars(
                select(LexicalPosting).where(LexicalPosting.chunk_id == chunks[1].id).order_by(LexicalPosting.term)
            ).all()
        )
        assert len(rows) == 1
        assert rows[0].term == "beta"
        assert rows[0].term_frequency == 2

    def test_retry_is_idempotent(self, db_session: Session, kb_context: dict) -> None:
        run, _ = _run_with_chunks(
            db_session,
            kb_context["kb_id"],
            kb_context["user_id"],
            chunk_texts=["once twice twice"],
        )
        db_session.commit()

        first = persist_lexical_for_run(db_session, run.id)
        db_session.commit()
        total = db_session.scalar(select(func.count()).select_from(LexicalPosting))
        second = persist_lexical_for_run(db_session, run.id)
        db_session.commit()

        assert first.replaced is True
        assert second.replaced is False
        assert second.posting_count == first.posting_count
        assert db_session.scalar(select(func.count()).select_from(LexicalPosting)) == total

    def test_replace_after_text_change(self, db_session: Session, kb_context: dict) -> None:
        run, chunks = _run_with_chunks(
            db_session,
            kb_context["kb_id"],
            kb_context["user_id"],
            chunk_texts=["token token"],
        )
        db_session.commit()
        persist_lexical_for_run(db_session, run.id)
        db_session.commit()

        chunks[0].text = "token token token"
        db_session.commit()
        result = persist_lexical_for_run(db_session, run.id)
        db_session.commit()

        assert result.replaced is True
        row = db_session.scalars(
            select(LexicalPosting).where(
                LexicalPosting.chunk_id == chunks[0].id,
                LexicalPosting.term == "token",
            )
        ).one()
        assert row.term_frequency == 3
        stats = db_session.get(ChunkStats, chunks[0].id)
        assert stats is not None
        assert stats.token_count == 3

    def test_run_isolation(self, db_session: Session, kb_context: dict) -> None:
        run_a, chunks_a = _run_with_chunks(
            db_session,
            kb_context["kb_id"],
            kb_context["user_id"],
            chunk_texts=["only run a"],
        )
        run_b, chunks_b = _run_with_chunks(
            db_session,
            kb_context["kb_id"],
            kb_context["user_id"],
            chunk_texts=["only run b"],
        )
        db_session.commit()
        persist_lexical_for_run(db_session, run_a.id)
        persist_lexical_for_run(db_session, run_b.id)
        db_session.commit()

        rows_a = db_session.scalars(select(LexicalPosting).where(LexicalPosting.processing_run_id == run_a.id)).all()
        rows_b = db_session.scalars(select(LexicalPosting).where(LexicalPosting.processing_run_id == run_b.id)).all()
        assert all(row.chunk_id in {chunk.id for chunk in chunks_a} for row in rows_a)
        assert all(row.chunk_id in {chunk.id for chunk in chunks_b} for row in rows_b)
        assert {row.chunk_id for row in rows_a}.isdisjoint({row.chunk_id for row in rows_b})