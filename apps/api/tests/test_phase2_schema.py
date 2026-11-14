"""PostgreSQL schema tests for Phase 2 Milestone 1."""

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

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
    User,
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


def _insert_document(
    session: Session,
    *,
    kb_id: uuid.UUID,
    user_id: uuid.UUID,
    status: str = "queued",
    current_version_id: uuid.UUID | None = None,
    preview_version_id: uuid.UUID | None = None,
) -> Document:
    doc = Document(
        id=uuid.uuid4(),
        knowledge_base_id=kb_id,
        title="sample.pdf",
        source_type="pdf",
        status=status,
        created_by=user_id,
        current_version_id=current_version_id,
        preview_version_id=preview_version_id,
    )
    session.add(doc)
    session.flush()
    return doc


def _insert_version(
    session: Session,
    *,
    document: Document,
    version_number: int = 1,
    content_hash: str | None = "abc123",
    dedup_hash: str | None = "abc123",
) -> DocumentVersion:
    version = DocumentVersion(
        id=uuid.uuid4(),
        document_id=document.id,
        knowledge_base_id=document.knowledge_base_id,
        version_number=version_number,
        content_hash=content_hash,
        dedup_hash=dedup_hash,
        storage_path=f"incoming/{uuid.uuid4()}",
        storage_state="stored",
        byte_size=12,
        mime_type="application/pdf",
    )
    session.add(version)
    session.flush()
    return version


class TestVersionOwnership:
    def test_composite_fk_rejects_version_from_another_document(self, db_session: Session, kb_context: dict) -> None:
        doc_a = _insert_document(db_session, kb_id=kb_context["kb_id"], user_id=kb_context["user_id"])
        doc_b = _insert_document(db_session, kb_id=kb_context["kb_id"], user_id=kb_context["user_id"])
        version_b = _insert_version(db_session, document=doc_b)
        db_session.commit()

        doc_a.preview_version_id = version_b.id
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_processing_run_version_must_belong_to_document(self, db_session: Session, kb_context: dict) -> None:
        doc_a = _insert_document(db_session, kb_id=kb_context["kb_id"], user_id=kb_context["user_id"])
        doc_b = _insert_document(db_session, kb_id=kb_context["kb_id"], user_id=kb_context["user_id"])
        version_a = _insert_version(db_session, document=doc_a)
        db_session.commit()

        run = ProcessingRun(
            id=uuid.uuid4(),
            document_id=doc_b.id,
            document_version_id=version_a.id,
            knowledge_base_id=kb_context["kb_id"],
            status="queued",
            chunk_strategy=DEFAULT_CHUNK_CONFIG,
        )
        db_session.add(run)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()


class TestUniqueVersionNumbers:
    def test_duplicate_version_number_rejected(self, db_session: Session, kb_context: dict) -> None:
        doc = _insert_document(db_session, kb_id=kb_context["kb_id"], user_id=kb_context["user_id"])
        _insert_version(db_session, document=doc, version_number=1)
        with pytest.raises(IntegrityError):
            _insert_version(db_session, document=doc, version_number=1)
            db_session.commit()
        db_session.rollback()


class TestDeduplication:
    def test_same_content_hash_on_one_document_rejected(self, db_session: Session, kb_context: dict) -> None:
        doc = _insert_document(db_session, kb_id=kb_context["kb_id"], user_id=kb_context["user_id"])
        _insert_version(db_session, document=doc, version_number=1, content_hash="hash-a", dedup_hash="hash-a")
        with pytest.raises(IntegrityError):
            _insert_version(db_session, document=doc, version_number=2, content_hash="hash-a", dedup_hash="hash-a")
            db_session.commit()
        db_session.rollback()

    def test_kb_dedup_hash_unique_for_uploads(self, db_session: Session, kb_context: dict) -> None:
        doc_a = _insert_document(db_session, kb_id=kb_context["kb_id"], user_id=kb_context["user_id"])
        doc_b = _insert_document(db_session, kb_id=kb_context["kb_id"], user_id=kb_context["user_id"])
        _insert_version(db_session, document=doc_a, content_hash="dup", dedup_hash="dup")
        with pytest.raises(IntegrityError):
            _insert_version(db_session, document=doc_b, content_hash="dup", dedup_hash="dup")
            db_session.commit()
        db_session.rollback()

    def test_url_versions_may_share_content_without_dedup_hash(self, db_session: Session, kb_context: dict) -> None:
        doc_a = Document(
            id=uuid.uuid4(),
            knowledge_base_id=kb_context["kb_id"],
            title="a",
            source_type="web",
            status="queued",
            created_by=kb_context["user_id"],
        )
        doc_b = Document(
            id=uuid.uuid4(),
            knowledge_base_id=kb_context["kb_id"],
            title="b",
            source_type="web",
            status="queued",
            created_by=kb_context["user_id"],
        )
        db_session.add_all([doc_a, doc_b])
        db_session.flush()
        _insert_version(db_session, document=doc_a, content_hash="same-body", dedup_hash=None)
        _insert_version(db_session, document=doc_b, content_hash="same-body", dedup_hash=None)
        db_session.commit()


class TestPointerConstraints:
    def test_preview_pointer_requires_matching_document(self, db_session: Session, kb_context: dict) -> None:
        doc = _insert_document(db_session, kb_id=kb_context["kb_id"], user_id=kb_context["user_id"])
        version = _insert_version(db_session, document=doc)
        doc.preview_version_id = version.id
        doc.status = "extracted"
        db_session.commit()
        assert doc.preview_version_id == version.id

    def test_extracted_status_requires_preview_and_no_current(self, db_session: Session, kb_context: dict) -> None:
        with pytest.raises(IntegrityError):
            _insert_document(
                db_session,
                kb_id=kb_context["kb_id"],
                user_id=kb_context["user_id"],
                status="extracted",
            )
        db_session.rollback()

    def test_ready_with_serving_version_allows_preview_ahead(
        self, db_session: Session, kb_context: dict
    ) -> None:
        doc = _insert_document(db_session, kb_id=kb_context["kb_id"], user_id=kb_context["user_id"])
        v1 = _insert_version(
            db_session,
            document=doc,
            version_number=1,
            content_hash="hash-v1",
            dedup_hash="hash-v1",
        )
        v2 = _insert_version(
            db_session,
            document=doc,
            version_number=2,
            content_hash="hash-v2",
            dedup_hash=None,
        )
        doc.current_version_id = v1.id
        doc.preview_version_id = v2.id
        doc.status = "ready"
        db_session.commit()
        assert doc.status == "ready"
        assert doc.current_version_id == v1.id
        assert doc.preview_version_id == v2.id


class TestPendingPointers:
    def test_pending_version_without_run_rejected(self, db_session: Session, kb_context: dict) -> None:
        doc = _insert_document(db_session, kb_id=kb_context["kb_id"], user_id=kb_context["user_id"])
        version = _insert_version(db_session, document=doc)
        doc.pending_version_id = version.id
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()


class TestRetryCounters:
    def test_user_retry_count_cannot_exceed_limit(self, db_session: Session, kb_context: dict) -> None:
        doc = _insert_document(db_session, kb_id=kb_context["kb_id"], user_id=kb_context["user_id"])
        version = _insert_version(db_session, document=doc)
        db_session.commit()
        run = ProcessingRun(
            id=uuid.uuid4(),
            document_id=doc.id,
            document_version_id=version.id,
            knowledge_base_id=kb_context["kb_id"],
            status="failed",
            chunk_strategy=DEFAULT_CHUNK_CONFIG,
            user_retry_count=4,
            user_retry_limit=3,
        )
        db_session.add(run)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()


class TestDocumentLifecycle:
    def test_only_one_active_run_per_document(self, db_session: Session, kb_context: dict) -> None:
        doc = _insert_document(db_session, kb_id=kb_context["kb_id"], user_id=kb_context["user_id"])
        version = _insert_version(db_session, document=doc)
        doc.current_version_id = version.id
        doc.preview_version_id = version.id
        doc.status = "ready"
        db_session.commit()
        run_a = ProcessingRun(
            id=uuid.uuid4(),
            document_id=doc.id,
            document_version_id=version.id,
            knowledge_base_id=kb_context["kb_id"],
            status="ready",
            is_active=True,
            chunk_strategy=DEFAULT_CHUNK_CONFIG,
        )
        run_b = ProcessingRun(
            id=uuid.uuid4(),
            document_id=doc.id,
            document_version_id=version.id,
            knowledge_base_id=kb_context["kb_id"],
            status="ready",
            is_active=True,
            chunk_strategy=DEFAULT_CHUNK_CONFIG,
        )
        db_session.add(run_a)
        db_session.commit()
        db_session.add(run_b)
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_inconsistent_serving_run_diagnostics_persisted(
        self, db_session: Session, kb_context: dict
    ) -> None:
        """Approved Phase 2 acceptance: inactive serving run blocks block publish."""
        doc_id = uuid.uuid4()
        v1_id = uuid.uuid4()
        v2_id = uuid.uuid4()
        serving_run_id = uuid.uuid4()
        pending_run_id = uuid.uuid4()
        job_id = uuid.uuid4()
        trace_id = uuid.uuid4()

        db_session.add(
            Document(
                id=doc_id,
                knowledge_base_id=kb_context["kb_id"],
                title="serving.pdf",
                source_type="pdf",
                status="ready",
                current_version_id=v1_id,
                preview_version_id=v1_id,
                created_by=kb_context["user_id"],
            )
        )
        db_session.flush()
        db_session.add_all(
            [
                DocumentVersion(
                    id=v1_id,
                    document_id=doc_id,
                    knowledge_base_id=kb_context["kb_id"],
                    version_number=1,
                    content_hash="v1",
                    dedup_hash="v1",
                    storage_path="kb/doc/v1",
                    storage_state="stored",
                    byte_size=1,
                    mime_type="application/pdf",
                ),
                DocumentVersion(
                    id=v2_id,
                    document_id=doc_id,
                    knowledge_base_id=kb_context["kb_id"],
                    version_number=2,
                    content_hash="v2",
                    dedup_hash=None,
                    storage_path="kb/doc/v2",
                    storage_state="stored",
                    byte_size=1,
                    mime_type="application/pdf",
                ),
            ]
        )
        db_session.flush()
        db_session.add(
            QueryTrace(
                id=trace_id,
                knowledge_base_id=kb_context["kb_id"],
                kind="ingestion",
                status="running",
                config={"source_type": "pdf"},
            )
        )
        db_session.flush()
        db_session.add(
            ProcessingRun(
                id=serving_run_id,
                document_id=doc_id,
                document_version_id=v1_id,
                knowledge_base_id=kb_context["kb_id"],
                status="ready",
                is_active=False,
                chunk_strategy=DEFAULT_CHUNK_CONFIG,
                trace_id=trace_id,
            )
        )
        db_session.commit()

        diagnostic = {
            "current_version_id": str(v1_id),
            "preview_version_id": str(v1_id),
            "document_status": "ready",
            "serving_run_id": str(serving_run_id),
            "serving_run_status": "ready",
            "serving_run_is_active": False,
            "rejected_version_id": str(v2_id),
            "rejected_run_id": str(pending_run_id),
        }

        db_session.execute(
            text(
                """
                UPDATE documents SET
                  pending_version_id = NULL,
                  pending_run_id = NULL
                WHERE id = :doc_id
                """
            ),
            {"doc_id": doc_id},
        )
        db_session.add(
            ProcessingRun(
                id=pending_run_id,
                document_id=doc_id,
                document_version_id=v2_id,
                knowledge_base_id=kb_context["kb_id"],
                status="failed",
                is_active=False,
                chunk_strategy=DEFAULT_CHUNK_CONFIG,
                error_code="inconsistent_state",
                error_detail=str(diagnostic),
            )
        )
        db_session.add(
            Job(
                id=job_id,
                job_type="ingest_document",
                idempotency_key=f"processing_run:{pending_run_id}",
                knowledge_base_id=kb_context["kb_id"],
                payload={"processing_run_id": str(pending_run_id)},
                status="failed",
                last_error_code="inconsistent_state",
                last_error=str(diagnostic),
            )
        )
        db_session.commit()

        block_count = db_session.execute(
            text("SELECT COUNT(*) FROM canonical_blocks WHERE document_version_id = :vid"),
            {"vid": v2_id},
        ).scalar()
        assert block_count == 0

        doc = db_session.get(Document, doc_id)
        assert doc is not None
        assert doc.current_version_id == v1_id
        assert doc.preview_version_id == v1_id
        assert doc.status == "ready"
        assert doc.pending_version_id is None
        assert doc.pending_run_id is None

        serving = db_session.get(ProcessingRun, serving_run_id)
        assert serving is not None
        assert serving.is_active is False
        assert serving.status == "ready"


class TestJobsAndTraces:
    def test_live_job_idempotency_unique(self, db_session: Session, kb_context: dict) -> None:
        key = f"processing_run:{uuid.uuid4()}"
        db_session.add(
            Job(
                id=uuid.uuid4(),
                job_type="ingest_document",
                idempotency_key=key,
                knowledge_base_id=kb_context["kb_id"],
                payload={},
                status="pending",
            )
        )
        db_session.commit()
        db_session.add(
            Job(
                id=uuid.uuid4(),
                job_type="ingest_document",
                idempotency_key=key,
                knowledge_base_id=kb_context["kb_id"],
                payload={},
                status="running",
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_canonical_block_sequence_unique(self, db_session: Session, kb_context: dict) -> None:
        doc = _insert_document(db_session, kb_id=kb_context["kb_id"], user_id=kb_context["user_id"])
        version = _insert_version(db_session, document=doc)
        db_session.add(
            CanonicalBlock(
                id=uuid.uuid4(),
                document_version_id=version.id,
                sequence=0,
                block_type="paragraph",
                text="hello",
                locator={"source_type": "txt", "char_start": 0, "char_end": 5},
            )
        )
        db_session.commit()
        db_session.add(
            CanonicalBlock(
                id=uuid.uuid4(),
                document_version_id=version.id,
                sequence=0,
                block_type="paragraph",
                text="duplicate",
                locator={"source_type": "txt", "char_start": 0, "char_end": 9},
            )
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()
