"""Milestone 2B: worker claiming, retries, and traces."""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text

pytest_plugins = ["test_canonical_blocks"]

from app.domain.configs import DEFAULT_CHUNK_CONFIG
from app.models import Chunk, ChunkStats, Document, DocumentVersion, Job, KnowledgeBase, KnowledgeBaseMember, ProcessingRun, QueryTrace, TraceEvent, User
from app.modules.ingestion.index_stages import post_chunk_index_complete
from tests.test_canonical_blocks import INDEX_STAGE_TAIL, _enqueue, _events, _execute
from app.modules.ingestion.enqueue import enqueue_ingest
from app.modules.ingestion.pipeline_stages import INGESTION_STAGES
from app.worker.claim import claim_next_job, refresh_job_lock, schedule_automatic_retry
from app.worker.dispatch import execute_job
from app.worker.errors import AUTOMATIC_RETRY_DELAYS_SECONDS, INGEST_MAX_ATTEMPTS, TransientWorkerError
from app.worker.handlers.noop import handle_noop
from app.worker.loop import WorkerRunner


def _password_hash() -> str:
    from argon2 import PasswordHasher

    return PasswordHasher().hash("password-long-enough")


@pytest.fixture
def kb_ctx() -> dict:
    from app.core.db import SessionLocal

    with SessionLocal() as session:
        user = User(email=f"{uuid.uuid4().hex}@example.com", password_hash=_password_hash(), display_name="W")
        session.add(user)
        session.flush()
        kb = KnowledgeBase(
            owner_id=user.id,
            name="Worker KB",
            description="",
            retrieval_config={},
            chunk_config=DEFAULT_CHUNK_CONFIG,
        )
        session.add(kb)
        session.flush()
        session.add(KnowledgeBaseMember(knowledge_base_id=kb.id, user_id=user.id, role="owner"))
        doc = Document(
            knowledge_base_id=kb.id,
            title="Doc",
            source_type="pdf",
            status="queued",
            created_by=user.id,
        )
        session.add(doc)
        session.flush()
        version = DocumentVersion(
            document_id=doc.id,
            knowledge_base_id=kb.id,
            version_number=1,
            storage_path=f"{kb.id}/{doc.id}/{uuid.uuid4()}",
            storage_state="stored",
            byte_size=10,
            mime_type="application/pdf",
            content_hash="abc",
            dedup_hash="abc",
        )
        session.add(version)
        session.flush()
        session.commit()
        yield {
            "user_id": user.id,
            "kb_id": kb.id,
            "document_id": doc.id,
            "version_id": version.id,
        }


def _pending_noop_job(session, kb_id: uuid.UUID) -> Job:
    job = Job(
        job_type="noop",
        idempotency_key=f"noop:{uuid.uuid4()}",
        knowledge_base_id=kb_id,
        payload={},
        status="pending",
    )
    session.add(job)
    session.commit()
    return job


class TestJobClaiming:
    def test_concurrent_workers_claim_distinct_jobs(self, kb_ctx: dict) -> None:
        from app.core.db import SessionLocal

        with SessionLocal() as session:
            _pending_noop_job(session, kb_ctx["kb_id"])
            _pending_noop_job(session, kb_ctx["kb_id"])

        claimed: list[str] = []
        lock = threading.Lock()

        def worker(name: str) -> None:
            with SessionLocal() as session:
                job = claim_next_job(session, worker_id=name)
                if job is not None:
                    with lock:
                        claimed.append(name)

        threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(claimed) == 2
        assert len(set(claimed)) == 2

    def test_expired_lock_is_reclaimed(self, kb_ctx: dict) -> None:
        from app.core.db import SessionLocal

        with SessionLocal() as session:
            job = _pending_noop_job(session, kb_ctx["kb_id"])
            job_id = job.id

        with SessionLocal() as session:
            first = claim_next_job(session, worker_id="w1")
            assert first is not None
            first.locked_at = datetime.now(UTC) - timedelta(seconds=1200)
            first.lock_timeout_seconds = 60
            session.commit()

        with SessionLocal() as session:
            second = claim_next_job(session, worker_id="w2")
            assert second is not None
            assert second.id == job_id
            assert second.attempts == 2
            assert second.locked_by == "w2"


class TestHeartbeatAndNoop:
    def test_heartbeat_refreshes_lock(self, kb_ctx: dict) -> None:
        from app.core.db import SessionLocal

        with SessionLocal() as session:
            job = _pending_noop_job(session, kb_ctx["kb_id"])

        with SessionLocal() as session:
            claimed = claim_next_job(session, worker_id="hb-worker")
            assert claimed is not None
            old = claimed.locked_at
            assert refresh_job_lock(session, job_id=claimed.id, worker_id="hb-worker")
            session.refresh(claimed)
            assert claimed.locked_at >= old

    def test_noop_job_completes(self, kb_ctx: dict) -> None:
        from app.core.db import SessionLocal

        with SessionLocal() as session:
            job = _pending_noop_job(session, kb_ctx["kb_id"])

        with SessionLocal() as session:
            claimed = claim_next_job(session, worker_id="noop-worker")
            assert claimed is not None
            handle_noop(session, claimed)
            claimed.status = "succeeded"
            session.commit()

        with SessionLocal() as session:
            stored = session.get(Job, job.id)
            assert stored is not None
            assert stored.status == "succeeded"


class TestIngestWorker:
    def test_ingest_fails_with_trace_tail(self, kb_ctx: dict, isolated_storage, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.core.db import SessionLocal
        from app.modules.ingestion import storage as blob_storage

        data = b"%PDF-1.4\n%%EOF"
        monkeypatch.setenv("STORAGE_DIR", isolated_storage)
        from app.core.config import get_settings

        get_settings.cache_clear()
        incoming = blob_storage.write_incoming(data, uuid.uuid4())

        with SessionLocal() as session:
            doc = session.get(Document, kb_ctx["document_id"])
            kb = session.get(KnowledgeBase, kb_ctx["kb_id"])
            version = session.get(DocumentVersion, kb_ctx["version_id"])
            assert doc and kb and version
            version.storage_path = incoming
            blob_storage.promote_incoming_to_final(
                incoming_rel=incoming,
                final_rel=blob_storage.rel_final_path(kb.id, doc.id, version.id),
            )
            version.storage_path = blob_storage.rel_final_path(kb.id, doc.id, version.id)
            version.storage_state = "stored"
            version.content_hash = blob_storage.sha256_bytes(data)
            version.dedup_hash = version.content_hash
            job = enqueue_ingest(
                session,
                kb=kb,
                document=doc,
                version=version,
                user_id=kb_ctx["user_id"],
                source_type="pdf",
                display_filename="Doc",
                original_filename="doc.pdf",
            )
            session.commit()
            job_id = job.id
            run_id = doc.pending_run_id
            trace_id = session.scalar(select(ProcessingRun.trace_id).where(ProcessingRun.id == run_id))

        with SessionLocal() as session:
            job = session.get(Job, job_id)
            assert job is not None
            execute_job(session, job)

        with SessionLocal() as session:
            job = session.get(Job, job_id)
            doc = session.get(Document, kb_ctx["document_id"])
            run = session.get(ProcessingRun, doc.pending_run_id) if doc else None
            assert job is not None and job.status == "failed"
            assert job.last_error_code == "pdf_unreadable"
            assert doc is not None
            assert doc.pending_run_id is None
            assert doc.status == "failed"
            assert trace_id is not None
            stages = session.scalars(select(TraceEvent.stage).where(TraceEvent.trace_id == trace_id)).all()
            assert "extract" in stages
            assert set(INGESTION_STAGES).issubset(set(stages))

    def test_storage_missing_is_permanent_no_retry(self, kb_ctx: dict, isolated_storage, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.core.db import SessionLocal
        from app.modules.ingestion import storage as blob_storage

        monkeypatch.setenv("STORAGE_DIR", isolated_storage)
        from app.core.config import get_settings

        get_settings.cache_clear()

        scheduled_delays: list[int] = []

        def record_delay(db, job, *, error_code: str, error_message: str, delay_seconds: int) -> None:
            scheduled_delays.append(delay_seconds)
            schedule_automatic_retry(
                db,
                job,
                error_code=error_code,
                error_message=error_message,
                delay_seconds=delay_seconds,
            )

        monkeypatch.setattr("app.worker.dispatch.schedule_automatic_retry", record_delay)

        data = b"%PDF-1.4\n%%EOF"
        missing_rel = "missing/bytes/on/disk.pdf"

        with SessionLocal() as session:
            doc = session.get(Document, kb_ctx["document_id"])
            kb = session.get(KnowledgeBase, kb_ctx["kb_id"])
            version = session.get(DocumentVersion, kb_ctx["version_id"])
            assert doc and kb and version
            doc.status = "ready"
            doc.current_version_id = version.id
            doc.preview_version_id = version.id
            version.storage_path = blob_storage.rel_final_path(kb.id, doc.id, version.id)
            version.storage_state = "stored"
            blob_storage.promote_incoming_to_final(
                incoming_rel=blob_storage.write_incoming(data, uuid.uuid4()),
                final_rel=version.storage_path,
            )
            session.add(
                ProcessingRun(
                    document_id=doc.id,
                    document_version_id=version.id,
                    knowledge_base_id=kb.id,
                    status="ready",
                    is_active=True,
                    chunk_strategy=kb.chunk_config,
                )
            )
            pending_version = DocumentVersion(
                document_id=doc.id,
                knowledge_base_id=kb.id,
                version_number=2,
                storage_path=missing_rel,
                storage_state="stored",
                byte_size=len(data),
                mime_type="application/pdf",
                content_hash=blob_storage.sha256_bytes(data),
                dedup_hash=blob_storage.sha256_bytes(data),
            )
            session.add(pending_version)
            session.flush()
            job = enqueue_ingest(
                session,
                kb=kb,
                document=doc,
                version=pending_version,
                user_id=kb_ctx["user_id"],
                source_type="pdf",
                display_filename="Doc",
                original_filename="doc.pdf",
            )
            session.commit()
            job_id = job.id
            run_id = doc.pending_run_id
            trace_id = session.scalar(select(ProcessingRun.trace_id).where(ProcessingRun.id == run_id))
            serving_version = doc.current_version_id

        with SessionLocal() as session:
            job = claim_next_job(session, worker_id="storage-missing")
            assert job is not None
            execute_job(session, job)

        assert scheduled_delays == []

        with SessionLocal() as session:
            job = session.get(Job, job_id)
            doc = session.get(Document, kb_ctx["document_id"])
            run = session.get(ProcessingRun, run_id)
            assert job is not None and doc is not None and run is not None
            assert job.attempts == 1
            assert job.status == "failed"
            assert job.last_error_code == "storage_missing"
            assert run.status == "failed"
            assert run.error_code == "storage_missing"
            assert doc.pending_run_id is None
            assert doc.pending_version_id is None
            assert doc.status == "ready"
            assert doc.current_version_id == serving_version
            assert trace_id is not None
            stages = session.scalars(select(TraceEvent.stage).where(TraceEvent.trace_id == trace_id)).all()
            assert set(INGESTION_STAGES).issubset(set(stages))

    def test_transient_retry_exhaustion_lifecycle(self, kb_ctx: dict, isolated_storage, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.core.db import SessionLocal
        from app.modules.ingestion import storage as blob_storage

        data = b"%PDF-1.4\n%%EOF"
        monkeypatch.setenv("STORAGE_DIR", isolated_storage)
        from app.core.config import get_settings

        get_settings.cache_clear()

        scheduled_delays: list[int] = []

        def record_delay(db, job, *, error_code: str, error_message: str, delay_seconds: int) -> None:
            scheduled_delays.append(delay_seconds)
            schedule_automatic_retry(
                db,
                job,
                error_code=error_code,
                error_message=error_message,
                delay_seconds=delay_seconds,
            )

        monkeypatch.setattr("app.worker.dispatch.schedule_automatic_retry", record_delay)

        def always_transient(_db, _job) -> None:
            raise TransientWorkerError("storage_unavailable", "simulated transient")

        import app.worker.dispatch as dispatch_module

        monkeypatch.setitem(dispatch_module.HANDLERS, "ingest_document", always_transient)

        with SessionLocal() as session:
            doc = session.get(Document, kb_ctx["document_id"])
            kb = session.get(KnowledgeBase, kb_ctx["kb_id"])
            version = session.get(DocumentVersion, kb_ctx["version_id"])
            assert doc and kb and version
            doc.status = "ready"
            doc.current_version_id = version.id
            doc.preview_version_id = version.id
            incoming = blob_storage.write_incoming(data, uuid.uuid4())
            blob_storage.promote_incoming_to_final(
                incoming_rel=incoming,
                final_rel=blob_storage.rel_final_path(kb.id, doc.id, version.id),
            )
            version.storage_path = blob_storage.rel_final_path(kb.id, doc.id, version.id)
            version.storage_state = "stored"
            pending_version = DocumentVersion(
                document_id=doc.id,
                knowledge_base_id=kb.id,
                version_number=2,
                storage_path=version.storage_path,
                storage_state="stored",
                byte_size=len(data),
                mime_type="application/pdf",
                content_hash=blob_storage.sha256_bytes(data),
                dedup_hash=blob_storage.sha256_bytes(data),
            )
            session.add(pending_version)
            session.flush()
            job = enqueue_ingest(
                session,
                kb=kb,
                document=doc,
                version=pending_version,
                user_id=kb_ctx["user_id"],
                source_type="pdf",
                display_filename="Doc",
                original_filename="doc.pdf",
            )
            session.commit()
            job_id = job.id
            assert job.max_attempts == INGEST_MAX_ATTEMPTS
            run_id = doc.pending_run_id
            trace_id = session.scalar(select(ProcessingRun.trace_id).where(ProcessingRun.id == run_id))
            serving_version = doc.current_version_id

        claim_count = 0
        while claim_count < INGEST_MAX_ATTEMPTS + 2:
            with SessionLocal() as session:
                job = session.get(Job, job_id)
                assert job is not None
                if job.status == "failed":
                    break
                if job.status == "running":
                    job.status = "pending"
                    job.locked_at = None
                    job.locked_by = None
                job.run_after = datetime.now(UTC) - timedelta(seconds=1)
                session.commit()
            with SessionLocal() as session:
                claimed = claim_next_job(session, worker_id=f"exhaust-{claim_count}")
                if claimed is None:
                    job = session.get(Job, job_id)
                    if job and job.status == "failed":
                        break
                    pytest.fail("expected claim while job not failed")
                execute_job(session, claimed)
                claim_count += 1

        assert scheduled_delays == list(AUTOMATIC_RETRY_DELAYS_SECONDS)

        with SessionLocal() as session:
            job = session.get(Job, job_id)
            doc = session.get(Document, kb_ctx["document_id"])
            run = session.get(ProcessingRun, run_id)
            assert job is not None and doc is not None and run is not None
            assert job.attempts == INGEST_MAX_ATTEMPTS
            assert job.status == "failed"
            assert job.last_error_code == "storage_unavailable"
            assert run.status == "failed"
            assert run.error_code == "storage_unavailable"
            assert doc.pending_run_id is None
            assert doc.pending_version_id is None
            assert doc.status == "ready"
            assert doc.current_version_id == serving_version
            assert trace_id is not None
            stages = session.scalars(select(TraceEvent.stage).where(TraceEvent.trace_id == trace_id)).all()
            assert set(INGESTION_STAGES).issubset(set(stages))

    def test_transient_retry_schedules_backoff(self, kb_ctx: dict, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.core.db import SessionLocal
        from app.worker.errors import TransientWorkerError

        calls = {"n": 0}

        def flaky(_db, _job):
            calls["n"] += 1
            if calls["n"] < 2:
                raise TransientWorkerError("worker_lost", "simulated")

        import app.worker.dispatch as dispatch_module

        monkeypatch.setitem(dispatch_module.HANDLERS, "noop", flaky)

        with SessionLocal() as session:
            pending = _pending_noop_job(session, kb_ctx["kb_id"])
            job_id = pending.id

        with SessionLocal() as session:
            job = claim_next_job(session, worker_id="t1")
            assert job is not None
            execute_job(session, job)

        with SessionLocal() as session:
            job = session.get(Job, job_id)
            assert job is not None
            assert job.status == "pending"
            assert job.attempts == 1
            assert job.run_after > datetime.now(UTC)

    def test_inconsistent_state_preserves_serving_version(self, kb_ctx: dict, isolated_storage, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.core.db import SessionLocal
        from app.modules.ingestion import storage as blob_storage

        data = b"%PDF-1.4\n%%EOF"
        monkeypatch.setenv("STORAGE_DIR", isolated_storage)
        from app.core.config import get_settings

        get_settings.cache_clear()

        with SessionLocal() as session:
            doc = session.get(Document, kb_ctx["document_id"])
            kb = session.get(KnowledgeBase, kb_ctx["kb_id"])
            version = session.get(DocumentVersion, kb_ctx["version_id"])
            assert doc and kb and version
            doc.status = "ready"
            doc.current_version_id = version.id
            doc.preview_version_id = version.id
            incoming = blob_storage.write_incoming(data, uuid.uuid4())
            version.storage_path = blob_storage.rel_final_path(kb.id, doc.id, version.id)
            blob_storage.promote_incoming_to_final(
                incoming_rel=incoming,
                final_rel=version.storage_path,
            )
            version.storage_state = "stored"
            pending_version = DocumentVersion(
                document_id=doc.id,
                knowledge_base_id=kb.id,
                version_number=2,
                storage_path=version.storage_path,
                storage_state="stored",
                byte_size=len(data),
                mime_type="application/pdf",
                content_hash=blob_storage.sha256_bytes(data),
                dedup_hash=blob_storage.sha256_bytes(data),
            )
            session.add(pending_version)
            session.flush()
            job = enqueue_ingest(
                session,
                kb=kb,
                document=doc,
                version=pending_version,
                user_id=kb_ctx["user_id"],
                source_type="pdf",
                display_filename="Doc",
                original_filename="doc.pdf",
            )
            session.commit()
            job_id = job.id
            serving_version = doc.current_version_id

        with SessionLocal() as session:
            job = session.get(Job, job_id)
            assert job is not None
            execute_job(session, job)

        with SessionLocal() as session:
            doc = session.get(Document, kb_ctx["document_id"])
            job = session.get(Job, job_id)
            assert doc is not None and job is not None
            assert doc.status == "ready"
            assert doc.current_version_id == serving_version
            assert job.status == "failed"
            assert job.last_error_code == "inconsistent_state"


class TestIngestIndexStages:
    def test_successful_index_stage_order_and_activation(self, kb_ctx: dict) -> None:
        from app.core.db import SessionLocal
        from app.modules.embeddings import FAKE_EMBEDDING_MODEL_ID

        prepared = _enqueue(kb_ctx, b"Hello\n\nWorld", source_type="txt", filename="index.txt")
        _execute(prepared["job_id"])

        tail = [(event.stage, event.status, event.skip_reason) for event in _events(prepared["trace_id"])[3:]]
        assert tail == list(INDEX_STAGE_TAIL)

        with SessionLocal() as session:
            doc = session.get(Document, kb_ctx["document_id"])
            run = session.get(ProcessingRun, prepared["run_id"])
            assert doc is not None and run is not None
            assert doc.status == "ready"
            assert doc.current_version_id == prepared["version_id"]
            assert doc.preview_version_id is None
            assert run.status == "ready"
            assert run.is_active is True
            assert run.embedding_model == FAKE_EMBEDDING_MODEL_ID

            chunk = session.scalars(select(Chunk).where(Chunk.processing_run_id == run.id)).first()
            assert chunk is not None
            assert session.get(ChunkStats, chunk.id) is not None

    def test_embed_failure_leaves_no_false_success_on_later_stages(self, kb_ctx: dict, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.modules.ingestion.index_stages as index_stages

        def explode(*_args, **_kwargs):
            raise ValueError("simulated embed failure")

        monkeypatch.setattr(index_stages, "build_staged_embedding_artifacts_for_run", explode)
        prepared = _enqueue(kb_ctx, b"Hello\n\nWorld", source_type="txt", filename="fail-embed.txt")

        from app.core.db import SessionLocal
        from app.worker.dispatch import execute_job

        with SessionLocal() as session:
            job = session.get(Job, prepared["job_id"])
            assert job is not None
            execute_job(session, job)

        events = _events(prepared["trace_id"])
        assert any(event.stage == "chunk" and event.status == "succeeded" for event in events)
        assert not any(event.stage == "vector_index" and event.status == "succeeded" for event in events)
        assert not any(event.stage == "lexical_index" and event.status == "succeeded" for event in events)

        with SessionLocal() as session:
            job = session.get(Job, prepared["job_id"])
            assert job is not None
            assert job.status == "pending"

    def test_retry_resumes_indexing_after_embed_failure(self, kb_ctx: dict, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.modules.ingestion.index_stages as index_stages

        calls = {"n": 0}
        original = index_stages.build_staged_embedding_artifacts_for_run

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ValueError("simulated embed failure")
            return original(*args, **kwargs)

        monkeypatch.setattr(index_stages, "build_staged_embedding_artifacts_for_run", flaky)
        prepared = _enqueue(kb_ctx, b"Hello\n\nWorld", source_type="txt", filename="retry-index.txt")

        from app.core.db import SessionLocal
        from app.worker.claim import claim_next_job
        from app.worker.dispatch import execute_job

        with SessionLocal() as session:
            job = session.get(Job, prepared["job_id"])
            assert job is not None
            execute_job(session, job)

        with SessionLocal() as session:
            job = session.get(Job, prepared["job_id"])
            assert job is not None
            job.run_after = datetime.now(UTC) - timedelta(seconds=1)
            session.commit()

        with SessionLocal() as session:
            claimed = claim_next_job(session, worker_id="index-retry")
            assert claimed is not None
            execute_job(session, claimed)

        events = _events(prepared["trace_id"])
        assert any(event.stage == "embed" and event.status == "succeeded" for event in events)
        assert any(event.stage == "vector_index" and event.status == "succeeded" for event in events)
        assert any(event.stage == "lexical_index" and event.status == "succeeded" for event in events)
        assert any(event.stage == "activate" and event.status == "succeeded" for event in events)
        with SessionLocal() as session:
            run = session.get(ProcessingRun, prepared["run_id"])
            doc = session.get(Document, kb_ctx["document_id"])
            assert run is not None and doc is not None
            assert doc.status == "ready"
            assert doc.current_version_id == prepared["version_id"]
