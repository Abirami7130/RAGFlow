"""Worker polling loop."""

from __future__ import annotations

import logging
import signal
import socket
import threading
import time
import uuid

from app.core.db import SessionLocal
from app.models import Job
from app.worker.claim import claim_next_job, refresh_job_lock
from app.worker.dispatch import execute_job

logger = logging.getLogger(__name__)


def default_worker_id() -> str:
    return f"{socket.gethostname()}-{uuid.uuid4().hex[:8]}"


class WorkerRunner:
    def __init__(
        self,
        *,
        worker_id: str | None = None,
        poll_interval_seconds: float = 0.25,
        heartbeat_seconds: int = 30,
    ) -> None:
        self.worker_id = worker_id or default_worker_id()
        self.poll_interval_seconds = poll_interval_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self._shutdown = threading.Event()

    def request_shutdown(self) -> None:
        self._shutdown.set()

    def _heartbeat_loop(self, job_id: uuid.UUID, stop: threading.Event) -> None:
        while not stop.wait(self.heartbeat_seconds):
            with SessionLocal() as db:
                if not refresh_job_lock(db, job_id=job_id, worker_id=self.worker_id):
                    return

    def run_once(self) -> bool:
        with SessionLocal() as db:
            job = claim_next_job(db, worker_id=self.worker_id)
        if job is None:
            return False
        stop = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            args=(job.id, stop),
            name="worker-heartbeat",
            daemon=True,
        )
        heartbeat.start()
        try:
            with SessionLocal() as db:
                job = db.get(Job, job.id)
                assert job is not None
                execute_job(db, job)
        finally:
            stop.set()
            heartbeat.join(timeout=1)
        return True

    def run_forever(self) -> None:
        logger.info("worker_started id=%s", self.worker_id)
        while not self._shutdown.is_set():
            if self.run_once():
                continue
            time.sleep(self.poll_interval_seconds)
        logger.info("worker_stopped id=%s", self.worker_id)


def install_signal_handlers(runner: WorkerRunner) -> None:
    def _handle(_signum: int, _frame: object) -> None:
        runner.request_shutdown()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)
