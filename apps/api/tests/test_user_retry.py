"""Milestone 2B: user retry, reprocess, and force version selection."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.domain.configs import DEFAULT_CHUNK_CONFIG
from app.models import Document, DocumentVersion, Job, KnowledgeBase, KnowledgeBaseMember, ProcessingRun, User
from app.modules.ingestion.user_retry import USER_RETRY_LIMIT, request_user_retry_on_version


def _password_hash() -> str:
    from argon2 import PasswordHasher

    return PasswordHasher().hash("password-long-enough")


def _auth(client, email: str) -> str:
    client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "a-long-password", "display_name": email},
    )
    return client.post("/api/v1/auth/login", json={"email": email, "password": "a-long-password"}).json()["access_token"]


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _kb(client, token: str) -> str:
    created = client.post(
        "/api/v1/knowledge-bases",
        headers=_headers(token),
        json={"name": "Library", "description": ""},
    )
    return created.json()["id"]


def _minimal_pdf() -> bytes:
    return b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"


@pytest.fixture(autouse=True)
def isolated_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil
    import tempfile

    path = tempfile.mkdtemp(prefix="ragflow-user-retry-")
    monkeypatch.setenv("STORAGE_DIR", path)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    shutil.rmtree(path, ignore_errors=True)


def _upload(client, token: str, kb_id: str, data: bytes):
    return client.post(
        f"/api/v1/knowledge-bases/{kb_id}/documents",
        headers=_headers(token),
        files={"file": ("sample.pdf", data, "application/pdf")},
        data={"force": "false"},
    )


@pytest.fixture
def kb_ctx():
    from app.core.db import SessionLocal

    with SessionLocal() as session:
        user = User(email=f"{uuid.uuid4().hex}@example.com", password_hash=_password_hash(), display_name="U")
        session.add(user)
        session.flush()
        kb = KnowledgeBase(
            owner_id=user.id,
            name="Retry KB",
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
            content_hash="hash-v1",
            dedup_hash="hash-v1",
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


class TestUserRetryRules:
    def test_retry_failed_version_increments_user_retry_count(self, kb_ctx: dict) -> None:
        from app.core.db import SessionLocal
        from app.modules.ingestion.enqueue import enqueue_ingest

        with SessionLocal() as session:
            doc = session.get(Document, kb_ctx["document_id"])
            kb = session.get(KnowledgeBase, kb_ctx["kb_id"])
            version = session.get(DocumentVersion, kb_ctx["version_id"])
            assert doc and kb and version
            run = ProcessingRun(
                document_id=doc.id,
                document_version_id=version.id,
                knowledge_base_id=kb.id,
                status="failed",
                chunk_strategy=kb.chunk_config,
                error_code="extract_failed",
                user_retry_count=0,
                user_retry_limit=USER_RETRY_LIMIT,
            )
            session.add(run)
            session.flush()
            doc.pending_run_id = None
            doc.pending_version_id = None
            session.commit()

        with SessionLocal() as session:
            job = request_user_retry_on_version(
                session,
                document_id=kb_ctx["document_id"],
                version_id=kb_ctx["version_id"],
                user_id=kb_ctx["user_id"],
            )
            run_id = uuid.UUID(str(job.payload["processing_run_id"]))
            run = session.get(ProcessingRun, run_id)
            assert run is not None
            assert run.user_retry_count == 1
            assert run.document_version_id == kb_ctx["version_id"]

    def test_retry_blocked_while_pending(self, kb_ctx: dict) -> None:
        from app.core.db import SessionLocal
        from app.core.errors import APIError
        from app.modules.ingestion.enqueue import enqueue_ingest

        with SessionLocal() as session:
            doc = session.get(Document, kb_ctx["document_id"])
            kb = session.get(KnowledgeBase, kb_ctx["kb_id"])
            version = session.get(DocumentVersion, kb_ctx["version_id"])
            assert doc and kb and version
            enqueue_ingest(
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

        with SessionLocal() as session:
            with pytest.raises(APIError) as exc:
                request_user_retry_on_version(
                    session,
                    document_id=kb_ctx["document_id"],
                    version_id=kb_ctx["version_id"],
                    user_id=kb_ctx["user_id"],
                )
            assert exc.value.status_code == 409
            assert exc.value.code == "ingest_in_progress"

    def test_retry_rejected_when_version_already_extracted_preview(self, kb_ctx: dict) -> None:
        from app.core.db import SessionLocal
        from app.core.errors import APIError

        with SessionLocal() as session:
            doc = session.get(Document, kb_ctx["document_id"])
            assert doc is not None
            doc.status = "ready"
            doc.current_version_id = kb_ctx["version_id"]
            doc.preview_version_id = kb_ctx["version_id"]
            run = ProcessingRun(
                document_id=doc.id,
                document_version_id=kb_ctx["version_id"],
                knowledge_base_id=kb_ctx["kb_id"],
                status="extracted",
                chunk_strategy=DEFAULT_CHUNK_CONFIG,
            )
            session.add(run)
            session.commit()

        with SessionLocal() as session:
            with pytest.raises(APIError) as exc:
                request_user_retry_on_version(
                    session,
                    document_id=kb_ctx["document_id"],
                    version_id=kb_ctx["version_id"],
                    user_id=kb_ctx["user_id"],
                )
            assert exc.value.status_code == 422
            assert exc.value.code == "unprocessable"

    def test_user_retry_count_scoped_per_version(self, kb_ctx: dict) -> None:
        from app.core.db import SessionLocal

        with SessionLocal() as session:
            doc = session.get(Document, kb_ctx["document_id"])
            assert doc is not None
            other = DocumentVersion(
                document_id=doc.id,
                knowledge_base_id=kb_ctx["kb_id"],
                version_number=2,
                storage_path=f"{kb_ctx['kb_id']}/{doc.id}/{uuid.uuid4()}",
                storage_state="stored",
                byte_size=10,
                mime_type="application/pdf",
                content_hash="hash-v2",
                dedup_hash="hash-v2",
            )
            session.add(other)
            session.flush()
            session.add(
                ProcessingRun(
                    document_id=doc.id,
                    document_version_id=kb_ctx["version_id"],
                    knowledge_base_id=kb_ctx["kb_id"],
                    status="failed",
                    chunk_strategy=DEFAULT_CHUNK_CONFIG,
                    user_retry_count=3,
                    user_retry_limit=USER_RETRY_LIMIT,
                )
            )
            session.commit()
            other_id = other.id

        with SessionLocal() as session:
            job = request_user_retry_on_version(
                session,
                document_id=kb_ctx["document_id"],
                version_id=other_id,
                user_id=kb_ctx["user_id"],
            )
            run_id = uuid.UUID(str(job.payload["processing_run_id"]))
            run = session.get(ProcessingRun, run_id)
            assert run is not None
            assert run.user_retry_count == 1


class TestReprocessHttp:
    def test_reprocess_targets_serving_version(self, client) -> None:
        from app.core.db import SessionLocal

        token = _auth(client, "reprocess-serving@example.com")
        kb_id = _kb(client, token)
        data = _minimal_pdf()
        created = _upload(client, token, kb_id, data)
        doc_id = uuid.UUID(created.json()["document"]["id"])
        with SessionLocal() as session:
            doc = session.get(Document, doc_id)
            assert doc is not None
            version = session.get(DocumentVersion, doc.pending_version_id)
            assert version is not None
            run = session.get(ProcessingRun, doc.pending_run_id)
            assert run is not None
            doc.status = "ready"
            doc.current_version_id = version.id
            doc.preview_version_id = version.id
            doc.pending_version_id = None
            doc.pending_run_id = None
            run.status = "extracted"
            session.commit()
            serving_version_id = doc.current_version_id

        response = client.post(f"/api/v1/documents/{doc_id}/reprocess", headers=_headers(token))
        assert response.status_code == 202
        body = response.json()
        with SessionLocal() as session:
            run = session.get(ProcessingRun, uuid.UUID(body["processing_run_id"]))
            doc = session.get(Document, doc_id)
            assert run is not None and doc is not None
            assert run.document_version_id == serving_version_id
            assert doc.current_version_id == serving_version_id
            assert doc.status == "ready"
            assert doc.pending_run_id == run.id

    def test_reprocess_returns_409_when_pending(self, client) -> None:
        from app.core.db import SessionLocal

        token = _auth(client, "reprocess-pending@example.com")
        kb_id = _kb(client, token)
        created = _upload(client, token, kb_id, _minimal_pdf())
        doc_id = uuid.UUID(created.json()["document"]["id"])
        with SessionLocal() as session:
            doc = session.get(Document, doc_id)
            assert doc is not None
            version = session.get(DocumentVersion, doc.pending_version_id)
            assert version is not None
            doc.status = "ready"
            doc.current_version_id = version.id
            doc.preview_version_id = version.id
            session.commit()
        response = client.post(f"/api/v1/documents/{doc_id}/reprocess", headers=_headers(token))
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "ingest_in_progress"

    def test_force_retries_failed_replacement_not_serving_when_hashes_differ(self, client) -> None:
        from app.core.db import SessionLocal

        token = _auth(client, "force-v2@example.com")
        kb_id = _kb(client, token)
        v1 = _minimal_pdf()
        created = _upload(client, token, kb_id, v1)
        doc_id = uuid.UUID(created.json()["document"]["id"])
        with SessionLocal() as session:
            doc = session.get(Document, doc_id)
            assert doc is not None
            doc.pending_version_id = None
            doc.pending_run_id = None
            session.commit()

        v2 = v1 + b"\n% v2 marker"
        replacement = client.post(
            f"/api/v1/documents/{doc_id}/versions",
            headers=_headers(token),
            files={"file": ("sample.pdf", v2, "application/pdf")},
            data={"force": "false"},
        )
        assert replacement.status_code == 201
        pending_version_id = uuid.UUID(replacement.json()["document"]["pending_version_id"])

        with SessionLocal() as session:
            doc = session.get(Document, doc_id)
            run = session.get(ProcessingRun, doc.pending_run_id)
            assert doc and run
            run.status = "failed"
            run.error_code = "extract_failed"
            doc.pending_version_id = None
            doc.pending_run_id = None
            session.commit()

        forced = client.post(
            f"/api/v1/documents/{doc_id}/versions",
            headers=_headers(token),
            files={"file": ("sample.pdf", v2, "application/pdf")},
            data={"force": "true"},
        )
        assert forced.status_code == 200
        job_id = uuid.UUID(forced.json()["job_id"])
        with SessionLocal() as session:
            job = session.get(Job, job_id)
            assert job is not None
            assert uuid.UUID(job.payload["document_version_id"]) == pending_version_id
