"""Phase 3Q live FAISS publish, recovery, and committed lookup."""

from __future__ import annotations

import uuid

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
    QueryTrace,
    User,
    VectorMapping,
)
from app.modules.activation import vector_mapping_visible_in_search
from app.modules.activation.stage import complete_activate_stage
from app.modules.embeddings import FakeEmbeddingProvider
from app.modules.vectorstore import (
    FaissIdMapIndex,
    build_staged_embedding_artifacts_for_run,
    build_staged_faiss_artifacts_for_run,
    committed_vector_id_set,
    filter_committed_search_hits,
    load_searchable_live_index,
    lookup_committed_mapping,
    publish_live_faiss_for_run,
    read_generation_stamp,
    recover_vector_index,
    run_vector_index_recovery,
)
from app.modules.vectorstore.paths import generation_stamp_abs_path, live_faiss_abs_path
from app.modules.vectorstore.sidecar import load_sidecar, sidecar_abs_path

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


def _kb_context(session: Session) -> dict:
    user_id = uuid.uuid4()
    kb_id = uuid.uuid4()
    session.add(
        User(
            id=user_id,
            email=f"{uuid.uuid4().hex[:12]}@example.com",
            password_hash=_password_hash(),
            display_name="Tester",
        )
    )
    session.flush()
    session.add(
        KnowledgeBase(
            id=kb_id,
            owner_id=user_id,
            name="KB",
            description="",
            retrieval_config={},
            chunk_config=DEFAULT_CHUNK_CONFIG,
        )
    )
    session.add(KnowledgeBaseMember(knowledge_base_id=kb_id, user_id=user_id, role="owner"))
    session.commit()
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


def _stage_run(session: Session, kb: dict, chunk_texts: list[str]) -> dict:
    run = _run_with_chunks(session, kb["kb_id"], kb["user_id"], chunk_texts=chunk_texts)
    provider = FakeEmbeddingProvider()
    embedded = build_staged_embedding_artifacts_for_run(session, run_id=run.id, provider=provider)
    build_staged_faiss_artifacts_for_run(session, run_id=run.id, index_id=embedded.index_id)
    session.commit()
    return {"run": run, "index_id": embedded.index_id, "vector_ids": embedded.vector_ids}


class TestLiveFaissPublish:
    def test_successful_publish_commits_mappings_and_generation(
        self,
        db_session: Session,
        isolated_index_dir: str,
    ) -> None:
        del isolated_index_dir
        kb = _kb_context(db_session)
        staged = _stage_run(db_session, kb, ["alpha chunk", "beta chunk"])
        result = publish_live_faiss_for_run(
            db_session,
            run_id=staged["run"].id,
            index_id=staged["index_id"],
        )
        db_session.commit()

        assert result.generation == 1
        assert result.mappings_committed == 2
        rows = db_session.scalars(
            select(VectorMapping).where(VectorMapping.index_id == staged["index_id"]).order_by(VectorMapping.vector_id)
        ).all()
        assert all(row.state == "committed" for row in rows)
        live, index = load_searchable_live_index(db_session, index_id=staged["index_id"])
        assert index.generation == 1
        assert set(live.stored_vector_ids()) == set(staged["vector_ids"])
        stamp = generation_stamp_abs_path(
            knowledge_base_id=staged["run"].knowledge_base_id,
            index_id=staged["index_id"],
        )
        assert read_generation_stamp(stamp) == 1

    def test_interrupt_before_mapping_commit_is_recovered(
        self,
        db_session: Session,
        isolated_index_dir: str,
    ) -> None:
        del isolated_index_dir
        kb = _kb_context(db_session)
        staged = _stage_run(db_session, kb, ["gamma chunk"])
        publish_live_faiss_for_run(
            db_session,
            run_id=staged["run"].id,
            index_id=staged["index_id"],
            commit_mappings=False,
        )
        db_session.rollback()
        db_session.expire_all()

        live_path = live_faiss_abs_path(
            knowledge_base_id=staged["run"].knowledge_base_id,
            index_id=staged["index_id"],
        )
        torn = FaissIdMapIndex.read_from_file(live_path)
        vector_id = staged["vector_ids"][0]
        sidecar = load_sidecar(
            sidecar_abs_path(
                knowledge_base_id=staged["run"].knowledge_base_id,
                index_id=staged["index_id"],
                processing_run_id=staged["run"].id,
            )
        )
        query = list(sidecar.vectors[0])
        raw_hits = torn.search(query, k=1)
        assert filter_committed_search_hits(db_session, index_id=staged["index_id"], hits=raw_hits) == []
        assert lookup_committed_mapping(db_session, index_id=staged["index_id"], vector_id=vector_id) is None

        stats = run_vector_index_recovery(db_session)
        db_session.commit()
        assert stats["indexes_rebuilt"] >= 1
        assert committed_vector_id_set(db_session, index_id=staged["index_id"]) == set()
        live, _index = load_searchable_live_index(db_session, index_id=staged["index_id"])
        assert live.size == 0

    def test_publish_retry_after_recovery(
        self,
        db_session: Session,
        isolated_index_dir: str,
    ) -> None:
        del isolated_index_dir
        kb = _kb_context(db_session)
        staged = _stage_run(db_session, kb, ["delta chunk"])
        publish_live_faiss_for_run(
            db_session,
            run_id=staged["run"].id,
            index_id=staged["index_id"],
            commit_mappings=False,
        )
        db_session.rollback()
        recover_vector_index(db_session, index_id=staged["index_id"])
        db_session.commit()

        result = publish_live_faiss_for_run(
            db_session,
            run_id=staged["run"].id,
            index_id=staged["index_id"],
        )
        db_session.commit()
        assert result.generation == 1
        assert result.mappings_committed == 1
        assert lookup_committed_mapping(db_session, index_id=staged["index_id"], vector_id=staged["vector_ids"][0])

    def test_generation_selection_uses_database_generation(
        self,
        db_session: Session,
        isolated_index_dir: str,
    ) -> None:
        del isolated_index_dir
        kb = _kb_context(db_session)
        first = _stage_run(db_session, kb, ["one"])
        publish_live_faiss_for_run(db_session, run_id=first["run"].id, index_id=first["index_id"])
        db_session.commit()

        second = _stage_run(db_session, kb, ["two", "three"])
        publish_live_faiss_for_run(db_session, run_id=second["run"].id, index_id=second["index_id"])
        db_session.commit()

        _live, index = load_searchable_live_index(db_session, index_id=first["index_id"])
        assert index.generation == 2
        assert committed_vector_id_set(db_session, index_id=first["index_id"]) == {1, 2, 3}

    def test_committed_only_lookup_never_returns_staged(
        self,
        db_session: Session,
        isolated_index_dir: str,
    ) -> None:
        del isolated_index_dir
        kb = _kb_context(db_session)
        staged = _stage_run(db_session, kb, ["staged only"])
        publish_live_faiss_for_run(
            db_session,
            run_id=staged["run"].id,
            index_id=staged["index_id"],
            commit_mappings=False,
        )
        db_session.rollback()

        mapping = db_session.scalars(select(VectorMapping).where(VectorMapping.index_id == staged["index_id"])).one()
        assert mapping.state == "staged"
        assert lookup_committed_mapping(db_session, index_id=staged["index_id"], vector_id=mapping.vector_id) is None


class TestPhase3AcceptanceGaps:
    """Roadmap Phase 3 acceptance checks not covered elsewhere."""

    def test_reopened_live_index_returns_same_neighbor_ids(
        self,
        db_session: Session,
        isolated_index_dir: str,
    ) -> None:
        del isolated_index_dir
        kb = _kb_context(db_session)
        staged = _stage_run(db_session, kb, ["alpha chunk", "beta chunk", "gamma chunk"])
        publish_live_faiss_for_run(
            db_session,
            run_id=staged["run"].id,
            index_id=staged["index_id"],
        )
        db_session.commit()

        live_before, _index = load_searchable_live_index(db_session, index_id=staged["index_id"])
        sidecar = load_sidecar(
            sidecar_abs_path(
                knowledge_base_id=staged["run"].knowledge_base_id,
                index_id=staged["index_id"],
                processing_run_id=staged["run"].id,
            )
        )
        query = list(sidecar.vectors[0])
        hits_before = filter_committed_search_hits(
            db_session,
            index_id=staged["index_id"],
            hits=live_before.search(query, k=3),
        )
        neighbor_ids_before = [vector_id for vector_id, _score in hits_before]
        assert neighbor_ids_before

        get_settings.cache_clear()
        db_session.expire_all()

        live_after, _reopened = load_searchable_live_index(db_session, index_id=staged["index_id"])
        hits_after = filter_committed_search_hits(
            db_session,
            index_id=staged["index_id"],
            hits=live_after.search(query, k=3),
        )
        neighbor_ids_after = [vector_id for vector_id, _score in hits_after]
        assert neighbor_ids_after == neighbor_ids_before

    def test_interrupted_publish_recovery_retry_activates_run(
        self,
        db_session: Session,
        isolated_index_dir: str,
    ) -> None:
        del isolated_index_dir
        kb = _kb_context(db_session)
        staged = _stage_run(db_session, kb, ["activation chunk"])
        run = db_session.get(ProcessingRun, staged["run"].id)
        doc = db_session.get(Document, run.document_id)
        assert run is not None and doc is not None
        doc.status = "extracted"
        doc.preview_version_id = run.document_version_id
        doc.current_version_id = None
        db_session.flush()

        publish_live_faiss_for_run(
            db_session,
            run_id=run.id,
            index_id=staged["index_id"],
            commit_mappings=False,
        )
        db_session.rollback()
        db_session.expire_all()

        sidecar = load_sidecar(
            sidecar_abs_path(
                knowledge_base_id=run.knowledge_base_id,
                index_id=staged["index_id"],
                processing_run_id=run.id,
            )
        )
        torn = FaissIdMapIndex.read_from_file(
            live_faiss_abs_path(knowledge_base_id=run.knowledge_base_id, index_id=staged["index_id"])
        )
        raw_hits = torn.search(list(sidecar.vectors[0]), k=1)
        assert filter_committed_search_hits(db_session, index_id=staged["index_id"], hits=raw_hits) == []

        run_vector_index_recovery(db_session)
        db_session.commit()

        trace = QueryTrace(
            id=uuid.uuid4(),
            knowledge_base_id=run.knowledge_base_id,
            kind="ingestion",
            status="running",
            config={"source_type": doc.source_type},
        )
        db_session.add(trace)
        run.trace_id = trace.id
        db_session.flush()

        complete_activate_stage(db_session, document=doc, run=run, trace=trace)
        db_session.commit()
        db_session.refresh(run)
        db_session.refresh(doc)

        assert run.is_active is True
        assert run.status == "ready"
        assert doc.status == "ready"
        assert doc.current_version_id == run.document_version_id
        vector_id = staged["vector_ids"][0]
        assert vector_mapping_visible_in_search(db_session, index_id=staged["index_id"], vector_id=vector_id)