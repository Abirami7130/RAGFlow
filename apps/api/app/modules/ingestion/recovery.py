"""Crash recovery for interrupted uploads and orphan incoming files."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document, DocumentVersion, Job, KnowledgeBase, ProcessingRun
from app.modules.ingestion.enqueue import enqueue_ingest
from app.modules.ingestion.storage import (
    INCOMING_SUBDIR,
    abs_path,
    finalize_same_inode_cleanup,
    fsync_directory,
    promote_incoming_to_final,
    rel_final_path,
    same_inode,
    sha256_bytes,
    storage_root,
)

logger = logging.getLogger(__name__)

ORPHAN_GRACE = timedelta(minutes=15)


def _final_rel(version: DocumentVersion) -> str:
    return rel_final_path(version.knowledge_base_id, version.document_id, version.id)


def recover_version_storage(db: Session, version: DocumentVersion) -> bool:
    """Promote or finalize one version row. Returns True when the version is stored."""
    if version.storage_state == "stored":
        return True

    incoming = abs_path(version.storage_path)
    final_rel = _final_rel(version)
    final = abs_path(final_rel)

    if incoming.exists() and final.exists():
        if same_inode(incoming, final):
            finalize_same_inode_cleanup(incoming_rel=version.storage_path, final_rel=final_rel)
            version.storage_path = final_rel
            version.storage_state = "stored"
            if version.content_hash is None:
                version.content_hash = sha256_bytes(final.read_bytes())
                version.dedup_hash = version.content_hash
            db.commit()
            return True
        logger.error(
            "storage_conflict version_id=%s incoming=%s final=%s",
            version.id,
            version.storage_path,
            final_rel,
        )
        return False

    if incoming.exists() and not final.exists():
        promote_incoming_to_final(incoming_rel=version.storage_path, final_rel=final_rel)
        version.storage_path = final_rel
        version.storage_state = "stored"
        final = abs_path(final_rel)
        if version.content_hash is None:
            version.content_hash = sha256_bytes(final.read_bytes())
            version.dedup_hash = version.content_hash
        db.commit()
        return True

    if not incoming.exists() and final.exists():
        fsync_directory(final.parent)
        incoming_dir = storage_root() / INCOMING_SUBDIR
        if incoming_dir.exists():
            fsync_directory(incoming_dir)
        version.storage_path = final_rel
        version.storage_state = "stored"
        if version.content_hash is None:
            version.content_hash = sha256_bytes(final.read_bytes())
            version.dedup_hash = version.content_hash
        db.commit()
        return True

    return False


def recover_pending_uploads(db: Session) -> int:
    rows = db.scalars(
        select(DocumentVersion).where(DocumentVersion.storage_state == "incoming").order_by(DocumentVersion.created_at)
    ).all()
    recovered = 0
    for version in rows:
        if recover_version_storage(db, version):
            recovered += 1
    return recovered


def _referenced_incoming_paths(db: Session) -> set[str]:
    rows = db.scalars(
        select(DocumentVersion.storage_path).where(
            DocumentVersion.storage_state == "incoming",
            DocumentVersion.storage_path.like(f"{INCOMING_SUBDIR}/%"),
        )
    ).all()
    return set(rows)


def cleanup_orphan_incoming(db: Session) -> int:
    referenced = _referenced_incoming_paths(db)
    incoming_dir = storage_root() / INCOMING_SUBDIR
    if not incoming_dir.is_dir():
        return 0
    cutoff = datetime.now(UTC) - ORPHAN_GRACE
    removed = 0
    for entry in incoming_dir.iterdir():
        if not entry.is_file():
            continue
        rel = f"{INCOMING_SUBDIR}/{entry.name}"
        if rel in referenced:
            continue
        mtime = datetime.fromtimestamp(entry.stat().st_mtime, tz=UTC)
        if mtime >= cutoff:
            continue
        entry.unlink(missing_ok=True)
        removed += 1
    if removed:
        fsync_directory(incoming_dir)
        logger.info("orphan_incoming_removed count=%s", removed)
    return removed


def _latest_stored_version(db: Session, document_id: uuid.UUID) -> DocumentVersion | None:
    return db.scalar(
        select(DocumentVersion)
        .where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.storage_state == "stored",
        )
        .order_by(DocumentVersion.version_number.desc())
    )


def recover_unenqueued_ingest(db: Session) -> int:
    """Enqueue or repair ingest for stored versions that never received a run/job."""
    recovered = 0
    docs = db.scalars(
        select(Document)
        .where(
            Document.deleted_at.is_(None),
            Document.pending_run_id.is_(None),
        )
        .order_by(Document.created_at)
    ).all()

    for doc in docs:
        version = _latest_stored_version(db, doc.id)
        if version is None:
            continue

        existing_run = db.scalar(
            select(ProcessingRun)
            .where(ProcessingRun.document_version_id == version.id)
            .order_by(ProcessingRun.created_at.desc())
        )
        if existing_run is not None:
            job = db.scalar(
                select(Job).where(Job.idempotency_key == f"processing_run:{existing_run.id}")
            )
            if job is not None and job.status in ("pending", "running"):
                locked = db.scalar(select(Document).where(Document.id == doc.id).with_for_update())
                if locked is None or locked.pending_run_id is not None:
                    continue
                locked.pending_version_id = version.id
                locked.pending_run_id = existing_run.id
                db.commit()
                recovered += 1
            continue

        locked = db.scalar(select(Document).where(Document.id == doc.id).with_for_update())
        if locked is None or locked.pending_run_id is not None:
            continue

        kb = db.get(KnowledgeBase, locked.knowledge_base_id)
        if kb is None:
            continue

        original = version.original_filename or locked.title
        enqueue_ingest(
            db,
            kb=kb,
            document=locked,
            version=version,
            user_id=locked.created_by,
            source_type=locked.source_type,
            display_filename=locked.title,
            original_filename=original,
        )
        db.commit()
        recovered += 1

    return recovered


def cleanup_orphan_final_paths(db: Session) -> int:
    """Remove final-path files whose version id is not present in the database."""
    root = storage_root()
    removed = 0
    for kb_dir in root.iterdir():
        if not kb_dir.is_dir() or kb_dir.name == INCOMING_SUBDIR:
            continue
        for doc_dir in kb_dir.iterdir():
            if not doc_dir.is_dir():
                continue
            for entry in doc_dir.iterdir():
                if not entry.is_file():
                    continue
                try:
                    version_id = uuid.UUID(entry.name)
                except ValueError:
                    continue
                if db.get(DocumentVersion, version_id) is not None:
                    continue
                entry.unlink(missing_ok=True)
                removed += 1
    if removed:
        logger.info("orphan_final_removed count=%s", removed)
    return removed


def run_storage_recovery(db: Session) -> dict[str, int]:
    promoted = recover_pending_uploads(db)
    enqueued = recover_unenqueued_ingest(db)
    incoming_orphans = cleanup_orphan_incoming(db)
    final_orphans = cleanup_orphan_final_paths(db)
    return {
        "promoted": promoted,
        "enqueued": enqueued,
        "orphans_removed": incoming_orphans,
        "final_orphans_removed": final_orphans,
    }
