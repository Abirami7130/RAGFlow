"""Phase 3L Okapi BM25 scoring."""

from __future__ import annotations

import math
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
    bm25_score_chunk_from_db,
    persist_lexical_for_run,
    recompute_lexical_corpus_for_active_chunks,
)
from app.modules.lexical.bm25 import (
    BM25_B,
    BM25_K1,
    Bm25ChunkParams,
    Bm25CorpusParams,
    bm25_idf,
    bm25_score,
    bm25_term_contribution,
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


class TestBm25Primitives:
    def test_idf_hand_calculated(self) -> None:
        assert bm25_idf(chunk_count=2, document_frequency=1) == pytest.approx(math.log(2.0))
        assert bm25_idf(chunk_count=10, document_frequency=2) == pytest.approx(
            math.log(1.0 + (10 - 2 + 0.5) / (2 + 0.5))
        )
        assert bm25_idf(chunk_count=0, document_frequency=1) == 0.0
        assert bm25_idf(chunk_count=5, document_frequency=0) == 0.0

    def test_term_contribution_uses_length_and_avgdl(self) -> None:
        idf = math.log(2.0)
        value = bm25_term_contribution(
            term_frequency=2,
            document_length=3,
            avg_token_length=2.5,
            idf=idf,
            k1=BM25_K1,
            b=BM25_B,
        )
        norm = 1.0 - BM25_B + BM25_B * (3.0 / 2.5)
        expected = idf * (2 * (BM25_K1 + 1.0)) / (2 + BM25_K1 * norm)
        assert value == pytest.approx(expected)

    def test_missing_term_and_zero_df_add_nothing(self) -> None:
        corpus = Bm25CorpusParams(chunk_count=2, avg_token_length=2.0, document_frequencies={"known": 1})
        chunk = Bm25ChunkParams(document_length=2, term_frequencies={"known": 1})
        assert bm25_score("missing", corpus=corpus, chunk=chunk) == 0.0
        assert bm25_score("known unseen", corpus=corpus, chunk=chunk) == pytest.approx(
            bm25_score("known", corpus=corpus, chunk=chunk)
        )

    def test_repeated_query_terms(self) -> None:
        corpus = Bm25CorpusParams(chunk_count=2, avg_token_length=2.0, document_frequencies={"cat": 1})
        chunk = Bm25ChunkParams(document_length=2, term_frequencies={"cat": 1})
        once = bm25_score("cat", corpus=corpus, chunk=chunk)
        twice = bm25_score("cat cat", corpus=corpus, chunk=chunk)
        assert twice == pytest.approx(2.0 * once)

    def test_corpus_size_changes_idf(self) -> None:
        chunk = Bm25ChunkParams(document_length=2, term_frequencies={"term": 1})
        small = Bm25CorpusParams(chunk_count=2, avg_token_length=2.0, document_frequencies={"term": 1})
        large = Bm25CorpusParams(chunk_count=4, avg_token_length=2.0, document_frequencies={"term": 1})
        assert bm25_score("term", corpus=small, chunk=chunk) < bm25_score("term", corpus=large, chunk=chunk)

    def test_hand_calculated_two_document_corpus(self) -> None:
        corpus = Bm25CorpusParams(
            chunk_count=2,
            avg_token_length=2.0,
            document_frequencies={"alpha": 1, "beta": 2},
        )
        doc_a = Bm25ChunkParams(document_length=2, term_frequencies={"alpha": 1, "beta": 1})
        doc_b = Bm25ChunkParams(document_length=2, term_frequencies={"beta": 2})

        idf_alpha = bm25_idf(chunk_count=2, document_frequency=1)
        idf_beta = bm25_idf(chunk_count=2, document_frequency=2)
        tf_alpha = bm25_term_contribution(
            term_frequency=1,
            document_length=2,
            avg_token_length=2.0,
            idf=idf_alpha,
        )
        tf_beta = bm25_term_contribution(
            term_frequency=1,
            document_length=2,
            avg_token_length=2.0,
            idf=idf_beta,
        )
        expected_a = tf_alpha + tf_beta
        expected_b = bm25_term_contribution(
            term_frequency=2,
            document_length=2,
            avg_token_length=2.0,
            idf=idf_beta,
        )
        assert bm25_score("alpha beta", corpus=corpus, chunk=doc_a) == pytest.approx(expected_a)
        assert bm25_score("alpha beta", corpus=corpus, chunk=doc_b) == pytest.approx(expected_b)
        assert expected_a != expected_b


class TestBm25FromDatabase:
    def test_uses_persisted_postings_and_corpus(self, db_session: Session, kb_context: dict) -> None:
        user_id = kb_context["user_id"]
        kb_id = kb_context["kb_id"]
        doc = Document(
            id=uuid.uuid4(),
            knowledge_base_id=kb_id,
            title="sample.pdf",
            source_type="pdf",
            status="queued",
            created_by=user_id,
        )
        db_session.add(doc)
        db_session.flush()
        version = DocumentVersion(
            id=uuid.uuid4(),
            document_id=doc.id,
            knowledge_base_id=kb_id,
            version_number=1,
            content_hash="x",
            dedup_hash="x",
            storage_path="incoming/x",
            storage_state="stored",
            byte_size=1,
            mime_type="application/pdf",
        )
        db_session.add(version)
        db_session.flush()
        block = CanonicalBlock(
            id=uuid.uuid4(),
            document_version_id=version.id,
            sequence=0,
            block_type="paragraph",
            text="block",
            locator={"source_type": "txt", "char_start": 0, "char_end": 1},
        )
        db_session.add_all([block])
        db_session.flush()
        run = ProcessingRun(
            id=uuid.uuid4(),
            document_id=doc.id,
            document_version_id=version.id,
            knowledge_base_id=kb_id,
            status="extracted",
            chunk_strategy=DEFAULT_CHUNK_CONFIG,
        )
        db_session.add(run)
        db_session.flush()
        chunk = Chunk(
            id=uuid.uuid4(),
            processing_run_id=run.id,
            document_version_id=version.id,
            knowledge_base_id=kb_id,
            document_id=doc.id,
            sequence=0,
            text="alpha beta",
            token_count=1,
            block_start_id=block.id,
            block_end_id=block.id,
            locator_start=block.locator,
            locator_end=block.locator,
        )
        db_session.add(chunk)
        db_session.commit()

        persist_lexical_for_run(db_session, run.id)
        recompute_lexical_corpus_for_active_chunks(db_session, kb_id, [chunk.id])
        db_session.commit()

        from_db = bm25_score_chunk_from_db(
            db_session,
            knowledge_base_id=kb_id,
            chunk_id=chunk.id,
            query="alpha beta",
        )
        corpus = Bm25CorpusParams(chunk_count=1, avg_token_length=2.0, document_frequencies={"alpha": 1, "beta": 1})
        chunk_params = Bm25ChunkParams(document_length=2, term_frequencies={"alpha": 1, "beta": 1})
        assert from_db == pytest.approx(bm25_score("alpha beta", corpus=corpus, chunk=chunk_params))