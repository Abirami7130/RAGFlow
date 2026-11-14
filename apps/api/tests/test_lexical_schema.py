"""PostgreSQL schema tests for Phase 3D lexical index tables."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
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
    LexicalCorpusStats,
    LexicalPosting,
    ProcessingRun,
    User,
)


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
    sequence: int = 0,
) -> Chunk:
    chunk = Chunk(
        id=uuid.uuid4(),
        processing_run_id=run.id,
        document_version_id=version.id,
        knowledge_base_id=document.knowledge_base_id,
        document_id=document.id,
        sequence=sequence,
        text="hello",
        token_count=2,
        block_start_id=block.id,
        block_end_id=block.id,
        locator_start=block.locator,
        locator_end=block.locator,
    )
    session.add(chunk)
    session.flush()
    return chunk


def _chunk_context(session: Session, kb_context: dict) -> dict:
    doc = _insert_document(session, kb_id=kb_context["kb_id"], user_id=kb_context["user_id"])
    version = _insert_version(session, document=doc)
    block = _insert_block(session, version=version)
    run = _insert_run(session, document=doc, version=version)
    chunk = _insert_chunk(session, run=run, version=version, document=doc, block=block)
    session.commit()
    return {"kb_id": kb_context["kb_id"], "run": run, "chunk": chunk}


class TestLexicalPostingsIndexes:
    def test_required_indexes_exist(self, db_session: Session) -> None:
        inspector = inspect(db_session.bind)
        names = {idx["name"] for idx in inspector.get_indexes("lexical_postings")}
        assert "ix_lexical_postings_chunk_id" in names
        assert "ix_lexical_postings_processing_run_id" in names


class TestChunkStatsKeys:
    def test_chunk_id_primary_key(self, db_session: Session, kb_context: dict) -> None:
        ctx = _chunk_context(db_session, kb_context)
        db_session.add(
            ChunkStats(
                chunk_id=ctx["chunk"].id,
                knowledge_base_id=ctx["kb_id"],
                token_count=2,
                processing_run_id=ctx["run"].id,
            )
        )
        db_session.commit()
        with pytest.raises(IntegrityError):
            db_session.add(
                ChunkStats(
                    chunk_id=ctx["chunk"].id,
                    knowledge_base_id=ctx["kb_id"],
                    token_count=3,
                    processing_run_id=ctx["run"].id,
                )
            )
            db_session.commit()
        db_session.rollback()

    def test_negative_token_count_rejected(self, db_session: Session, kb_context: dict) -> None:
        ctx = _chunk_context(db_session, kb_context)
        db_session.add(
            ChunkStats(
                chunk_id=ctx["chunk"].id,
                knowledge_base_id=ctx["kb_id"],
                token_count=-1,
                processing_run_id=ctx["run"].id,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()


class TestChunkStatsForeignKeys:
    def test_rejects_unknown_chunk_id(self, db_session: Session, kb_context: dict) -> None:
        ctx = _chunk_context(db_session, kb_context)
        db_session.add(
            ChunkStats(
                chunk_id=uuid.uuid4(),
                knowledge_base_id=ctx["kb_id"],
                token_count=1,
                processing_run_id=ctx["run"].id,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_cascade_delete_with_chunk(self, db_session: Session, kb_context: dict) -> None:
        ctx = _chunk_context(db_session, kb_context)
        db_session.add(
            ChunkStats(
                chunk_id=ctx["chunk"].id,
                knowledge_base_id=ctx["kb_id"],
                token_count=2,
                processing_run_id=ctx["run"].id,
            )
        )
        db_session.commit()
        chunk = db_session.get(Chunk, ctx["chunk"].id)
        assert chunk is not None
        db_session.delete(chunk)
        db_session.commit()
        assert db_session.get(ChunkStats, ctx["chunk"].id) is None


class TestLexicalPostingKeys:
    def test_composite_primary_key_unique(self, db_session: Session, kb_context: dict) -> None:
        ctx = _chunk_context(db_session, kb_context)
        db_session.add(
            LexicalPosting(
                knowledge_base_id=ctx["kb_id"],
                term="hello",
                chunk_id=ctx["chunk"].id,
                processing_run_id=ctx["run"].id,
                term_frequency=1,
            )
        )
        db_session.commit()
        db_session.add(
            LexicalPosting(
                knowledge_base_id=ctx["kb_id"],
                term="hello",
                chunk_id=ctx["chunk"].id,
                processing_run_id=ctx["run"].id,
                term_frequency=2,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_same_term_different_chunks_allowed(self, db_session: Session, kb_context: dict) -> None:
        doc = _insert_document(db_session, kb_id=kb_context["kb_id"], user_id=kb_context["user_id"])
        version = _insert_version(db_session, document=doc)
        block = _insert_block(db_session, version=version)
        run = _insert_run(db_session, document=doc, version=version)
        chunk_a = _insert_chunk(db_session, run=run, version=version, document=doc, block=block, sequence=0)
        chunk_b = _insert_chunk(db_session, run=run, version=version, document=doc, block=block, sequence=1)
        db_session.add_all(
            [
                LexicalPosting(
                    knowledge_base_id=kb_context["kb_id"],
                    term="shared",
                    chunk_id=chunk_a.id,
                    processing_run_id=run.id,
                    term_frequency=1,
                ),
                LexicalPosting(
                    knowledge_base_id=kb_context["kb_id"],
                    term="shared",
                    chunk_id=chunk_b.id,
                    processing_run_id=run.id,
                    term_frequency=1,
                ),
            ]
        )
        db_session.commit()

    def test_zero_term_frequency_rejected(self, db_session: Session, kb_context: dict) -> None:
        ctx = _chunk_context(db_session, kb_context)
        db_session.add(
            LexicalPosting(
                knowledge_base_id=ctx["kb_id"],
                term="zero",
                chunk_id=ctx["chunk"].id,
                processing_run_id=ctx["run"].id,
                term_frequency=0,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()


class TestLexicalPostingForeignKeys:
    def test_rejects_unknown_chunk_id(self, db_session: Session, kb_context: dict) -> None:
        ctx = _chunk_context(db_session, kb_context)
        db_session.add(
            LexicalPosting(
                knowledge_base_id=ctx["kb_id"],
                term="orphan",
                chunk_id=uuid.uuid4(),
                processing_run_id=ctx["run"].id,
                term_frequency=1,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_cascade_delete_with_chunk(self, db_session: Session, kb_context: dict) -> None:
        ctx = _chunk_context(db_session, kb_context)
        db_session.add(
            LexicalPosting(
                knowledge_base_id=ctx["kb_id"],
                term="temp",
                chunk_id=ctx["chunk"].id,
                processing_run_id=ctx["run"].id,
                term_frequency=1,
            )
        )
        db_session.commit()
        chunk = db_session.get(Chunk, ctx["chunk"].id)
        assert chunk is not None
        db_session.delete(chunk)
        db_session.commit()
        remaining = db_session.query(LexicalPosting).filter_by(chunk_id=ctx["chunk"].id).all()
        assert remaining == []


class TestLexicalCorpusStatsKeys:
    def test_one_row_per_knowledge_base(self, db_session: Session, kb_context: dict) -> None:
        db_session.add(
            LexicalCorpusStats(
                knowledge_base_id=kb_context["kb_id"],
                chunk_count=0,
                token_sum=0,
                avg_token_length=0.0,
            )
        )
        db_session.commit()
        db_session.add(
            LexicalCorpusStats(
                knowledge_base_id=kb_context["kb_id"],
                chunk_count=1,
                token_sum=2,
                avg_token_length=2.0,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_negative_totals_rejected(self, db_session: Session, kb_context: dict) -> None:
        db_session.add(
            LexicalCorpusStats(
                knowledge_base_id=kb_context["kb_id"],
                chunk_count=-1,
                token_sum=0,
                avg_token_length=0.0,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()


class TestLexicalCorpusStatsForeignKeys:
    def test_rejects_unknown_knowledge_base(self, db_session: Session) -> None:
        db_session.add(
            LexicalCorpusStats(
                knowledge_base_id=uuid.uuid4(),
                chunk_count=0,
                token_sum=0,
                avg_token_length=0.0,
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()