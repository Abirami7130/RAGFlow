"""Phase 3O staged FAISS artifacts for one processing run."""

from __future__ import annotations

import uuid

import faiss
import numpy as np
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
from app.modules.embeddings import FAKE_EMBEDDING_DIMENSION, FakeEmbeddingProvider
from app.modules.vectorstore import (
    FaissIdMapIndex,
    build_staged_embedding_artifacts_for_run,
    build_staged_faiss_artifacts_for_run,
    validate_staged_faiss_file,
    write_sidecar_atomic,
)
from app.modules.vectorstore.paths import sidecar_abs_path, staged_faiss_abs_path

pytest.importorskip("faiss")


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


@pytest.fixture
def embedded_run(db_session: Session, kb_context: dict, isolated_index_dir: str) -> dict:
    del isolated_index_dir
    run = _run_with_chunks(
        db_session,
        kb_context["kb_id"],
        kb_context["user_id"],
        chunk_texts=["north", "south"],
    )
    provider = FakeEmbeddingProvider()
    embedded = build_staged_embedding_artifacts_for_run(db_session, run_id=run.id, provider=provider)
    db_session.commit()
    return {"run": run, "index_id": embedded.index_id, "vector_ids": embedded.vector_ids}


class TestStagedFaissArtifactsForRun:
    def test_successful_build_matches_staged_mappings(
        self,
        db_session: Session,
        embedded_run: dict,
    ) -> None:
        run = embedded_run["run"]
        before = {
            row.vector_id: (row.state, row.l2_norm)
            for row in db_session.scalars(
                select(VectorMapping).where(VectorMapping.index_id == embedded_run["index_id"])
            ).all()
        }

        result = build_staged_faiss_artifacts_for_run(
            db_session,
            run_id=run.id,
            index_id=embedded_run["index_id"],
        )
        assert result.faiss_replaced is True
        assert result.vector_ids == embedded_run["vector_ids"]

        staged_path = staged_faiss_abs_path(
            knowledge_base_id=run.knowledge_base_id,
            index_id=embedded_run["index_id"],
            processing_run_id=run.id,
        )
        validate_staged_faiss_file(
            staged_path,
            dimension=FAKE_EMBEDDING_DIMENSION,
            expected_ids=embedded_run["vector_ids"],
        )

        after = {
            row.vector_id: (row.state, row.l2_norm)
            for row in db_session.scalars(
                select(VectorMapping).where(VectorMapping.index_id == embedded_run["index_id"])
            ).all()
        }
        assert before == after

        live_index = staged_path.parent.parent / "index.faiss"
        assert not live_index.exists()

    def test_sidecar_mapping_mismatch_rejected(
        self,
        db_session: Session,
        embedded_run: dict,
    ) -> None:
        run = embedded_run["run"]
        sidecar_path = sidecar_abs_path(
            knowledge_base_id=run.knowledge_base_id,
            index_id=embedded_run["index_id"],
            processing_run_id=run.id,
        )
        wrong_ids = np.asarray([9999, 8888], dtype=np.int64)
        scale = float(np.sqrt(FAKE_EMBEDDING_DIMENSION))
        vectors = (np.ones((2, FAKE_EMBEDDING_DIMENSION), dtype=np.float32) / scale).astype(np.float32)
        write_sidecar_atomic(sidecar_path, ids=wrong_ids, vectors=vectors)

        with pytest.raises(ValueError, match="does not match staged mappings"):
            build_staged_faiss_artifacts_for_run(
                db_session,
                run_id=run.id,
                index_id=embedded_run["index_id"],
            )

    def test_retry_reuses_complete_staged_faiss_without_changing_mappings(
        self,
        db_session: Session,
        embedded_run: dict,
    ) -> None:
        run = embedded_run["run"]
        first = build_staged_faiss_artifacts_for_run(
            db_session,
            run_id=run.id,
            index_id=embedded_run["index_id"],
        )
        staged_path = staged_faiss_abs_path(
            knowledge_base_id=run.knowledge_base_id,
            index_id=embedded_run["index_id"],
            processing_run_id=run.id,
        )
        first_mtime = staged_path.stat().st_mtime_ns
        mapping_snapshot = list(
            db_session.scalars(
                select(VectorMapping).where(VectorMapping.index_id == embedded_run["index_id"])
            ).all()
        )

        second = build_staged_faiss_artifacts_for_run(
            db_session,
            run_id=run.id,
            index_id=embedded_run["index_id"],
        )
        assert second.faiss_replaced is False
        assert staged_path.stat().st_mtime_ns == first_mtime
        assert first.vector_ids == second.vector_ids

        mapping_count = db_session.scalar(
            select(func.count()).select_from(VectorMapping).where(VectorMapping.index_id == embedded_run["index_id"])
        )
        assert mapping_count == len(mapping_snapshot)
        assert all(row.state == "staged" for row in mapping_snapshot)

    def test_invalid_staged_file_replaced_on_retry(
        self,
        db_session: Session,
        embedded_run: dict,
    ) -> None:
        run = embedded_run["run"]
        build_staged_faiss_artifacts_for_run(
            db_session,
            run_id=run.id,
            index_id=embedded_run["index_id"],
        )
        staged_path = staged_faiss_abs_path(
            knowledge_base_id=run.knowledge_base_id,
            index_id=embedded_run["index_id"],
            processing_run_id=run.id,
        )
        good_ids = FaissIdMapIndex.read_from_file(staged_path).stored_vector_ids()

        bad_index = faiss.IndexIDMap2(faiss.IndexFlatIP(3))
        faiss.write_index(bad_index, str(staged_path))

        result = build_staged_faiss_artifacts_for_run(
            db_session,
            run_id=run.id,
            index_id=embedded_run["index_id"],
        )
        assert result.faiss_replaced is True
        assert FaissIdMapIndex.read_from_file(staged_path).stored_vector_ids() == good_ids