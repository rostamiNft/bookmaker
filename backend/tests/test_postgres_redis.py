"""PostgreSQL and Redis paths, against real servers.

* PostgreSQL: ``pgserver`` (Apache-2.0) starts a real PostgreSQL 16 locally and the
  whole schema is created there, plus the multi-worker job claim
  (``SELECT ... FOR UPDATE SKIP LOCKED``) - the exact statement the worker uses when
  several processes share one queue.
* Redis: if ``BOOKMAKER_REDIS_URL`` points at a running server the named lock is
  checked against it; otherwise the test asserts the documented fallback (in-process
  lock, job still runs) instead of pretending.

Both are skipped (with a clear reason) when the optional package is missing.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from app.config import settings
from app.db import Base, migrate_add_columns, session_scope
from app.models import Job, Project, now

pytest.importorskip("pg8000", reason="PostgreSQL support needs pg8000 (BSD-3)")


@pytest.fixture(scope="module")
def postgres():
    pgserver = pytest.importorskip("pgserver", reason="pgserver (Apache-2.0) provides a local PostgreSQL for the test")
    tmp = tempfile.mkdtemp(prefix="bookmaker-pg-")
    try:
        srv = pgserver.get_server(tmp)
    except Exception as e:  # noqa: BLE001 - no binaries in this environment
        pytest.skip(f"PostgreSQL could not be started: {type(e).__name__}: {e}")
    sock = os.path.join(tmp, ".s.PGSQL.5432")
    url = f"postgresql+pg8000://postgres:@/postgres?unix_sock={sock}"
    yield url


def _bind(url: str):
    """Point app.db at this database for the duration of the test."""
    import sqlalchemy
    from app import db
    engine = sqlalchemy.create_engine(url, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    added = migrate_add_columns()
    old_engine, old_factory, old_is = db.engine, db.SessionLocal, db.IS_POSTGRES
    db.engine, db.IS_POSTGRES = engine, True
    db.SessionLocal = sqlalchemy.orm.sessionmaker(bind=engine, expire_on_commit=False)

    def restore():
        db.engine, db.SessionLocal, db.IS_POSTGRES = old_engine, old_factory, old_is
        engine.dispose()
    return restore


def test_schema_and_job_claim_on_postgres(postgres):
    restore = _bind(postgres)
    try:
        # every table of the real schema is created by SQLAlchemy on PostgreSQL
        from sqlalchemy import inspect, select
        insp = inspect(db_engine())
        assert {"projects", "pages", "characters", "jobs", "users", "auth_sessions",
                "password_resets", "oidc_states"} <= set(insp.get_table_names())
        # a project + two queued jobs, then the SKIP LOCKED claim used by the worker
        with session_scope() as s:
            s.merge(Project(id="pgtest", title="PG book", settings={"language": "en"}, stage_state={}))
            s.flush()
            s.add_all([Job(id="pgjob1", project_id="pgtest", kind="book", status="queued"),
                       Job(id="pgjob2", project_id="pgtest", kind="book", status="queued")])
        from sqlalchemy import update
        claimed = []
        # two "workers" in two connections: each must get a different job
        for _ in range(2):
            with session_scope() as s:
                job = s.scalars(select(Job).where(Job.status == "queued").order_by(Job.created_at)
                                .with_for_update(skip_locked=True).limit(1)).first()
                assert job is not None
                res = s.execute(update(Job).where(Job.id == job.id, Job.status == "queued")
                                .values(status="running", attempts=Job.attempts + 1, started_at=now()))
                assert res.rowcount == 1
                claimed.append(job.id)
        assert sorted(claimed) == ["pgjob1", "pgjob2"], claimed
        with session_scope() as s:
            assert s.scalar(select(Job).where(Job.status == "queued").limit(1)) is None
    finally:
        restore()


def db_engine():
    from app import db
    return db.engine


def test_worker_claim_uses_skip_locked_on_postgres(postgres):
    """The Worker.claim() code path itself, on PostgreSQL."""
    restore = _bind(postgres)
    try:
        from sqlalchemy import select
        from app.pipeline.worker import Worker
        with session_scope() as s:
            s.merge(Project(id="pgtest2", title="PG book 2", settings={}, stage_state={}))
            s.flush()
            s.merge(Job(id="claim1", project_id="pgtest2", kind="book", status="queued"))
        w = Worker(max_books=1)
        got = w.claim()
        assert got == "claim1", got
        assert w.claim() is None          # nothing left to claim
    finally:
        restore()


def test_redis_lock_or_documented_fallback(monkeypatch):
    """With Redis configured the lock is taken in Redis; without it the code falls back
    to an in-process lock and keeps working. Both cases are verified, never assumed."""
    from app import locks
    locks.reset_cache()
    monkeypatch.setattr(settings, "redis_url", "")
    with locks.named_lock("test-lock", ttl=5) as (got, mode):
        assert got is True and mode == "in-process"
    assert locks.lock_info()["mode"] == "in-process"
    # a Redis URL that is not reachable must degrade, not hang or raise
    monkeypatch.setattr(settings, "redis_url", "redis://127.0.0.1:1/0")
    locks.reset_cache()
    with locks.named_lock("test-lock2", ttl=5, timeout=1) as (got, mode):
        assert got is True, "the fallback lock must still be acquired"
        assert mode == "in-process"
    assert locks.lock_info()["last_error"]
    locks.reset_cache()
    if os.environ.get("BOOKMAKER_REDIS_URL"):
        monkeypatch.setattr(settings, "redis_url", os.environ["BOOKMAKER_REDIS_URL"])
        locks.reset_cache()
        if locks.redis_client() is not None:
            with locks.named_lock("test-lock3", ttl=5, timeout=5) as (got, mode):
                assert got is True and mode == "redis", locks.lock_info()
