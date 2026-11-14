"""Phase 3K lexical corpus statistics and document frequencies."""

from __future__ import annotations

import uuid

import pytest
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
)
from app.modules.lexical import (
    load_lexical_corpus_snapshot,
    persist_lexical_for_run,
    recompute_lexical_corpus_for_active_chunks,
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


class TestLexicalCorpusRecompute:
    def test_hand_calculated_small_corpus(self, db_session: Session, kb_context: dict) -> None:
        run, chunks = _run_with_chunks(
            db_session,
            kb_context["kb_id"],
            kb_context["user_id"],
            chunk_texts=["alpha beta", "beta beta"],
        )
        db_session.commit()
        persist_lexical_for_run(db_session, run.id)
        db_session.commit()

        snapshot = recompute_lexical_corpus_for_active_chunks(
            db_session,
            kb_context["kb_id"],
            [chunk.id for chunk in chunks],
        )
        db_session.commit()

        assert snapshot.chunk_count == 2
        assert snapshot.token_sum == 4
        assert snapshot.avg_token_length == 2.0
        assert dict(snapshot.term_document_frequencies) == {"alpha": 1, "beta": 2}

        loaded = load_lexical_corpus_snapshot(db_session, kb_context["kb_id"])
        assert loaded == snapshot

    def test_repeated_terms_do_not_inflate_df(self, db_session: Session, kb_context: dict) -> None:
        run, chunks = _run_with_chunks(
            db_session,
            kb_context["kb_id"],
            kb_context["user_id"],
            chunk_texts=["repeat repeat repeat"],
        )
        db_session.commit()
        persist_lexical_for_run(db_session, run.id)
        db_session.commit()

        snapshot = recompute_lexical_corpus_for_active_chunks(
            db_session,
            kb_context["kb_id"],
            [chunks[0].id],
        )
        db_session.commit()
        assert dict(snapshot.term_document_frequencies) == {"repeat": 1}

    def test_empty_active_set(self, db_session: Session, kb_context: dict) -> None:
        snapshot = recompute_lexical_corpus_for_active_chunks(db_session, kb_context["kb_id"], [])
        db_session.commit()
        assert snapshot.chunk_count == 0
        assert snapshot.token_sum == 0
        assert snapshot.avg_token_length == 0.0
        assert snapshot.term_document_frequencies == ()

    def test_replace_active_run_selection(self, db_session: Session, kb_context: dict) -> None:
        run_a, chunks_a = _run_with_chunks(
            db_session,
            kb_context["kb_id"],
            kb_context["user_id"],
            chunk_texts=["term one"],
        )
        run_b, chunks_b = _run_with_chunks(
            db_session,
            kb_context["kb_id"],
            kb_context["user_id"],
            chunk_texts=["term two"],
        )
        db_session.commit()
        persist_lexical_for_run(db_session, run_a.id)
        persist_lexical_for_run(db_session, run_b.id)
        db_session.commit()

        first = recompute_lexical_corpus_for_active_chunks(
            db_session,
            kb_context["kb_id"],
            [chunks_a[0].id],
        )
        db_session.commit()
        second = recompute_lexical_corpus_for_active_chunks(
            db_session,
            kb_context["kb_id"],
            [chunks_b[0].id],
        )
        db_session.commit()

        assert first.chunk_count == 1
        assert dict(first.term_document_frequencies) == {"one": 1, "term": 1}
        assert second.chunk_count == 1
        assert dict(second.term_document_frequencies) == {"term": 1, "two": 1}
        assert load_lexical_corpus_snapshot(db_session, kb_context["kb_id"]) == second

    def test_recompute_is_idempotent(self, db_session: Session, kb_context: dict) -> None:
        run, chunks = _run_with_chunks(
            db_session,
            kb_context["kb_id"],
            kb_context["user_id"],
            chunk_texts=["stable corpus"],
        )
        db_session.commit()
        persist_lexical_for_run(db_session, run.id)
        db_session.commit()
        active = [chunks[0].id]
        first = recompute_lexical_corpus_for_active_chunks(db_session, kb_context["kb_id"], active)
        db_session.commit()
        second = recompute_lexical_corpus_for_active_chunks(db_session, kb_context["kb_id"], active)
        db_session.commit()
        assert first == second