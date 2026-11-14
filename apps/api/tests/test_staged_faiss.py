"""Phase 3H durable staged FAISS index files."""

from __future__ import annotations

import uuid

import faiss
import pytest
from sqlalchemy import select
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
)
from app.modules.embeddings import FAKE_EMBEDDING_DIMENSION, FAKE_EMBEDDING_MODEL_ID, FAKE_EMBEDDING_REVISION, FakeEmbeddingProvider
from app.modules.vectorstore import (
    FaissIdMapIndex,
    build_staged_faiss_from_sidecar,
    load_sidecar,
    persist_run_sidecar,
    persist_staged_faiss_for_run,
    round_trip_sidecar_search,
    staged_faiss_is_complete,
    stage_vector_mappings_for_run,
    validate_staged_faiss_file,
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
def staged_sidecar(db_session: Session, kb_context: dict) -> dict:
    run = _run_with_chunks(
        db_session,
        kb_context["kb_id"],
        kb_context["user_id"],
        chunk_texts=["north", "south"],
    )
    staged = stage_vector_mappings_for_run(
        db_session,
        run_id=run.id,
        embedding_model=FAKE_EMBEDDING_MODEL_ID,
        embedding_revision=FAKE_EMBEDDING_REVISION,
        dimension=FAKE_EMBEDDING_DIMENSION,
    )
    db_session.commit()
    provider = FakeEmbeddingProvider()
    persist_run_sidecar(
        db_session,
        run_id=run.id,
        index_id=staged.index_id,
        provider=provider,
    )
    db_session.commit()
    return {
        "run": run,
        "index_id": staged.index_id,
        "expected_ids": staged.pairs and tuple(vector_id for _, vector_id in staged.pairs) or (),
    }


class TestStagedFaissFiles:
    def test_sidecar_to_index_round_trip(
        self,
        isolated_index_dir: str,
        staged_sidecar: dict,
    ) -> None:
        del isolated_index_dir
        run = staged_sidecar["run"]
        rel = persist_staged_faiss_for_run(
            knowledge_base_id=run.knowledge_base_id,
            index_id=staged_sidecar["index_id"],
            processing_run_id=run.id,
        )
        staged_path = staged_faiss_abs_path(
            knowledge_base_id=run.knowledge_base_id,
            index_id=staged_sidecar["index_id"],
            processing_run_id=run.id,
        )
        sidecar_path = sidecar_abs_path(
            knowledge_base_id=run.knowledge_base_id,
            index_id=staged_sidecar["index_id"],
            processing_run_id=run.id,
        )
        assert rel.endswith(f"{run.id}.faiss")
        assert staged_path.is_file()
        payload = load_sidecar(sidecar_path)
        validate_staged_faiss_file(
            staged_path,
            dimension=FAKE_EMBEDDING_DIMENSION,
            expected_ids=staged_sidecar["expected_ids"],
        )
        round_trip_sidecar_search(staged_path, payload)

    def test_staged_validation_and_retry_skip(
        self,
        isolated_index_dir: str,
        staged_sidecar: dict,
    ) -> None:
        del isolated_index_dir
        run = staged_sidecar["run"]
        sidecar_path = sidecar_abs_path(
            knowledge_base_id=run.knowledge_base_id,
            index_id=staged_sidecar["index_id"],
            processing_run_id=run.id,
        )
        staged_path = staged_faiss_abs_path(
            knowledge_base_id=run.knowledge_base_id,
            index_id=staged_sidecar["index_id"],
            processing_run_id=run.id,
        )
        build_staged_faiss_from_sidecar(sidecar_path, staged_path=staged_path)
        first_mtime = staged_path.stat().st_mtime_ns
        build_staged_faiss_from_sidecar(sidecar_path, staged_path=staged_path)
        assert staged_path.stat().st_mtime_ns == first_mtime
        assert staged_faiss_is_complete(
            staged_path,
            dimension=FAKE_EMBEDDING_DIMENSION,
            expected_ids=staged_sidecar["expected_ids"],
        )

    def test_retry_replaces_when_forced(
        self,
        db_session: Session,
        isolated_index_dir: str,
        staged_sidecar: dict,
    ) -> None:
        del isolated_index_dir
        run = staged_sidecar["run"]
        sidecar_path = sidecar_abs_path(
            knowledge_base_id=run.knowledge_base_id,
            index_id=staged_sidecar["index_id"],
            processing_run_id=run.id,
        )
        staged_path = staged_faiss_abs_path(
            knowledge_base_id=run.knowledge_base_id,
            index_id=staged_sidecar["index_id"],
            processing_run_id=run.id,
        )
        build_staged_faiss_from_sidecar(sidecar_path, staged_path=staged_path)
        before_mtime = staged_path.stat().st_mtime_ns

        chunk = db_session.scalars(select(Chunk).where(Chunk.processing_run_id == run.id, Chunk.sequence == 0)).one()
        chunk.text = "north revised"
        db_session.commit()

        persist_run_sidecar(
            db_session,
            run_id=run.id,
            index_id=staged_sidecar["index_id"],
            provider=FakeEmbeddingProvider(),
            force=True,
        )
        db_session.commit()
        build_staged_faiss_from_sidecar(sidecar_path, staged_path=staged_path, force=True)
        assert staged_path.stat().st_mtime_ns > before_mtime
        payload = load_sidecar(sidecar_path)
        round_trip_sidecar_search(staged_path, payload)

    def test_interrupted_tmp_does_not_replace_staged_file(
        self,
        isolated_index_dir: str,
        staged_sidecar: dict,
    ) -> None:
        del isolated_index_dir
        run = staged_sidecar["run"]
        sidecar_path = sidecar_abs_path(
            knowledge_base_id=run.knowledge_base_id,
            index_id=staged_sidecar["index_id"],
            processing_run_id=run.id,
        )
        staged_path = staged_faiss_abs_path(
            knowledge_base_id=run.knowledge_base_id,
            index_id=staged_sidecar["index_id"],
            processing_run_id=run.id,
        )
        build_staged_faiss_from_sidecar(sidecar_path, staged_path=staged_path)
        good = FaissIdMapIndex.read_from_file(staged_path)

        tmp_path = staged_path.with_name(f"{staged_path.name}.tmp")
        empty = faiss.IndexIDMap2(faiss.IndexFlatIP(3))
        faiss.write_index(empty, str(tmp_path))

        assert staged_faiss_is_complete(
            staged_path,
            dimension=FAKE_EMBEDDING_DIMENSION,
            expected_ids=staged_sidecar["expected_ids"],
        )
        assert FaissIdMapIndex.read_from_file(staged_path).stored_vector_ids() == good.stored_vector_ids()
        assert tmp_path.exists()
        live_index = staged_path.parent.parent / "index.faiss"
        assert not live_index.exists()