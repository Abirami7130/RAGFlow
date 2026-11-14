"""Milestone 2C.1: TXT/Markdown canonical blocks, lifecycle, and block reads."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, insert, select

from app.domain.configs import DEFAULT_CHUNK_CONFIG
from app.models import (
    CanonicalBlock,
    Document,
    DocumentVersion,
    Job,
    KnowledgeBase,
    KnowledgeBaseMember,
    ProcessingRun,
    QueryTrace,
    TraceEvent,
    User,
)
from app.modules.ingestion.enqueue import enqueue_ingest
from app.modules.ingestion.user_retry import request_user_retry_on_version
from app.modules.ingestion.extraction import extract_raw_blocks, normalize_blocks
from app.modules.ingestion.pipeline_stages import INGESTION_STAGES
from app.worker.claim import claim_next_job
from app.worker.dispatch import execute_job

FIXTURES = Path(__file__).parent / "fixtures" / "extraction"
INDEX_STAGE_TAIL = (
    ("embed", "succeeded", None),
    ("vector_index", "succeeded", None),
    ("lexical_index", "succeeded", None),
    ("activate", "succeeded", None),
)
DEFERRED_TAIL: tuple[tuple[str, str, str | None], ...] = ()


def _password_hash() -> str:
    from argon2 import PasswordHasher

    return PasswordHasher().hash("password-long-enough")


@pytest.fixture(autouse=True)
def _storage(isolated_storage: str) -> str:
    return isolated_storage


@pytest.fixture
def kb_ctx() -> dict:
    from app.core.db import SessionLocal

    with SessionLocal() as session:
        user = User(email=f"{uuid.uuid4().hex}@example.com", password_hash=_password_hash(), display_name="W")
        session.add(user)
        session.flush()
        kb = KnowledgeBase(
            owner_id=user.id,
            name="Extract KB",
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
            source_type="txt",
            status="queued",
            created_by=user.id,
        )
        session.add(doc)
        session.flush()
        version = DocumentVersion(
            document_id=doc.id,
            knowledge_base_id=kb.id,
            version_number=1,
            storage_path="pending",
            storage_state="incoming",
            byte_size=1,
            mime_type="text/plain",
        )
        session.add(version)
        session.commit()
        yield {
            "user_id": user.id,
            "kb_id": kb.id,
            "document_id": doc.id,
            "version_id": version.id,
        }


def _store(kb: KnowledgeBase, doc: Document, version: DocumentVersion, data: bytes) -> None:
    from app.modules.ingestion import storage as blob_storage

    incoming = blob_storage.write_incoming(data, uuid.uuid4())
    final = blob_storage.rel_final_path(kb.id, doc.id, version.id)
    blob_storage.promote_incoming_to_final(incoming_rel=incoming, final_rel=final)
    version.storage_path = final
    version.storage_state = "stored"
    version.byte_size = len(data)
    version.content_hash = blob_storage.sha256_bytes(data)
    version.dedup_hash = version.content_hash
    if doc.source_type == "md":
        version.mime_type = "text/markdown"
    elif doc.source_type == "pdf":
        version.mime_type = "application/pdf"
    else:
        version.mime_type = "text/plain"


def _enqueue(ctx: dict, data: bytes, *, source_type: str, filename: str, version_id: uuid.UUID | None = None) -> dict:
    from app.core.db import SessionLocal

    with SessionLocal() as session:
        doc = session.get(Document, ctx["document_id"])
        kb = session.get(KnowledgeBase, ctx["kb_id"])
        assert doc is not None and kb is not None
        doc.source_type = source_type
        if version_id is None:
            version = session.get(DocumentVersion, ctx["version_id"])
        else:
            version = session.get(DocumentVersion, version_id)
        assert version is not None
        _store(kb, doc, version, data)
        job = enqueue_ingest(
            session,
            kb=kb,
            document=doc,
            version=version,
            user_id=ctx["user_id"],
            source_type=source_type,
            display_filename=filename,
            original_filename=filename,
        )
        session.commit()
        run_id = doc.pending_run_id
        trace_id = session.scalar(select(ProcessingRun.trace_id).where(ProcessingRun.id == run_id))
        return {"job_id": job.id, "run_id": run_id, "trace_id": trace_id, "version_id": version.id}


def _execute(job_id: uuid.UUID) -> None:
    from app.core.db import SessionLocal

    with SessionLocal() as session:
        job = session.get(Job, job_id)
        assert job is not None
        execute_job(session, job)


def _blocks(version_id: uuid.UUID) -> list[CanonicalBlock]:
    from app.core.db import SessionLocal

    with SessionLocal() as session:
        return list(
            session.scalars(
                select(CanonicalBlock)
                .where(CanonicalBlock.document_version_id == version_id)
                .order_by(CanonicalBlock.sequence)
            ).all()
        )


def _events(trace_id: uuid.UUID) -> list[TraceEvent]:
    from app.core.db import SessionLocal

    with SessionLocal() as session:
        return list(
            session.scalars(select(TraceEvent).where(TraceEvent.trace_id == trace_id).order_by(TraceEvent.sequence)).all()
        )


def _add_version(ctx: dict, data: bytes, *, source_type: str, filename: str, number: int) -> dict:
    from app.core.db import SessionLocal

    with SessionLocal() as session:
        doc = session.get(Document, ctx["document_id"])
        kb = session.get(KnowledgeBase, ctx["kb_id"])
        assert doc is not None and kb is not None
        doc.source_type = source_type
        version = DocumentVersion(
            document_id=doc.id,
            knowledge_base_id=kb.id,
            version_number=number,
            storage_path="pending",
            storage_state="incoming",
            byte_size=len(data),
            mime_type="text/plain",
        )
        session.add(version)
        session.flush()
        _store(kb, doc, version, data)
        job = enqueue_ingest(
            session,
            kb=kb,
            document=doc,
            version=version,
            user_id=ctx["user_id"],
            source_type=source_type,
            display_filename=filename,
            original_filename=filename,
        )
        session.commit()
        run_id = doc.pending_run_id
        trace_id = session.scalar(select(ProcessingRun.trace_id).where(ProcessingRun.id == run_id))
        return {"job_id": job.id, "run_id": run_id, "trace_id": trace_id, "version_id": version.id}


class TestPersistence:
    def test_markdown_blocks_persist_in_order_with_locators(self, kb_ctx: dict) -> None:
        data = (FIXTURES / "sample.md").read_bytes()
        prepared = _enqueue(kb_ctx, data, source_type="md", filename="sample.md")
        _execute(prepared["job_id"])
        expected = normalize_blocks(extract_raw_blocks("md", data))
        rows = _blocks(prepared["version_id"])
        assert [(row.sequence, row.block_type, row.text, row.locator, row.block_metadata) for row in rows] == [
            (block.sequence, block.block_type, block.text, block.locator, block.metadata) for block in expected
        ]
        assert [row.sequence for row in rows] == list(range(len(rows)))
        assert all("char_start" not in row.locator for row in rows)

    def test_first_extraction_lifecycle_and_success_trace(self, kb_ctx: dict) -> None:
        from app.core.db import SessionLocal

        data = (FIXTURES / "sample.txt").read_bytes()
        prepared = _enqueue(kb_ctx, data, source_type="txt", filename="sample.txt")
        _execute(prepared["job_id"])

        with SessionLocal() as session:
            doc = session.get(Document, kb_ctx["document_id"])
            run = session.get(ProcessingRun, prepared["run_id"])
            job = session.get(Job, prepared["job_id"])
            trace = session.get(QueryTrace, prepared["trace_id"])
            assert doc is not None and run is not None and job is not None and trace is not None
            assert doc.status == "ready"
            assert doc.preview_version_id is None
            assert doc.current_version_id == prepared["version_id"]
            assert doc.pending_run_id is None
            assert doc.pending_version_id is None
            assert doc.error_code is None
            assert run.status == "ready"
            assert run.is_active is True
            assert job.status == "succeeded"
            assert trace.status == "succeeded"

        tail = [(event.stage, event.status, event.skip_reason) for event in _events(prepared["trace_id"])]
        assert tail[0][0:2] == ("extract", "succeeded")
        assert tail[1][0:2] == ("normalize", "succeeded")
        assert tail[2][:2] == ("chunk", "succeeded")
        assert tail[3:] == list(INDEX_STAGE_TAIL)
        assert all(event.duration_ms == 0 for event in _events(prepared["trace_id"]) if event.status == "skipped")
        assert all(event.duration_ms is not None for event in _events(prepared["trace_id"]) if event.status == "succeeded")

    @pytest.mark.parametrize(
        ("label", "data"),
        [
            ("utf-8-bom", b"\xef\xbb\xbfHello\n\nWorld"),
            ("utf-16-le", b"\xff\xfe" + "Hello\n\nWorld".encode("utf-16-le")),
            ("utf-16-be", b"\xfe\xff" + "Hello\n\nWorld".encode("utf-16-be")),
            ("crlf", b"Hello\r\n\r\nWorld"),
        ],
    )
    def test_textual_decoding_persists_paragraphs(self, kb_ctx: dict, label: str, data: bytes) -> None:
        prepared = _enqueue(kb_ctx, data, source_type="txt", filename=f"{label}.txt")
        _execute(prepared["job_id"])
        rows = _blocks(prepared["version_id"])
        assert [row.text for row in rows] == ["Hello", "World"]
        assert "\r" not in "".join(row.text for row in rows)
        assert rows[1].locator["start_line"] == 3

    @pytest.mark.parametrize(
        ("data", "code"),
        [
            (b"\x80\x81", "invalid_encoding"),
            (b"a\x00b", "nul_in_text"),
            (b" \n\t\n ", "empty_content"),
        ],
    )
    def test_permanent_text_failures_write_no_blocks(self, kb_ctx: dict, data: bytes, code: str) -> None:
        from app.core.db import SessionLocal

        prepared = _enqueue(kb_ctx, data, source_type="txt", filename="bad.txt")
        _execute(prepared["job_id"])
        assert _blocks(prepared["version_id"]) == []
        with SessionLocal() as session:
            doc = session.get(Document, kb_ctx["document_id"])
            run = session.get(ProcessingRun, prepared["run_id"])
            job = session.get(Job, prepared["job_id"])
            assert doc is not None and run is not None and job is not None
            assert job.status == "failed"
            assert job.last_error_code == code
            assert run.status == "failed"
            assert run.error_code == code
            assert doc.status == "failed"
            assert doc.pending_run_id is None
            assert doc.preview_version_id is None
            assert doc.current_version_id is None
        events = _events(prepared["trace_id"])
        failed = next(event for event in events if event.status == "failed")
        assert failed.error_code == code
        later = [event for event in events if event.sequence > failed.sequence]
        assert later
        assert {(event.status, event.skip_reason) for event in later} == {("skipped", "upstream_failed")}
        assert {event.stage for event in events} == set(INGESTION_STAGES)

    def test_publish_failure_leaves_no_partial_rows(self, kb_ctx: dict, monkeypatch: pytest.MonkeyPatch) -> None:
        from sqlalchemy.orm import Session

        real_flush = Session.flush

        def fail_on_blocks(self, objects=None):  # type: ignore[no-untyped-def]
            if any(isinstance(obj, CanonicalBlock) for obj in self.new):
                raise RuntimeError("forced publish failure")
            return real_flush(self, objects)

        monkeypatch.setattr(Session, "flush", fail_on_blocks)
        prepared = _enqueue(kb_ctx, b"Hello\n\nWorld", source_type="txt", filename="partial.txt")
        _execute(prepared["job_id"])
        assert _blocks(prepared["version_id"]) == []
        from app.core.db import SessionLocal

        with SessionLocal() as session:
            doc = session.get(Document, kb_ctx["document_id"])
            assert doc is not None
            assert doc.status == "failed"
            assert doc.preview_version_id is None
            assert doc.pending_run_id is None


class TestReplacementLifecycle:
    def _extract_first(self, kb_ctx: dict) -> dict:
        prepared = _enqueue(kb_ctx, b"Hello\n\nWorld", source_type="txt", filename="first.txt")
        _execute(prepared["job_id"])
        return prepared

    def _mark_serving(self, kb_ctx: dict, prepared: dict) -> None:
        from app.core.db import SessionLocal

        with SessionLocal() as session:
            doc = session.get(Document, kb_ctx["document_id"])
            run = session.get(ProcessingRun, prepared["run_id"])
            assert doc is not None and run is not None
            doc.status = "ready"
            doc.current_version_id = prepared["version_id"]
            run.status = "ready"
            run.is_active = True
            session.commit()

    def test_failed_replacement_preserves_serving_after_first_activation(self, kb_ctx: dict) -> None:
        from app.core.db import SessionLocal

        first = self._extract_first(kb_ctx)
        replacement = _add_version(kb_ctx, b" \n\t\n ", source_type="txt", filename="empty.txt", number=2)
        _execute(replacement["job_id"])
        assert len(_blocks(first["version_id"])) == 2
        assert _blocks(replacement["version_id"]) == []
        with SessionLocal() as session:
            doc = session.get(Document, kb_ctx["document_id"])
            assert doc is not None
            assert doc.status == "ready"
            assert doc.preview_version_id is None
            assert doc.current_version_id == first["version_id"]
            assert doc.pending_run_id is None
            assert doc.error_code is None

    def test_successful_replacement_preserves_serving_version(self, kb_ctx: dict) -> None:
        from app.core.db import SessionLocal

        first = self._extract_first(kb_ctx)
        replacement = _add_version(kb_ctx, b"Replacement\n\nText", source_type="txt", filename="next.txt", number=2)
        _execute(replacement["job_id"])
        assert [row.text for row in _blocks(replacement["version_id"])] == ["Replacement", "Text"]
        with SessionLocal() as session:
            doc = session.get(Document, kb_ctx["document_id"])
            serving = session.get(ProcessingRun, first["run_id"])
            incoming = session.get(ProcessingRun, replacement["run_id"])
            assert doc is not None and serving is not None and incoming is not None
            assert doc.status == "ready"
            assert doc.current_version_id == replacement["version_id"]
            assert doc.preview_version_id is None
            assert doc.pending_run_id is None
            assert serving.is_active is False
            assert serving.status == "ready"
            assert serving.superseded_at is not None
            assert incoming.is_active is True
            assert incoming.status == "ready"

    def test_failed_replacement_preserves_serving_version(self, kb_ctx: dict) -> None:
        from app.core.db import SessionLocal

        first = self._extract_first(kb_ctx)
        replacement = _add_version(kb_ctx, b"\x80\x81", source_type="txt", filename="bad.txt", number=2)
        _execute(replacement["job_id"])
        assert _blocks(replacement["version_id"]) == []
        assert len(_blocks(first["version_id"])) == 2
        with SessionLocal() as session:
            doc = session.get(Document, kb_ctx["document_id"])
            serving = session.get(ProcessingRun, first["run_id"])
            assert doc is not None and serving is not None
            assert doc.status == "ready"
            assert doc.current_version_id == first["version_id"]
            assert doc.preview_version_id is None
            assert doc.pending_run_id is None
            assert serving.is_active is True
            assert serving.status == "ready"

    def test_reclaim_after_publish_does_not_duplicate_blocks(self, kb_ctx: dict) -> None:
        from app.core.db import SessionLocal

        prepared = _enqueue(kb_ctx, b"Hello\n\nWorld", source_type="txt", filename="again.txt")
        _execute(prepared["job_id"])
        with SessionLocal() as session:
            job = session.get(Job, prepared["job_id"])
            assert job is not None
            job.status = "running"
            job.locked_by = "lost-worker"
            job.locked_at = datetime.now(UTC) - timedelta(seconds=2000)
            job.lock_timeout_seconds = 30
            session.commit()
        with SessionLocal() as session:
            claimed = claim_next_job(session, worker_id="reclaim")
            assert claimed is not None
            execute_job(session, claimed)
        rows = _blocks(prepared["version_id"])
        assert [row.text for row in rows] == ["Hello", "World"]
        with SessionLocal() as session:
            doc = session.get(Document, kb_ctx["document_id"])
            job = session.get(Job, prepared["job_id"])
            assert doc is not None and job is not None
            assert doc.status == "ready"
            assert job.status in ("succeeded", "pending")

    def test_stale_run_does_not_change_document_lifecycle(self, kb_ctx: dict, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.core.db import SessionLocal
        import app.worker.handlers.ingest as ingest_module

        prepared = _enqueue(kb_ctx, b"Hello\n\nWorld", source_type="txt", filename="stale.txt")
        original = ingest_module.extract_raw_blocks

        def hijack(source_type: str, data: bytes, *, filename: str | None = None):
            with SessionLocal() as other:
                doc = other.get(Document, kb_ctx["document_id"])
                kb = other.get(KnowledgeBase, kb_ctx["kb_id"])
                assert doc is not None and kb is not None
                other_run = ProcessingRun(
                    document_id=doc.id,
                    document_version_id=prepared["version_id"],
                    knowledge_base_id=kb.id,
                    status="queued",
                    chunk_strategy=kb.chunk_config,
                )
                other.add(other_run)
                other.flush()
                doc.pending_run_id = other_run.id
                doc.pending_version_id = prepared["version_id"]
                other.commit()
                hijack.other_run_id = other_run.id  # type: ignore[attr-defined]
            return original(source_type, data, filename=filename)

        monkeypatch.setattr(ingest_module, "extract_raw_blocks", hijack)
        _execute(prepared["job_id"])
        assert _blocks(prepared["version_id"]) == []
        with SessionLocal() as session:
            doc = session.get(Document, kb_ctx["document_id"])
            run = session.get(ProcessingRun, prepared["run_id"])
            assert doc is not None and run is not None
            assert doc.status == "queued"
            assert doc.preview_version_id is None
            assert doc.current_version_id is None
            assert doc.pending_run_id == hijack.other_run_id  # type: ignore[attr-defined]
            assert run.status == "failed"


def _reprocess(ctx: dict, version_id: uuid.UUID) -> dict:
    from app.core.db import SessionLocal

    with SessionLocal() as session:
        job = request_user_retry_on_version(
            session,
            document_id=ctx["document_id"],
            version_id=version_id,
            user_id=ctx["user_id"],
            allow_extracted_preview=True,
        )
        session.commit()
        run_id = uuid.UUID(str(job.payload["processing_run_id"]))
        trace_id = session.scalar(select(ProcessingRun.trace_id).where(ProcessingRun.id == run_id))
        return {"job_id": job.id, "run_id": run_id, "trace_id": trace_id}


def _block_count(version_id: uuid.UUID) -> int:
    from app.core.db import SessionLocal

    with SessionLocal() as session:
        return session.scalar(
            select(func.count()).select_from(CanonicalBlock).where(CanonicalBlock.document_version_id == version_id)
        ) or 0


class TestReprocessWithExistingBlocks:
    def _ready_serving_is_preview(self, kb_ctx: dict) -> dict:
        prepared = _enqueue(kb_ctx, b"Hello\n\nWorld", source_type="txt", filename="serve.txt")
        _execute(prepared["job_id"])
        return prepared

    def test_reprocess_serving_when_also_preview(self, kb_ctx: dict) -> None:
        from app.core.db import SessionLocal

        first = self._ready_serving_is_preview(kb_ctx)
        before = _block_count(first["version_id"])
        replay = _reprocess(kb_ctx, first["version_id"])
        _execute(replay["job_id"])

        assert _block_count(first["version_id"]) == before
        with SessionLocal() as session:
            doc = session.get(Document, kb_ctx["document_id"])
            run = session.get(ProcessingRun, replay["run_id"])
            job = session.get(Job, replay["job_id"])
            assert doc is not None and run is not None and job is not None
            assert doc.status == "ready"
            assert doc.current_version_id == first["version_id"]
            assert doc.preview_version_id is None
            assert doc.pending_run_id is None
            assert doc.pending_version_id is None
            assert run.status in ("ready", "extracted")
            assert job.status == "succeeded"

        stages = [(e.stage, e.status, e.skip_reason) for e in _events(replay["trace_id"]) if e.stage in ("extract", "normalize")]
        assert stages == [
            ("extract", "skipped", "blocks_present"),
            ("normalize", "skipped", "blocks_present"),
        ]

    def test_reprocess_serving_after_newer_preview_exists(self, kb_ctx: dict) -> None:
        from app.core.db import SessionLocal

        first = self._ready_serving_is_preview(kb_ctx)
        newer = _add_version(kb_ctx, b"Replacement\n\nLine", source_type="txt", filename="v2.txt", number=2)
        _execute(newer["job_id"])
        serving_blocks = _block_count(first["version_id"])

        replay = _reprocess(kb_ctx, first["version_id"])
        _execute(replay["job_id"])

        assert _block_count(first["version_id"]) == serving_blocks
        with SessionLocal() as session:
            doc = session.get(Document, kb_ctx["document_id"])
            run = session.get(ProcessingRun, replay["run_id"])
            assert doc is not None and run is not None
            assert doc.status == "ready"
            assert doc.current_version_id in (first["version_id"], newer["version_id"])
            assert doc.preview_version_id is None
            assert doc.pending_run_id is None
            assert run.status in ("ready", "failed", "extracted")

    def _reprocess_interrupt_before_job_success(
        self, kb_ctx: dict, replay: dict, monkeypatch: pytest.MonkeyPatch
    ) -> list[TraceEvent]:
        import app.worker.dispatch as dispatch_module
        from app.worker.claim import mark_job_succeeded

        saw_handler = {"count": 0}
        original_mark = mark_job_succeeded

        def handler_complete_mark(db, job: Job) -> None:
            if job.id == replay["job_id"]:
                saw_handler["count"] += 1
                if saw_handler["count"] == 1:
                    job.status = "running"
                    job.locked_by = "lost-worker"
                    job.locked_at = datetime.now(UTC) - timedelta(seconds=2000)
                    job.lock_timeout_seconds = 30
                    db.commit()
                    return
            original_mark(db, job)

        monkeypatch.setattr(dispatch_module, "mark_job_succeeded", handler_complete_mark)

        from app.core.db import SessionLocal

        with SessionLocal() as session:
            job = session.get(Job, replay["job_id"])
            assert job is not None
            execute_job(session, job)

        trace_before = _events(replay["trace_id"])

        with SessionLocal() as session:
            claimed = claim_next_job(session, worker_id="reclaim-after-interrupt")
            assert claimed is not None
            assert claimed.id == replay["job_id"]
            execute_job(session, claimed)

        return trace_before

    def test_reprocess_reclaim_after_finalize_with_newer_preview(self, kb_ctx: dict, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.core.db import SessionLocal

        first = self._ready_serving_is_preview(kb_ctx)
        newer = _add_version(kb_ctx, b"Replacement\n\nLine", source_type="txt", filename="v2.txt", number=2)
        _execute(newer["job_id"])
        serving_blocks = _block_count(first["version_id"])
        replay = _reprocess(kb_ctx, first["version_id"])

        trace_before = self._reprocess_interrupt_before_job_success(kb_ctx, replay, monkeypatch)

        assert _block_count(first["version_id"]) == serving_blocks
        with SessionLocal() as session:
            doc = session.get(Document, kb_ctx["document_id"])
            run = session.get(ProcessingRun, replay["run_id"])
            job = session.get(Job, replay["job_id"])
            trace = session.get(QueryTrace, replay["trace_id"])
            assert doc is not None and run is not None and job is not None and trace is not None
            assert doc.status == "ready"
            assert doc.current_version_id in (first["version_id"], newer["version_id"])
            assert doc.preview_version_id is None
            assert doc.pending_run_id is None
            assert job.status in ("succeeded", "failed", "pending")
            assert trace.status in ("succeeded", "failed", "running")

        trace_after = _events(replay["trace_id"])
        assert trace_after

    def test_reprocess_reclaim_after_finalize_when_serving_is_preview(
        self, kb_ctx: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core.db import SessionLocal

        first = self._ready_serving_is_preview(kb_ctx)
        before = _block_count(first["version_id"])
        replay = _reprocess(kb_ctx, first["version_id"])

        trace_before = self._reprocess_interrupt_before_job_success(kb_ctx, replay, monkeypatch)

        assert _block_count(first["version_id"]) == before
        with SessionLocal() as session:
            doc = session.get(Document, kb_ctx["document_id"])
            run = session.get(ProcessingRun, replay["run_id"])
            job = session.get(Job, replay["job_id"])
            trace = session.get(QueryTrace, replay["trace_id"])
            assert doc is not None and run is not None and job is not None and trace is not None
            assert doc.status == "ready"
            assert doc.current_version_id == first["version_id"]
            assert doc.preview_version_id is None
            assert doc.pending_run_id is None
            assert job.status in ("succeeded", "failed", "pending")
            assert trace.status in ("succeeded", "failed", "running")

        trace_after = _events(replay["trace_id"])
        assert trace_after

    def test_ready_document_inconsistent_blocks_preserves_serving(self, kb_ctx: dict) -> None:
        from app.core.db import SessionLocal

        first = self._ready_serving_is_preview(kb_ctx)
        newer = _add_version(kb_ctx, b"Replacement\n\nLine", source_type="txt", filename="v2.txt", number=2)
        _execute(newer["job_id"])

        with SessionLocal() as session:
            session.query(CanonicalBlock).filter(CanonicalBlock.document_version_id == first["version_id"]).delete()
            session.execute(
                insert(CanonicalBlock).values(
                    document_version_id=first["version_id"],
                    sequence=0,
                    block_type="paragraph",
                    text="Only",
                    locator={"source_type": "txt", "start_line": 1, "end_line": 1},
                    block_metadata={},
                )
            )
            session.execute(
                insert(CanonicalBlock).values(
                    document_version_id=first["version_id"],
                    sequence=2,
                    block_type="paragraph",
                    text="Gap",
                    locator={"source_type": "txt", "start_line": 3, "end_line": 3},
                    block_metadata={},
                )
            )
            session.commit()

        replay = _reprocess(kb_ctx, first["version_id"])
        _execute(replay["job_id"])

        with SessionLocal() as session:
            doc = session.get(Document, kb_ctx["document_id"])
            run = session.get(ProcessingRun, replay["run_id"])
            job = session.get(Job, replay["job_id"])
            assert doc is not None and run is not None and job is not None
            assert job.last_error_code == "inconsistent_blocks"
            assert run.error_code == "inconsistent_blocks"
            assert doc.status == "ready"
            assert doc.current_version_id == newer["version_id"]
            assert doc.preview_version_id is None
            assert doc.pending_run_id is None

    def test_inconsistent_existing_blocks_fail_permanently(self, kb_ctx: dict) -> None:
        from app.core.db import SessionLocal

        prepared = _enqueue(kb_ctx, b"Hello\n\nWorld", source_type="txt", filename="broken.txt")
        with SessionLocal() as session:
            session.execute(
                insert(CanonicalBlock).values(
                    document_version_id=prepared["version_id"],
                    sequence=0,
                    block_type="paragraph",
                    text="Hello",
                    locator={"source_type": "txt", "start_line": 1, "end_line": 1},
                    block_metadata={},
                )
            )
            session.execute(
                insert(CanonicalBlock).values(
                    document_version_id=prepared["version_id"],
                    sequence=2,
                    block_type="paragraph",
                    text="World",
                    locator={"source_type": "txt", "start_line": 3, "end_line": 3},
                    block_metadata={},
                )
            )
            session.commit()

        _execute(prepared["job_id"])
        assert _block_count(prepared["version_id"]) == 2

        with SessionLocal() as session:
            doc = session.get(Document, kb_ctx["document_id"])
            run = session.get(ProcessingRun, prepared["run_id"])
            job = session.get(Job, prepared["job_id"])
            assert doc is not None and run is not None and job is not None
            assert job.last_error_code == "inconsistent_blocks"
            assert run.error_code == "inconsistent_blocks"
            assert doc.pending_run_id is None
            assert doc.preview_version_id is None
            assert doc.current_version_id is None

    def test_post_extraction_replay_race_trace(self, kb_ctx: dict, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.core.db import SessionLocal
        import app.worker.handlers.ingest as ingest_module

        prepared = _enqueue(kb_ctx, b"Hello\n\nWorld", source_type="txt", filename="race.txt")
        original_extract = ingest_module.extract_raw_blocks
        original_complete = ingest_module.blocks_complete
        race = {"past_extract": False}

        def gated_complete(db, version_id):  # type: ignore[no-untyped-def]
            if not race["past_extract"]:
                return False
            return original_complete(db, version_id)

        def publish_during_extract(source_type: str, data: bytes, *, filename: str | None = None):
            raw_blocks = original_extract(source_type, data, filename=filename)
            normalized = normalize_blocks(raw_blocks)
            with SessionLocal() as session:
                for block in normalized:
                    session.add(
                        CanonicalBlock(
                            document_version_id=prepared["version_id"],
                            sequence=block.sequence,
                            block_type=block.block_type,
                            text=block.text,
                            locator=block.locator,
                            block_metadata=block.metadata,
                        )
                    )
                session.commit()
            race["past_extract"] = True
            return raw_blocks

        monkeypatch.setattr(ingest_module, "blocks_complete", gated_complete)
        monkeypatch.setattr(ingest_module, "extract_raw_blocks", publish_during_extract)
        _execute(prepared["job_id"])

        events = _events(prepared["trace_id"])
        extract = next(event for event in events if event.stage == "extract")
        normalize = next(event for event in events if event.stage == "normalize")
        assert extract.status == "succeeded"
        assert extract.skip_reason is None
        assert normalize.status == "skipped"
        assert normalize.skip_reason == "blocks_present"
        assert _block_count(prepared["version_id"]) == 2


def _auth(client, email: str) -> str:
    client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "a-long-password", "display_name": email},
    )
    return client.post("/api/v1/auth/login", json={"email": email, "password": "a-long-password"}).json()["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class TestBlockReadApi:
    def test_authorized_pagination(self, client) -> None:
        from app.core.db import SessionLocal

        owner_email = f"{uuid.uuid4().hex}@example.com"
        owner = _auth(client, owner_email)
        created = client.post(
            "/api/v1/knowledge-bases",
            headers=_headers(owner),
            json={"name": "Blocks", "description": ""},
        )
        kb_id = created.json()["id"]
        uploaded = client.post(
            f"/api/v1/knowledge-bases/{kb_id}/documents",
            headers=_headers(owner),
            files={"file": ("notes.txt", b"One\n\nTwo\n\nThree\n", "text/plain")},
            data={"force": "false"},
        )
        assert uploaded.status_code == 201
        body = uploaded.json()
        version_id = body["document"]["pending_version_id"]
        _execute(uuid.UUID(body["job_id"]))

        viewer_email = f"{uuid.uuid4().hex}@example.com"
        viewer = _auth(client, viewer_email)
        member = client.put(
            f"/api/v1/knowledge-bases/{kb_id}/members",
            headers=_headers(owner),
            json={"email": viewer_email, "role": "viewer"},
        )
        assert member.status_code == 200
        outsider = _auth(client, f"{uuid.uuid4().hex}@example.com")

        denied = client.get(f"/api/v1/document-versions/{version_id}/blocks", headers=_headers(outsider))
        assert denied.status_code == 404

        first = client.get(
            f"/api/v1/document-versions/{version_id}/blocks?limit=2",
            headers=_headers(viewer),
        )
        assert first.status_code == 200
        page = first.json()
        assert [item["sequence"] for item in page["items"]] == [0, 1]
        assert [item["text"] for item in page["items"]] == ["One", "Two"]
        assert page["items"][0]["locator"]["source_type"] == "txt"
        assert page["items"][0]["metadata"] == {}
        assert set(page["items"][0]) == {"id", "sequence", "block_type", "text", "locator", "metadata"}
        assert page["next_cursor"]

        second = client.get(
            f"/api/v1/document-versions/{version_id}/blocks?limit=2&cursor={page['next_cursor']}",
            headers=_headers(owner),
        )
        assert second.status_code == 200
        assert [item["sequence"] for item in second.json()["items"]] == [2]
        assert second.json()["next_cursor"] is None

        invalid = client.get(
            f"/api/v1/document-versions/{version_id}/blocks?cursor=not-a-cursor",
            headers=_headers(owner),
        )
        assert invalid.status_code == 400

        missing = client.get(
            f"/api/v1/document-versions/{uuid.uuid4()}/blocks",
            headers=_headers(owner),
        )
        assert missing.status_code == 404
        with SessionLocal() as session:
            count = session.scalar(
                select(func.count()).select_from(CanonicalBlock).where(CanonicalBlock.document_version_id == uuid.UUID(version_id))
            )
        assert count == 3
