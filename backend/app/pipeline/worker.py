"""Durable, DB-backed job queue with bounded concurrency.

- Jobs survive restarts: on boot, jobs left 'running' are re-queued and the
  pipeline resumes from the last completed stage.
- One active job per project (no duplicate/concurrent work on the same book).
- Failed jobs are retried up to max_attempts, then reported, never hidden."""
from __future__ import annotations

import logging
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import select, update

from ..config import settings
from ..db import session_scope
from ..logging_setup import redact
from ..models import Job, Project, now
from .orchestrator import Cancelled, Paused, Pipeline
from .store import add_history, stage_log

log = logging.getLogger("worker")
ACTIVE = ("queued", "running")


class JobConflict(Exception):
    pass


def enqueue(project_id: str, kind: str, payload: dict | None = None, max_attempts: int = 2) -> Job:
    with session_scope() as s:
        existing = s.scalar(select(Job).where(Job.project_id == project_id, Job.status.in_(ACTIVE)))
        if existing:
            if existing.kind == kind and (existing.payload or {}) == (payload or {}):
                s.expunge(existing)
                return existing  # idempotent: same job already queued
            raise JobConflict(f"Another task ({existing.kind}) is already {existing.status} for this book")
        job = Job(project_id=project_id, kind=kind, payload=payload or {}, max_attempts=max_attempts)
        s.add(job)
        p = s.get(Project, project_id)
        p.status = "queued"  # visible immediately, so clients never see stale "completed"
        p.pause_requested = False
        s.flush()
        s.expunge(job)
        return job


def recover_interrupted() -> int:
    """Called on startup: put interrupted jobs back in the queue."""
    with session_scope() as s:
        jobs = s.scalars(select(Job).where(Job.status == "running")).all()
        for j in jobs:
            j.status = "queued"
            j.message = "re-queued after restart"
            stage_log(s, j.project_id, j.id, "worker", "warning", "Job interrupted by shutdown/crash; resuming")
        return len(jobs)


def run_job(job_id: str) -> None:
    with session_scope() as s:
        job = s.get(Job, job_id)
        kind, payload, pid = job.kind, dict(job.payload or {}), job.project_id
    try:
        pipe = Pipeline(pid, job_id)
        if kind == "generate_book":
            pipe.run_full()
        elif kind == "regenerate_image":
            pipe.regenerate_image(payload["page_id"], payload.get("prompt", ""))
        elif kind == "regenerate_cover":
            pipe.regenerate_cover()
        elif kind == "regenerate_text":
            pipe.regenerate_text(payload["page_id"], payload.get("instruction", ""))
        elif kind in ("rebuild_pdf", "run_qa"):
            pipe.rebuild()
        else:
            raise ValueError(f"unknown job kind {kind}")
        with session_scope() as s:
            j = s.get(Job, job_id)
            j.status, j.finished_at, j.message = "done", now(), "ok"
    except (Paused, Cancelled) as e:
        with session_scope() as s:
            j = s.get(Job, job_id)
            j.status, j.finished_at, j.message = ("cancelled" if isinstance(e, Cancelled) else "done"), now(), e.__class__.__name__
            p = s.get(Project, pid)
            if isinstance(e, Paused):
                p.status, p.pause_requested = "paused", False
    except Exception as e:
        err = redact(f"{e.__class__.__name__}: {e}")
        log.error("job %s failed: %s", job_id, err)
        with session_scope() as s:
            j = s.get(Job, job_id)
            j.error = redact(traceback.format_exc())[-4000:]
            stage_log(s, pid, job_id, "worker", "error", err)
            p = s.get(Project, pid)
            if j.attempts < j.max_attempts:
                j.status, j.message = "queued", f"retrying after error: {err[:200]}"
            else:
                j.status, j.finished_at, j.message = "failed", now(), err[:500]
                p.status, p.error = "failed", err[:1000]
                add_history(s, pid, "job_failed", {"kind": kind, "error": err[:500]})


class Worker:
    def __init__(self, max_books: int | None = None, poll: float = 0.5):
        self.max = max_books or settings.max_concurrent_books
        self.poll = poll
        self.pool = ThreadPoolExecutor(max_workers=self.max, thread_name_prefix="book")
        self.running: set[str] = set()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.thread: threading.Thread | None = None

    def claim(self) -> str | None:
        with session_scope() as s:
            busy_projects = set(s.scalars(select(Job.project_id).where(Job.status == "running")).all())
            for job in s.scalars(select(Job).where(Job.status == "queued").order_by(Job.created_at)).all():
                if job.project_id in busy_projects:
                    continue
                res = s.execute(update(Job).where(Job.id == job.id, Job.status == "queued")
                                .values(status="running", attempts=Job.attempts + 1, started_at=now()))
                if res.rowcount == 1:
                    return job.id
        return None

    def tick(self) -> bool:
        with self._lock:
            if len(self.running) >= self.max:
                return False
        jid = self.claim()
        if not jid:
            return False
        with self._lock:
            self.running.add(jid)

        def done(_):
            with self._lock:
                self.running.discard(jid)

        self.pool.submit(run_job, jid).add_done_callback(done)
        return True

    def loop(self):
        while not self._stop.is_set():
            try:
                if not self.tick():
                    time.sleep(self.poll)
            except Exception:
                log.exception("worker loop error")
                time.sleep(2)

    def start(self):
        n = recover_interrupted()
        if n:
            log.warning("re-queued %d interrupted job(s)", n)
        self.thread = threading.Thread(target=self.loop, name="job-dispatcher", daemon=True)
        self.thread.start()

    def stop(self, wait: bool = False):
        self._stop.set()
        self.pool.shutdown(wait=wait, cancel_futures=False)

    def drain(self, timeout: float = 600) -> bool:
        """Synchronously process jobs until the queue is empty (tests/CLI)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with session_scope() as s:
                active = s.scalar(select(Job.id).where(Job.status.in_(ACTIVE)).limit(1))
            if not active and not self.running:
                return True
            if not self.tick():
                time.sleep(0.1)
        return False


worker: Worker | None = None
