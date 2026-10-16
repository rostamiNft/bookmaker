"""Locks for multi-process deployments.

* default (single process / SQLite): an in-process :class:`threading.Lock` per name -
  nothing to install, nothing to break;
* ``BOOKMAKER_REDIS_URL`` set: a Redis lock (redis-py, MIT) so several workers on
  different machines share the same limit. Redis is *optional*: if it cannot be
  reached the code falls back to the in-process lock and says so, because a lost lock
  must never stop a book from being generated.
"""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager

from .config import settings

_LOCAL: dict[str, threading.Lock] = {}
_LOCAL_GUARD = threading.Lock()
_STATE: dict = {"redis": None, "last_error": "", "checked": False}


def _local(name: str) -> threading.Lock:
    with _LOCAL_GUARD:
        return _LOCAL.setdefault(name, threading.Lock())


def redis_client():
    """Shared redis client or None (never raises)."""
    if not settings.redis_url:
        return None
    if _STATE["checked"]:
        return _STATE["redis"]
    _STATE["checked"] = True
    try:
        import redis
        client = redis.Redis.from_url(settings.redis_url, socket_timeout=2, socket_connect_timeout=2,
                                      health_check_interval=30)
        client.ping()
        _STATE["redis"] = client
    except Exception as e:  # noqa: BLE001 - Redis is optional
        _STATE["redis"] = None
        _STATE["last_error"] = f"{type(e).__name__}: {e}"[:200]
    return _STATE["redis"]


def reset_cache():
    """Forget the cached client (tests, and after a Redis restart)."""
    _STATE.update({"redis": None, "last_error": "", "checked": False})


def lock_info() -> dict:
    client = redis_client()
    return {"mode": "redis" if client is not None else "in-process",
            "redis_url": bool(settings.redis_url), "connected": client is not None,
            "last_error": _STATE["last_error"]}


@contextmanager
def named_lock(name: str, ttl: int = 120, blocking: bool = True, timeout: float = 10.0):
    """Hold a lock named ``name``. Yields (acquired, mode)."""
    client = redis_client()
    if client is not None:
        lock = client.lock(f"bookmaker:{name}", timeout=ttl, blocking=blocking, blocking_timeout=timeout)
        got = False
        try:
            got = bool(lock.acquire(blocking=blocking, blocking_timeout=timeout))
            yield got, "redis"
        except Exception as e:  # noqa: BLE001 - fall back rather than deadlock
            _STATE["last_error"] = f"{type(e).__name__}: {e}"[:200]
            with _local(name):
                yield True, "in-process"
        finally:
            if got:
                try:
                    lock.release()
                except Exception:
                    pass
        return
    if blocking:
        got = _local(name).acquire(timeout=timeout)
        try:
            yield got, "in-process"
        finally:
            if got:
                _local(name).release()
    else:
        got = _local(name).acquire(blocking=False)
        try:
            yield got, "in-process"
        finally:
            if got:
                _local(name).release()


def wait_for_db(timeout: float = 30.0) -> bool:
    """Wait until the database answers (used on container start)."""
    end = time.monotonic() + timeout
    from .db import engine
    while time.monotonic() < end:
        try:
            with engine.connect() as c:
                c.exec_driver_sql("select 1" if not settings.database_url else "select 1")
            return True
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return False
