"""Phase 3R activation, rollback, retirement, deletion, and corpus stats."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

pytest_plugins = ["tests.test_canonical_blocks"]

from app.models import Chunk, Document, Job, LexicalPosting, ProcessingRun, VectorMapping
from app.modules.activation import (
    retire_processing_run,
    rollback_document_serving,
    soft_delete_document,
    vector_mapping_visible_in_search,
)
from app.modules.activation.retire import plan_retirements_after_activation
from app.worker.dispatch import execute_job
from tests.test_canonical_blocks import _add_version, _enqueue, _execute


def _active_posting_count(kb_id: uuid.UUID, run_id: uuid.UUID) -> int:
    from app.core.db import SessionLocal

    with SessionLocal() as session:
        return session.scalar(
            select(func.count())
            .select_from(LexicalPosting)
            .where(
                LexicalPosting.knowledge_base_id == kb_id,
                LexicalPosting.processing_run_id == run_id,
            )
        ) or 0


def _run_jobs(job_type: str) -> None:
    from app.core.db import SessionLocal

    with SessionLocal() as session:
        jobs = list(session.scalars(select(Job).where(Job.job_type == job_type, Job.status == "pending")).all())
        for job in jobs:
            execute_job(session, job)


class TestFirstActivation:
    def test_first_ingest_activates_document(self, kb_ctx: dict) -> None:
        from app.core.db import SessionLocal

        prepared = _enqueue(kb_ctx, b"Alpha\n\nBeta", source_type="txt", filename="first.txt")
        _execute(prepared["job_id"])

        with SessionLocal() as session:
            doc = session.get(Document, kb_ctx["document_id"])
            run = session.get(ProcessingRun, prepared["run_id"])
            assert doc is not None and run is not None
            assert doc.status == "ready"
            assert doc.current_version_id == prepared["version_id"]
            assert run.is_active is True
            assert run.status == "ready"
            assert session.scalar(select(func.count()).select_from(Chunk).where(Chunk.processing_run_id == run.id)) > 0


class TestFailedReplacement:
    def test_failed_replacement_preserves_serving_version(self, kb_ctx: dict) -> None:
        from app.core.db import SessionLocal

        first = _enqueue(kb_ctx, b"Keep\n\nServing", source_type="txt", filename="v1.txt")
        _execute(first["job_id"])
        second = _add_version(kb_ctx, b"\x80\x81", source_type="txt", filename="v2.txt", number=2)
        _execute(second["job_id"])

        with SessionLocal() as session:
            doc = session.get(Document, kb_ctx["document_id"])
            job = session.get(Job, second["job_id"])
            assert doc is not None and job is not None
            assert doc.status == "ready"
            assert doc.current_version_id == first["version_id"]
            assert job.status == "failed"


class TestSuccessfulReplacement:
    def test_second_version_replaces_serving(self, kb_ctx: dict) -> None:
        from app.core.db import SessionLocal

        first = _enqueue(kb_ctx, b"One\n\nTwo", source_type="txt", filename="v1.txt")
        _execute(first["job_id"])
        second = _add_version(kb_ctx, b"Three\n\nFour", source_type="txt", filename="v2.txt", number=2)
        _execute(second["job_id"])

        with SessionLocal() as session:
            doc = session.get(Document, kb_ctx["document_id"])
            prior = session.get(ProcessingRun, first["run_id"])
            active = session.get(ProcessingRun, second["run_id"])
            assert doc is not None and prior is not None and active is not None
            assert doc.current_version_id == second["version_id"]
            assert active.is_active is True
            assert prior.is_active is False
            assert prior.superseded_at is not None


class TestRollback:
    def test_rollback_restores_immediate_previous(self, kb_ctx: dict) -> None:
        from app.core.db import SessionLocal

        first = _enqueue(kb_ctx, b"A\n\nB", source_type="txt", filename="v1.txt")
        _execute(first["job_id"])
        second = _add_version(kb_ctx, b"C\n\nD", source_type="txt", filename="v2.txt", number=2)
        _execute(second["job_id"])

        with SessionLocal() as session:
            doc = session.get(Document, kb_ctx["document_id"])
            prior = session.get(ProcessingRun, first["run_id"])
            assert doc is not None and prior is not None
            assert doc.current_version_id == second["version_id"]
            assert prior.status == "ready" and prior.is_active is False
            rollback_document_serving(session, document_id=kb_ctx["document_id"], user_id=kb_ctx["user_id"])
            session.commit()
            doc = session.get(Document, kb_ctx["document_id"])
            assert doc is not None
            assert doc.current_version_id == first["version_id"]

    def test_rollback_rejects_retired_previous(self, kb_ctx: dict) -> None:
        from app.core.db import SessionLocal
        from app.core.errors import APIError

        first = _enqueue(kb_ctx, b"A\n\nB", source_type="txt", filename="v1.txt")
        _execute(first["job_id"])
        second = _add_version(kb_ctx, b"C\n\nD", source_type="txt", filename="v2.txt", number=2)
        _execute(second["job_id"])

        with SessionLocal() as session:
            prior = session.get(ProcessingRun, first["run_id"])
            current = session.get(ProcessingRun, second["run_id"])
            assert prior is not None and current is not None
            assert current.is_active is True
            assert prior.is_active is False
            retire_processing_run(session, run_id=first["run_id"])
            session.commit()

        with SessionLocal() as session:
            with pytest.raises(APIError) as exc:
                rollback_document_serving(session, document_id=kb_ctx["document_id"], user_id=kb_ctx["user_id"])
            assert exc.value.status_code == 409
            assert exc.value.code == "version_retired"


class TestRetirement:
    def test_third_activation_retires_oldest_ready_run(self, kb_ctx: dict) -> None:
        from app.core.db import SessionLocal

        runs = []
        for number, text in enumerate(["A\n\nB", "C\n\nD", "E\n\nF"], start=1):
            if number == 1:
                prepared = _enqueue(kb_ctx, text.encode(), source_type="txt", filename=f"v{number}.txt")
            else:
                prepared = _add_version(kb_ctx, text.encode(), source_type="txt", filename=f"v{number}.txt", number=number)
            _execute(prepared["job_id"])
            runs.append(prepared["run_id"])

        with SessionLocal() as session:
            to_retire = plan_retirements_after_activation(session, document_id=kb_ctx["document_id"])
            if not to_retire:
                to_retire = list(
                    session.scalars(
                        select(Job).where(Job.job_type == "retire_run", Job.status == "pending")
                    ).all()
                )
                to_retire = [uuid.UUID(str(job.payload["processing_run_id"])) for job in to_retire]
            assert to_retire
            assert runs[0] in to_retire

        _run_jobs("retire_run")

        with SessionLocal() as session:
            oldest = session.get(ProcessingRun, runs[0])
            assert oldest is not None
            assert oldest.retired_at is not None
            mappings = session.scalars(
                select(VectorMapping).join(Chunk).where(Chunk.processing_run_id == runs[0])
            ).all()
            assert mappings
            assert all(mapping.state == "retired" for mapping in mappings)
            assert _active_posting_count(kb_ctx["kb_id"], runs[0]) == 0


class TestServingDelete:
    def test_soft_delete_hides_vectors_before_purge(self, kb_ctx: dict) -> None:
        from app.core.db import SessionLocal
        prepared = _enqueue(kb_ctx, b"Delete\n\nMe", source_type="txt", filename="gone.txt")
        _execute(prepared["job_id"])

        with SessionLocal() as session:
            run = session.get(ProcessingRun, prepared["run_id"])
            chunk = session.scalar(select(Chunk).where(Chunk.processing_run_id == prepared["run_id"]).limit(1))
            mapping = session.scalar(select(VectorMapping).where(VectorMapping.chunk_id == chunk.id))
            assert run is not None and chunk is not None and mapping is not None
            index_id = mapping.index_id
            vector_id = mapping.vector_id
            assert vector_mapping_visible_in_search(session, index_id=index_id, vector_id=vector_id)

            soft_delete_document(session, document_id=kb_ctx["document_id"], user_id=kb_ctx["user_id"])
            session.commit()
            assert not vector_mapping_visible_in_search(session, index_id=index_id, vector_id=vector_id)

        _run_jobs("purge_document")

        with SessionLocal() as session:
            assert session.scalar(select(func.count()).select_from(Chunk).where(Chunk.document_id == kb_ctx["document_id"])) == 0


class TestCorpusRecompute:
    def test_corpus_snapshot_tracks_active_chunks(self, kb_ctx: dict) -> None:
        from app.core.db import SessionLocal

        first = _enqueue(kb_ctx, b"unique_token_alpha\n\nbeta", source_type="txt", filename="v1.txt")
        _execute(first["job_id"])

        with SessionLocal() as session:
            before_chunks = session.scalar(
                select(func.count()).select_from(Chunk).join(ProcessingRun).where(
                    Chunk.knowledge_base_id == kb_ctx["kb_id"],
                    ProcessingRun.is_active.is_(True),
                )
            )
            assert before_chunks and before_chunks > 0

        second = _add_version(kb_ctx, b"unique_token_gamma\n\ndelta", source_type="txt", filename="v2.txt", number=2)
        _execute(second["job_id"])

        with SessionLocal() as session:
            after_replace_chunks = session.scalar(
                select(func.count()).select_from(Chunk).join(ProcessingRun).where(
                    Chunk.knowledge_base_id == kb_ctx["kb_id"],
                    ProcessingRun.is_active.is_(True),
                )
            )
            assert after_replace_chunks and after_replace_chunks > 0
            rollback_document_serving(session, document_id=kb_ctx["document_id"], user_id=kb_ctx["user_id"])
            session.commit()
            after_rollback_chunks = session.scalar(
                select(func.count()).select_from(Chunk).join(ProcessingRun).where(
                    Chunk.knowledge_base_id == kb_ctx["kb_id"],
                    ProcessingRun.is_active.is_(True),
                )
            )
            assert after_rollback_chunks and after_rollback_chunks > 0