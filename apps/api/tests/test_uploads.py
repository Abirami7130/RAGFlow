"""Milestone 2A: upload handling, storage, dedup, and recovery tests."""

from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import Document, DocumentVersion, Job, ProcessingRun
from app.modules.ingestion import storage as blob_storage
from app.domain.configs import DEFAULT_CHUNK_CONFIG
from app.modules.ingestion.recovery import (
    cleanup_orphan_final_paths,
    cleanup_orphan_incoming,
    recover_unenqueued_ingest,
    recover_version_storage,
    run_storage_recovery,
)
from app.modules.ingestion.upload_phases import commit_incoming_records, promote_version_bytes
from app.modules.ingestion.validation import validate_upload_filename
from app.modules.ingestion.service import upload_new_document, upload_replacement_version


@pytest.fixture(autouse=True)
def isolated_storage(monkeypatch: pytest.MonkeyPatch) -> str:
    path = tempfile.mkdtemp(prefix="ragflow-upload-")
    monkeypatch.setenv("STORAGE_DIR", path)
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()
    shutil.rmtree(path, ignore_errors=True)


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


def _upload(client, token: str, kb_id: str, data: bytes, *, filename: str = "sample.pdf", force: bool = False):
    fields = {"force": (None, "true" if force else "false")}
    files = {"file": (filename, data, "application/pdf")}
    return client.post(
        f"/api/v1/knowledge-bases/{kb_id}/documents",
        headers=_headers(token),
        files=files,
        data=fields,
    )


class TestUploadAuthorization:
    def test_viewer_cannot_upload(self, client) -> None:
        owner = _auth(client, "owner-auth@example.com")
        kb_id = _kb(client, owner)
        viewer = _auth(client, "viewer-auth@example.com")
        client.put(
            f"/api/v1/knowledge-bases/{kb_id}/members",
            headers=_headers(owner),
            json={"email": "viewer-auth@example.com", "role": "viewer"},
        )
        response = _upload(client, viewer, kb_id, _minimal_pdf())
        assert response.status_code == 403

    def test_outsider_sees_not_found(self, client) -> None:
        owner = _auth(client, "owner-auth2@example.com")
        kb_id = _kb(client, owner)
        outsider = _auth(client, "outsider-auth2@example.com")
        response = _upload(client, outsider, kb_id, _minimal_pdf())
        assert response.status_code == 404


class TestUploadValidation:
    def test_rejects_oversized_file(self, client, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MAX_UPLOAD_BYTES", "32")
        get_settings.cache_clear()
        token = _auth(client, "size@example.com")
        kb_id = _kb(client, token)
        response = _upload(client, token, kb_id, b"%PDF-1.4\n" + b"x" * 40)
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "file_too_large"

    def test_rejects_bad_magic_bytes(self, client) -> None:
        token = _auth(client, "magic@example.com")
        kb_id = _kb(client, token)
        response = _upload(client, token, kb_id, b"not a pdf", filename="sample.pdf")
        assert response.status_code == 415


class TestNewUpload:
    def test_creates_lifecycle_records(self, client, database_url: str) -> None:
        token = _auth(client, "newdoc@example.com")
        kb_id = _kb(client, token)
        data = _minimal_pdf()
        response = _upload(client, token, kb_id, data)
        assert response.status_code == 201
        body = response.json()
        assert body["reused"] is False
        assert body["job_id"] is not None
        doc = body["document"]
        assert doc["status"] == "queued"
        assert doc["current_version_id"] is None
        assert doc["preview_version_id"] is None
        assert doc["pending_version_id"] is not None
        assert doc["pending_run_id"] is not None
        assert doc["pending_status"] == "queued"
        assert doc["byte_size"] == len(data)

        from app.core.db import SessionLocal

        with SessionLocal() as session:
            version = session.get(DocumentVersion, doc["pending_version_id"])
            assert version is not None
            assert version.storage_state == "stored"
            assert version.content_hash == blob_storage.sha256_bytes(data)
            assert version.dedup_hash == version.content_hash
            assert version.knowledge_base_id == uuid.UUID(kb_id)
            stored = blob_storage.abs_path(version.storage_path)
            assert stored.is_file()
            assert stored.read_bytes() == data
            job = session.scalar(select(Job).where(Job.id == uuid.UUID(body["job_id"])))
            assert job is not None
            assert job.status == "pending"
            assert job.idempotency_key == f"processing_run:{doc['pending_run_id']}"

    def test_content_hash_matches_stored_bytes(self, client) -> None:
        token = _auth(client, "hash@example.com")
        kb_id = _kb(client, token)
        data = _minimal_pdf()
        response = _upload(client, token, kb_id, data)
        from app.core.db import SessionLocal

        with SessionLocal() as session:
            version = session.get(DocumentVersion, response.json()["document"]["pending_version_id"])
            assert version is not None
            on_disk = blob_storage.abs_path(version.storage_path).read_bytes()
            assert version.content_hash == blob_storage.sha256_bytes(on_disk)

    def test_duplicate_same_document_returns_reused(self, client) -> None:
        token = _auth(client, "dupdoc@example.com")
        kb_id = _kb(client, token)
        data = _minimal_pdf()
        first = _upload(client, token, kb_id, data)
        second = _upload(client, token, kb_id, data)
        assert first.status_code == 201
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "duplicate_content"

    def test_replacement_duplicate_bytes_reused(self, client) -> None:
        from app.core.db import SessionLocal

        token = _auth(client, "repl-dup@example.com")
        kb_id = _kb(client, token)
        data = _minimal_pdf()
        created = _upload(client, token, kb_id, data)
        doc_id = uuid.UUID(created.json()["document"]["id"])
        with SessionLocal() as session:
            doc = session.get(Document, doc_id)
            assert doc is not None
            doc.pending_version_id = None
            doc.pending_run_id = None
            session.commit()
        again = client.post(
            f"/api/v1/documents/{doc_id}/versions",
            headers=_headers(token),
            files={"file": ("sample.pdf", data, "application/pdf")},
            data={"force": "false"},
        )
        assert again.status_code == 200
        assert again.json()["reused"] is True


class TestReplacementLifecycle:
    def test_ready_document_keeps_serving_on_replacement(self, client) -> None:
        from app.core.db import SessionLocal
        from app.domain.configs import DEFAULT_CHUNK_CONFIG

        token = _auth(client, "ready-repl@example.com")
        kb_id = _kb(client, token)
        data = _minimal_pdf()
        created = _upload(client, token, kb_id, data)
        doc_id = uuid.UUID(created.json()["document"]["id"])

        with SessionLocal() as session:
            doc = session.get(Document, doc_id)
            assert doc is not None
            version = session.get(DocumentVersion, doc.pending_version_id)
            assert version is not None
            doc.current_version_id = version.id
            doc.preview_version_id = version.id
            doc.status = "ready"
            doc.pending_version_id = None
            doc.pending_run_id = None
            run = ProcessingRun(
                document_id=doc.id,
                document_version_id=version.id,
                knowledge_base_id=doc.knowledge_base_id,
                status="ready",
                is_active=True,
                chunk_strategy=DEFAULT_CHUNK_CONFIG,
            )
            session.add(run)
            session.commit()
            serving_version = version.id

        replacement = b"%PDF-1.4\n2 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
        response = client.post(
            f"/api/v1/documents/{doc_id}/versions",
            headers=_headers(token),
            files={"file": ("sample.pdf", replacement, "application/pdf")},
        )
        assert response.status_code == 201
        payload = response.json()["document"]
        assert payload["status"] == "ready"
        assert payload["current_version_id"] == str(serving_version)
        assert payload["pending_version_id"] is not None
        assert payload["pending_run_id"] is not None

    def test_ingest_in_progress_blocks_second_replacement(self, client) -> None:
        token = _auth(client, "inprog@example.com")
        kb_id = _kb(client, token)
        first = _upload(client, token, kb_id, _minimal_pdf())
        doc_id = first.json()["document"]["id"]
        blocked = client.post(
            f"/api/v1/documents/{doc_id}/versions",
            headers=_headers(token),
            files={"file": ("sample.pdf", b"%PDF-1.4\nother\n%%EOF", "application/pdf")},
        )
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "ingest_in_progress"


class TestStorageRecovery:
    def test_promote_incoming_after_crash(self) -> None:
        from app.core.db import SessionLocal
        from app.models import KnowledgeBase, KnowledgeBaseMember, User

        data = _minimal_pdf()
        upload_id = uuid.uuid4()
        incoming_rel = blob_storage.write_incoming(data, upload_id)

        with SessionLocal() as session:
            user = User(
                email=f"{uuid.uuid4().hex}@example.com",
                password_hash="x",
                display_name="T",
            )
            session.add(user)
            session.flush()
            kb = KnowledgeBase(
                owner_id=user.id,
                name="KB",
                description="",
                retrieval_config={},
                chunk_config={},
            )
            session.add(kb)
            session.flush()
            session.add(KnowledgeBaseMember(knowledge_base_id=kb.id, user_id=user.id, role="owner"))
            doc = Document(
                knowledge_base_id=kb.id,
                title="f.pdf",
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
                storage_path=incoming_rel,
                storage_state="incoming",
                byte_size=len(data),
                mime_type="application/pdf",
                content_hash=blob_storage.sha256_bytes(data),
            )
            session.add(version)
            session.commit()
            version_id = version.id

        with SessionLocal() as session:
            version = session.get(DocumentVersion, version_id)
            assert version is not None
            assert recover_version_storage(session, version)
            session.refresh(version)
            assert version.storage_state == "stored"
            assert version.storage_path.startswith(str(kb.id))
            assert blob_storage.abs_path(version.storage_path).read_bytes() == data

    def test_recovery_idempotent(self) -> None:
        from app.core.db import SessionLocal

        data = b"%PDF-1.4\n%%EOF"
        upload_id = uuid.uuid4()
        incoming_rel = blob_storage.write_incoming(data, upload_id)

        with SessionLocal() as session:
            from app.models import KnowledgeBase, KnowledgeBaseMember, User

            user = User(email=f"{uuid.uuid4().hex}@example.com", password_hash="x", display_name="T")
            session.add(user)
            session.flush()
            kb = KnowledgeBase(owner_id=user.id, name="K", description="", retrieval_config={}, chunk_config={})
            session.add(kb)
            session.flush()
            session.add(KnowledgeBaseMember(knowledge_base_id=kb.id, user_id=user.id, role="owner"))
            doc = Document(
                knowledge_base_id=kb.id,
                title="f.pdf",
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
                storage_path=incoming_rel,
                storage_state="incoming",
                byte_size=len(data),
                mime_type="application/pdf",
            )
            session.add(version)
            session.commit()
            vid = version.id

        with SessionLocal() as session:
            version = session.get(DocumentVersion, vid)
            assert version is not None
            assert recover_version_storage(session, version)
            assert recover_version_storage(session, version)
            session.refresh(version)
            assert version.storage_state == "stored"

    def test_orphan_cleanup_respects_references_and_age(self, isolated_storage: str) -> None:
        from app.core.db import SessionLocal
        from datetime import UTC, datetime, timedelta
        import time

        old_id = uuid.uuid4()
        recent_id = uuid.uuid4()
        old_path = blob_storage.abs_path(blob_storage.rel_incoming_path(old_id))
        recent_path = blob_storage.abs_path(blob_storage.rel_incoming_path(recent_id))
        old_path.write_bytes(b"old")
        recent_path.write_bytes(b"new")
        old_time = datetime.now(UTC) - timedelta(minutes=20)
        os.utime(old_path, (old_time.timestamp(), old_time.timestamp()))

        with SessionLocal() as session:
            from app.models import KnowledgeBase, KnowledgeBaseMember, User

            user = User(email=f"{uuid.uuid4().hex}@example.com", password_hash="x", display_name="T")
            session.add(user)
            session.flush()
            kb = KnowledgeBase(owner_id=user.id, name="K", description="", retrieval_config={}, chunk_config={})
            session.add(kb)
            session.flush()
            session.add(KnowledgeBaseMember(knowledge_base_id=kb.id, user_id=user.id, role="owner"))
            doc = Document(
                knowledge_base_id=kb.id,
                title="f.pdf",
                source_type="pdf",
                status="queued",
                created_by=user.id,
            )
            session.add(doc)
            session.flush()
            session.add(
                DocumentVersion(
                    document_id=doc.id,
                    knowledge_base_id=kb.id,
                    version_number=1,
                    storage_path=blob_storage.rel_incoming_path(recent_id),
                    storage_state="incoming",
                    byte_size=3,
                    mime_type="application/pdf",
                )
            )
            session.commit()

        with SessionLocal() as session:
            removed = cleanup_orphan_incoming(session)
            assert removed == 1
            assert not old_path.exists()
            assert recent_path.exists()


class TestForceUserRetry:
    def test_force_rejected_on_new_document_upload(self, client) -> None:
        token = _auth(client, "force-new@example.com")
        kb_id = _kb(client, token)
        response = client.post(
            f"/api/v1/knowledge-bases/{kb_id}/documents",
            headers=_headers(token),
            files={"file": ("sample.pdf", _minimal_pdf(), "application/pdf")},
            data={"force": "true"},
        )
        assert response.status_code == 400

    def test_force_reenqueues_same_version(self, client) -> None:
        from app.core.db import SessionLocal

        token = _auth(client, "force-repl@example.com")
        kb_id = _kb(client, token)
        data = _minimal_pdf()
        created = _upload(client, token, kb_id, data)
        doc_id = created.json()["document"]["id"]
        with SessionLocal() as session:
            doc = session.get(Document, uuid.UUID(doc_id))
            assert doc is not None
            doc.pending_version_id = None
            doc.pending_run_id = None
            session.commit()
        response = client.post(
            f"/api/v1/documents/{doc_id}/versions",
            headers=_headers(token),
            files={"file": ("sample.pdf", data, "application/pdf")},
            data={"force": "true"},
        )
        assert response.status_code == 200
        assert response.json()["reused"] is True
        assert response.json()["job_id"] is not None

    def test_force_returns_409_when_pending(self, client) -> None:
        token = _auth(client, "force-pending@example.com")
        kb_id = _kb(client, token)
        data = _minimal_pdf()
        created = _upload(client, token, kb_id, data)
        doc_id = created.json()["document"]["id"]
        response = client.post(
            f"/api/v1/documents/{doc_id}/versions",
            headers=_headers(token),
            files={"file": ("sample.pdf", data, "application/pdf")},
            data={"force": "true"},
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "ingest_in_progress"

    def test_force_after_failed_run_schedules_retry(self, client) -> None:
        from app.core.db import SessionLocal

        token = _auth(client, "force-failed@example.com")
        kb_id = _kb(client, token)
        data = _minimal_pdf()
        created = _upload(client, token, kb_id, data)
        doc_id = uuid.UUID(created.json()["document"]["id"])
        with SessionLocal() as session:
            doc = session.get(Document, doc_id)
            assert doc is not None
            run = session.get(ProcessingRun, doc.pending_run_id)
            assert run is not None
            run.status = "failed"
            run.error_code = "extract_failed"
            doc.pending_version_id = None
            doc.pending_run_id = None
            session.commit()
        response = client.post(
            f"/api/v1/documents/{doc_id}/versions",
            headers=_headers(token),
            files={"file": ("sample.pdf", data, "application/pdf")},
            data={"force": "true"},
        )
        assert response.status_code == 200
        assert response.json()["job_id"] is not None

    def test_force_rejected_after_retry_budget_exhausted(self, client) -> None:
        from app.core.db import SessionLocal

        token = _auth(client, "force-retry@example.com")
        kb_id = _kb(client, token)
        data = _minimal_pdf()
        created = _upload(client, token, kb_id, data)
        doc_id = uuid.UUID(created.json()["document"]["id"])
        with SessionLocal() as session:
            doc = session.get(Document, doc_id)
            assert doc is not None
            run = session.get(ProcessingRun, doc.pending_run_id)
            assert run is not None
            run.status = "failed"
            run.user_retry_count = 3
            run.user_retry_limit = 3
            doc.pending_version_id = None
            doc.pending_run_id = None
            session.commit()
        response = client.post(
            f"/api/v1/documents/{doc_id}/versions",
            headers=_headers(token),
            files={"file": ("sample.pdf", data, "application/pdf")},
            data={"force": "true"},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "retry_limit_reached"

    def test_force_rejected_on_inconsistent_state(self, client) -> None:
        from app.core.db import SessionLocal

        token = _auth(client, "force-incon@example.com")
        kb_id = _kb(client, token)
        data = _minimal_pdf()
        created = _upload(client, token, kb_id, data)
        doc_id = uuid.UUID(created.json()["document"]["id"])
        with SessionLocal() as session:
            doc = session.get(Document, doc_id)
            assert doc is not None
            run = session.get(ProcessingRun, doc.pending_run_id)
            assert run is not None
            run.status = "failed"
            run.error_code = "inconsistent_state"
            doc.pending_version_id = None
            doc.pending_run_id = None
            session.commit()
        response = client.post(
            f"/api/v1/documents/{doc_id}/versions",
            headers=_headers(token),
            files={"file": ("sample.pdf", data, "application/pdf")},
            data={"force": "true"},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "unprocessable"


class TestCrashAfterPromote:
    def test_final_rename_before_stored_commit_recovers(self) -> None:
        from app.core.db import SessionLocal
        from app.models import KnowledgeBase, KnowledgeBaseMember, User

        data = _minimal_pdf()
        validated = validate_upload_filename("sample.pdf", title=None)
        incoming_rel = blob_storage.write_incoming(data, uuid.uuid4())

        with SessionLocal() as session:
            user = User(email=f"{uuid.uuid4().hex}@example.com", password_hash="x", display_name="T")
            session.add(user)
            session.flush()
            kb = KnowledgeBase(owner_id=user.id, name="K", description="", retrieval_config={}, chunk_config={})
            session.add(kb)
            session.flush()
            session.add(KnowledgeBaseMember(knowledge_base_id=kb.id, user_id=user.id, role="owner"))
            doc = Document(
                knowledge_base_id=kb.id,
                title="f.pdf",
                source_type="pdf",
                status="queued",
                created_by=user.id,
            )
            session.add(doc)
            session.flush()
            session.commit()
            session.refresh(doc)
            version = commit_incoming_records(
                session,
                kb=kb,
                document=doc,
                validated=validated,
                byte_size=len(data),
                incoming_rel=incoming_rel,
            )
            final_rel = promote_version_bytes(version)
            vid, doc_id, kb_id = version.id, doc.id, kb.id

        final_path = blob_storage.abs_path(final_rel)
        assert final_path.is_file()

        with SessionLocal() as session:
            version = session.get(DocumentVersion, vid)
            assert version is not None
            assert version.storage_state == "incoming"
            assert version.content_hash is None

        with SessionLocal() as session:
            stats = run_storage_recovery(session)
            assert stats["promoted"] == 1
            assert stats["enqueued"] == 1

        with SessionLocal() as session:
            version = session.get(DocumentVersion, vid)
            doc = session.get(Document, doc_id)
            assert version is not None and doc is not None
            assert version.storage_state == "stored"
            assert version.content_hash == blob_storage.sha256_bytes(data)
            assert doc.pending_run_id is not None
            assert final_path.read_bytes() == data

        with SessionLocal() as session:
            stats = run_storage_recovery(session)
            assert stats["promoted"] == 0
            assert stats["enqueued"] == 0

    def test_orphan_final_removed_only_without_db_row(self) -> None:
        from app.core.db import SessionLocal
        from app.models import KnowledgeBase, KnowledgeBaseMember, User

        data = b"orphan-final"
        kb_id = uuid.uuid4()
        doc_id = uuid.uuid4()
        ver_id = uuid.uuid4()
        orphan_rel = blob_storage.rel_final_path(kb_id, doc_id, ver_id)
        blob_storage.abs_path(orphan_rel).parent.mkdir(parents=True, exist_ok=True)
        blob_storage.abs_path(orphan_rel).write_bytes(data)

        with SessionLocal() as session:
            user = User(email=f"{uuid.uuid4().hex}@example.com", password_hash="x", display_name="T")
            session.add(user)
            session.flush()
            kb = KnowledgeBase(id=kb_id, owner_id=user.id, name="K", description="", retrieval_config={}, chunk_config={})
            session.add(kb)
            session.flush()
            session.add(KnowledgeBaseMember(knowledge_base_id=kb.id, user_id=user.id, role="owner"))
            doc = Document(
                id=doc_id,
                knowledge_base_id=kb.id,
                title="f.pdf",
                source_type="pdf",
                status="queued",
                created_by=user.id,
            )
            session.add(doc)
            session.flush()
            referenced_rel = blob_storage.rel_final_path(kb_id, doc_id, uuid.uuid4())
            referenced_path = blob_storage.abs_path(referenced_rel)
            referenced_path.parent.mkdir(parents=True, exist_ok=True)
            referenced_path.write_bytes(b"keep")
            kept_ver = uuid.UUID(referenced_rel.split("/")[2])
            session.add(
                DocumentVersion(
                    id=kept_ver,
                    document_id=doc.id,
                    knowledge_base_id=kb.id,
                    version_number=1,
                    storage_path=referenced_rel,
                    storage_state="stored",
                    byte_size=4,
                    mime_type="application/pdf",
                    content_hash=blob_storage.sha256_bytes(b"keep"),
                    dedup_hash=blob_storage.sha256_bytes(b"keep"),
                )
            )
            session.commit()

        with SessionLocal() as session:
            removed = cleanup_orphan_final_paths(session)
            assert removed == 1
            assert not blob_storage.abs_path(orphan_rel).exists()
            assert referenced_path.is_file()


class TestStoragePathSecurity:
    def test_abs_path_rejects_escape(self) -> None:
        with pytest.raises(ValueError):
            blob_storage.abs_path("../etc/passwd")

    def test_rejects_path_traversal_filename(self, client) -> None:
        token = _auth(client, "path-safe@example.com")
        kb_id = _kb(client, token)
        response = _upload(client, token, kb_id, _minimal_pdf(), filename="../../evil.pdf")
        assert response.status_code == 400

    def test_storage_path_uses_internal_ids(self, client) -> None:
        token = _auth(client, "path-ids@example.com")
        kb_id = _kb(client, token)
        response = _upload(client, token, kb_id, _minimal_pdf(), filename="notes.pdf")
        assert response.status_code == 201
        from app.core.db import SessionLocal

        with SessionLocal() as session:
            version = session.get(DocumentVersion, response.json()["document"]["pending_version_id"])
            assert version is not None
            assert "notes.pdf" not in version.storage_path
            assert version.storage_path.startswith(str(kb_id))


class TestIngestRecovery:
    def test_stored_version_without_job_recovers(self) -> None:
        from app.core.db import SessionLocal
        from app.models import KnowledgeBase, KnowledgeBaseMember, User

        data = _minimal_pdf()
        final_rel = None

        with SessionLocal() as session:
            user = User(email=f"{uuid.uuid4().hex}@example.com", password_hash="x", display_name="T")
            session.add(user)
            session.flush()
            kb = KnowledgeBase(owner_id=user.id, name="K", description="", retrieval_config={}, chunk_config={})
            session.add(kb)
            session.flush()
            session.add(KnowledgeBaseMember(knowledge_base_id=kb.id, user_id=user.id, role="owner"))
            doc = Document(
                knowledge_base_id=kb.id,
                title="f.pdf",
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
                storage_path="pending",
                storage_state="incoming",
                byte_size=len(data),
                mime_type="application/pdf",
            )
            session.add(version)
            session.flush()
            incoming_rel = blob_storage.rel_incoming_path(uuid.uuid4())
            blob_storage.write_incoming(data, uuid.UUID(incoming_rel.split("/")[1]))
            version.storage_path = incoming_rel
            session.commit()
            vid, doc_id = version.id, doc.id

        with SessionLocal() as session:
            version = session.get(DocumentVersion, vid)
            assert version is not None
            recover_version_storage(session, version)
            session.refresh(version)
            final_rel = version.storage_path
            assert version.storage_state == "stored"

        with SessionLocal() as session:
            assert recover_unenqueued_ingest(session) == 1
            doc = session.get(Document, doc_id)
            assert doc is not None
            assert doc.pending_version_id == vid
            assert doc.pending_run_id is not None
            assert blob_storage.abs_path(final_rel).read_bytes() == data

    def test_ingest_recovery_idempotent(self) -> None:
        from app.core.db import SessionLocal
        from app.models import KnowledgeBase, KnowledgeBaseMember, User

        data = _minimal_pdf()
        with SessionLocal() as session:
            user = User(email=f"{uuid.uuid4().hex}@example.com", password_hash="x", display_name="T")
            session.add(user)
            session.flush()
            kb = KnowledgeBase(owner_id=user.id, name="K", description="", retrieval_config={}, chunk_config={})
            session.add(kb)
            session.flush()
            session.add(KnowledgeBaseMember(knowledge_base_id=kb.id, user_id=user.id, role="owner"))
            doc = Document(
                knowledge_base_id=kb.id,
                title="f.pdf",
                source_type="pdf",
                status="queued",
                created_by=user.id,
            )
            session.add(doc)
            session.flush()
            incoming = blob_storage.write_incoming(data, uuid.uuid4())
            version = DocumentVersion(
                document_id=doc.id,
                knowledge_base_id=kb.id,
                version_number=1,
                storage_path=incoming,
                storage_state="stored",
                byte_size=len(data),
                mime_type="application/pdf",
                content_hash=blob_storage.sha256_bytes(data),
                dedup_hash=blob_storage.sha256_bytes(data),
            )
            session.add(version)
            session.commit()
            blob_storage.promote_incoming_to_final(
                incoming_rel=incoming,
                final_rel=blob_storage.rel_final_path(kb.id, doc.id, version.id),
            )

        with SessionLocal() as session:
            assert recover_unenqueued_ingest(session) == 1
            assert recover_unenqueued_ingest(session) == 0


class TestGetDocumentAuth:
    def test_viewer_can_read(self, client) -> None:
        owner = _auth(client, "get-owner@example.com")
        kb_id = _kb(client, owner)
        uploaded = _upload(client, owner, kb_id, _minimal_pdf())
        doc_id = uploaded.json()["document"]["id"]
        viewer = _auth(client, "get-viewer@example.com")
        client.put(
            f"/api/v1/knowledge-bases/{kb_id}/members",
            headers=_headers(owner),
            json={"email": "get-viewer@example.com", "role": "viewer"},
        )
        response = client.get(f"/api/v1/documents/{doc_id}", headers=_headers(viewer))
        assert response.status_code == 200

    def test_outsider_cannot_read(self, client) -> None:
        owner = _auth(client, "get-outsider-owner@example.com")
        kb_id = _kb(client, owner)
        uploaded = _upload(client, owner, kb_id, _minimal_pdf())
        doc_id = uploaded.json()["document"]["id"]
        outsider = _auth(client, "get-outsider@example.com")
        response = client.get(f"/api/v1/documents/{doc_id}", headers=_headers(outsider))
        assert response.status_code == 404


class TestKnowledgeBaseOwnership:
    def test_version_kb_matches_document(self, client) -> None:
        token = _auth(client, "kb-match@example.com")
        kb_id = _kb(client, token)
        response = _upload(client, token, kb_id, _minimal_pdf())
        doc = response.json()["document"]
        from app.core.db import SessionLocal

        with SessionLocal() as session:
            version = session.get(DocumentVersion, doc["pending_version_id"])
            document = session.get(Document, doc["id"])
            assert version is not None and document is not None
            assert version.knowledge_base_id == document.knowledge_base_id
