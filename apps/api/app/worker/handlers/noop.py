"""Test-only noop handler."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Job


def handle_noop(db: Session, job: Job) -> None:
    """Mark work complete without side effects (tests only)."""
    _ = db, job
