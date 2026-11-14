"""Phase 3N staged embedding artifacts for a processing run."""

from __future__ import annotations

import math
import uuid
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
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
    VectorMapping,
)
from app.modules.embeddings import FAKE_EMBEDDING_DIMENSION, FAKE_EMBEDDING_MODEL_ID, FAKE_EMBEDDING_REVISION, FakeEmbeddingProvider
from app.modules.vectorstore import (
    build_staged_embedding_artifacts_for_run,
    load_sidecar,
    persist_run_sidecar,
    stage_vector_mappings_for_run,
)
from app.modules.vectorstore.paths import sidecar_abs_path


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
def isolated_index_dir(monkeypatch: pytest.MonkeyPatch) -> str:
    import shutil
    import tempfile

    path = tempfile.mkdtemp(prefix="ragflow-indexes-")
    monkeypatch.setenv("INDEX_DIR", path)
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()
    shutil.rmtree(path, ignore_errors=True)


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
) -> ProcessingRun:
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
    for sequence, text in enumerate(chunk_texts):
        session.add(
            Chunk(
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
        )
    session.flush()
    return run


class _FailingBatchProvider(FakeEmbeddingProvider):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if texts:
            return [[math.nan] * self.dimension]
        return []


class TestStagedEmbeddingArtifacts:
    def test_builds_mappings_sidecar_and_run_metadata(
        self,
        db_session: Session,
        isolated_index_dir: str,
        kb_context: dict,
    ) -> None:
        del isolated_index_dir
        run = _run_with_chunks(
            db_session,
            kb_context["kb_id"],
            kb_context["user_id"],
            chunk_texts=["alpha chunk", "beta chunk"],
        )
        provider = FakeEmbeddingProvider()
        result = build_staged_embedding_artifacts_for_run(
            db_session,
            run_id=run.id,
            provider=provider,
        )
        db_session.commit()
        db_session.refresh(run)

        assert run.embedding_model == FAKE_EMBEDDING_MODEL_ID
        assert run.embedding_revision == FAKE_EMBEDDING_REVISION
        assert run.embedding_dimension == FAKE_EMBEDDING_DIMENSION
        assert result.sidecar_replaced is True
        assert len(result.vector_ids) == 2

        path = sidecar_abs_path(
            knowledge_base_id=run.knowledge_base_id,
            index_id=result.index_id,
            processing_run_id=run.id,
        )
        payload = load_sidecar(path)
        assert payload.ids == result.vector_ids

        mapping_count = db_session.scalar(
            select(func.count()).select_from(VectorMapping).where(VectorMapping.index_id == result.index_id)
        )
        assert mapping_count == 2
        rows = db_session.scalars(
            select(VectorMapping).where(VectorMapping.index_id == result.index_id).order_by(VectorMapping.vector_id)
        ).all()
        assert all(row.state == "staged" for row in rows)

    def test_retry_is_idempotent_for_mappings_and_sidecar(
        self,
        db_session: Session,
        isolated_index_dir: str,
        kb_context: dict,
    ) -> None:
        del isolated_index_dir
        run = _run_with_chunks(
            db_session,
            kb_context["kb_id"],
            kb_context["user_id"],
            chunk_texts=["alpha chunk", "beta chunk"],
        )
        provider = FakeEmbeddingProvider()
        first = build_staged_embedding_artifacts_for_run(db_session, run_id=run.id, provider=provider)
        db_session.commit()
        path = sidecar_abs_path(
            knowledge_base_id=run.knowledge_base_id,
            index_id=first.index_id,
            processing_run_id=run.id,
        )
        first_mtime = path.stat().st_mtime_ns
        mapping_count = db_session.scalar(
            select(func.count()).select_from(VectorMapping).where(VectorMapping.index_id == first.index_id)
        )

        second = build_staged_embedding_artifacts_for_run(db_session, run_id=run.id, provider=provider)
        db_session.commit()
        assert second.sidecar_replaced is False
        assert path.stat().st_mtime_ns == first_mtime
        assert db_session.scalar(
            select(func.count()).select_from(VectorMapping).where(VectorMapping.index_id == first.index_id)
        ) == mapping_count
        assert first.vector_ids == second.vector_ids

    def test_run_metadata_set_only_after_valid_sidecar(
        self,
        db_session: Session,
        isolated_index_dir: str,
        kb_context: dict,
    ) -> None:
        del isolated_index_dir
        run = _run_with_chunks(
            db_session,
            kb_context["kb_id"],
            kb_context["user_id"],
            chunk_texts=["broken embed"],
        )
        db_session.commit()
        provider = _FailingBatchProvider()
        with pytest.raises(ValueError, match="non-finite"):
            build_staged_embedding_artifacts_for_run(db_session, run_id=run.id, provider=provider)
        db_session.rollback()
        run = db_session.get(ProcessingRun, run.id)
        assert run is not None

        assert run.embedding_model is None
        assert run.embedding_revision is None
        assert run.embedding_dimension is None

    def test_retry_records_metadata_when_sidecar_already_complete(
        self,
        db_session: Session,
        isolated_index_dir: str,
        kb_context: dict,
    ) -> None:
        del isolated_index_dir
        run = _run_with_chunks(
            db_session,
            kb_context["kb_id"],
            kb_context["user_id"],
            chunk_texts=["alpha chunk"],
        )
        provider = FakeEmbeddingProvider()
        staged = stage_vector_mappings_for_run(
            db_session,
            run_id=run.id,
            embedding_model=FAKE_EMBEDDING_MODEL_ID,
            embedding_revision=FAKE_EMBEDDING_REVISION,
            dimension=FAKE_EMBEDDING_DIMENSION,
        )
        persist_run_sidecar(
            db_session,
            run_id=run.id,
            index_id=staged.index_id,
            provider=provider,
        )
        db_session.commit()
        assert run.embedding_model is None

        build_staged_embedding_artifacts_for_run(db_session, run_id=run.id, provider=provider)
        db_session.commit()
        db_session.refresh(run)

        assert run.embedding_model == FAKE_EMBEDDING_MODEL_ID
        assert run.embedding_revision == FAKE_EMBEDDING_REVISION
        assert run.embedding_dimension == FAKE_EMBEDDING_DIMENSION