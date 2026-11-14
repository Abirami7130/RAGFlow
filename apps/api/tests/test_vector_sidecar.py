"""Phase 3F durable per-run vector sidecar files."""

from __future__ import annotations

import uuid
import io

import numpy as np
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
    VectorMapping,
)
from app.modules.embeddings import FAKE_EMBEDDING_DIMENSION, FAKE_EMBEDDING_MODEL_ID, FAKE_EMBEDDING_REVISION, FakeEmbeddingProvider
from app.modules.vectorstore import (
    load_sidecar,
    persist_run_sidecar,
    sidecar_is_complete,
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


@pytest.fixture
def staged_run(db_session: Session, kb_context: dict) -> dict:
    run = _run_with_chunks(
        db_session,
        kb_context["kb_id"],
        kb_context["user_id"],
        chunk_texts=["alpha chunk", "beta chunk"],
    )
    staged = stage_vector_mappings_for_run(
        db_session,
        run_id=run.id,
        embedding_model=FAKE_EMBEDDING_MODEL_ID,
        embedding_revision=FAKE_EMBEDDING_REVISION,
        dimension=FAKE_EMBEDDING_DIMENSION,
    )
    db_session.commit()
    return {"run": run, "index_id": staged.index_id, "expected_ids": tuple(vector_id for _, vector_id in staged.pairs)}


class TestVectorSidecar:
    def test_round_trip_and_normalized(
        self,
        db_session: Session,
        isolated_index_dir: str,
        staged_run: dict,
    ) -> None:
        del isolated_index_dir
        provider = FakeEmbeddingProvider()
        rel = persist_run_sidecar(
            db_session,
            run_id=staged_run["run"].id,
            index_id=staged_run["index_id"],
            provider=provider,
        )
        db_session.commit()

        path = sidecar_abs_path(
            knowledge_base_id=staged_run["run"].knowledge_base_id,
            index_id=staged_run["index_id"],
            processing_run_id=staged_run["run"].id,
        )
        assert path.is_file()
        assert rel.endswith(f"{staged_run['run'].id}.npz")

        payload = load_sidecar(path)
        assert payload.ids == staged_run["expected_ids"]
        assert payload.dimension == FAKE_EMBEDDING_DIMENSION

        recomputed = provider.embed_documents(["alpha chunk", "beta chunk"])
        for row, expected in zip(payload.vectors, recomputed, strict=True):
            assert row == pytest.approx(expected, rel=1e-6, abs=1e-6)

        rows = db_session.scalars(
            select(VectorMapping).where(VectorMapping.index_id == staged_run["index_id"]).order_by(VectorMapping.vector_id)
        ).all()
        assert all(row.state == "staged" for row in rows)
        assert all(row.l2_norm is not None for row in rows)

    def test_retry_skips_when_sidecar_complete(
        self,
        db_session: Session,
        isolated_index_dir: str,
        staged_run: dict,
    ) -> None:
        del isolated_index_dir
        provider = FakeEmbeddingProvider()
        path = sidecar_abs_path(
            knowledge_base_id=staged_run["run"].knowledge_base_id,
            index_id=staged_run["index_id"],
            processing_run_id=staged_run["run"].id,
        )
        first_mtime = None

        persist_run_sidecar(
            db_session,
            run_id=staged_run["run"].id,
            index_id=staged_run["index_id"],
            provider=provider,
        )
        db_session.commit()
        first_mtime = path.stat().st_mtime_ns

        persist_run_sidecar(
            db_session,
            run_id=staged_run["run"].id,
            index_id=staged_run["index_id"],
            provider=provider,
        )
        db_session.commit()
        assert path.stat().st_mtime_ns == first_mtime

    def test_retry_replaces_when_forced_after_text_change(
        self,
        db_session: Session,
        isolated_index_dir: str,
        staged_run: dict,
    ) -> None:
        del isolated_index_dir
        provider = FakeEmbeddingProvider()
        persist_run_sidecar(
            db_session,
            run_id=staged_run["run"].id,
            index_id=staged_run["index_id"],
            provider=provider,
        )
        db_session.commit()
        path = sidecar_abs_path(
            knowledge_base_id=staged_run["run"].knowledge_base_id,
            index_id=staged_run["index_id"],
            processing_run_id=staged_run["run"].id,
        )
        before = load_sidecar(path)

        chunk = db_session.scalars(
            select(Chunk).where(Chunk.processing_run_id == staged_run["run"].id, Chunk.sequence == 0)
        ).one()
        chunk.text = "alpha chunk revised"
        db_session.commit()

        persist_run_sidecar(
            db_session,
            run_id=staged_run["run"].id,
            index_id=staged_run["index_id"],
            provider=provider,
            force=True,
        )
        db_session.commit()
        after = load_sidecar(path)
        assert after.ids == before.ids
        assert after.vectors[0] != before.vectors[0]

    def test_interrupted_tmp_does_not_replace_final(
        self,
        db_session: Session,
        isolated_index_dir: str,
        staged_run: dict,
    ) -> None:
        del isolated_index_dir
        provider = FakeEmbeddingProvider()
        path = sidecar_abs_path(
            knowledge_base_id=staged_run["run"].knowledge_base_id,
            index_id=staged_run["index_id"],
            processing_run_id=staged_run["run"].id,
        )
        persist_run_sidecar(
            db_session,
            run_id=staged_run["run"].id,
            index_id=staged_run["index_id"],
            provider=provider,
        )
        db_session.commit()
        good = load_sidecar(path)

        tmp_path = path.with_name(f"{path.name}.tmp")
        buffer = io.BytesIO()
        np.savez_compressed(
            buffer,
            ids=np.array([999], dtype=np.int64),
            vectors=np.ones((1, 3), dtype=np.float32),
        )
        tmp_path.write_bytes(buffer.getvalue())

        assert sidecar_is_complete(path, expected_ids=staged_run["expected_ids"], dimension=FAKE_EMBEDDING_DIMENSION)
        assert load_sidecar(path).vectors == good.vectors
        assert tmp_path.exists()