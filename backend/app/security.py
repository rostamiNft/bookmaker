"""Secrets encryption, auth, rate limiting and safe file paths."""
from __future__ import annotations

import os
import secrets
import threading
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, Request

from .config import settings

# ---------------------------------------------------------------- secrets
_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = settings.secret_key
        if not key:
            kf = settings.data_dir / ".secret.key"
            if not kf.exists():
                kf.write_bytes(Fernet.generate_key())
                os.chmod(kf, 0o600)
            key = kf.read_bytes().decode()
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def encrypt_secret(value: str) -> str:
    if not value:
        return ""
    if value.startswith("env:"):  # reference to an environment variable, never stored in clear
        return value
    return "enc:" + _get_fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    if value.startswith("env:"):
        return os.environ.get(value[4:], "")
    if value.startswith("enc:"):
        try:
            return _get_fernet().decrypt(value[4:].encode()).decode()
        except InvalidToken:
            return ""
    return value


def mask_secret(stored: str) -> str:
    if not stored:
        return ""
    if stored.startswith("env:"):
        return stored  # the variable name is not secret
    plain = decrypt_secret(stored)
    if len(plain) <= 8:
        return "****"
    return plain[:3] + "…" + plain[-4:]


# ---------------------------------------------------------------- auth
def require_auth(request: Request) -> str:
    """Return the current user id. Single-user mode -> 'local'.
    When BOOKMAKER_API_TOKEN is set a matching bearer token is required."""
    token = settings.api_token
    if not token:
        return "local"
    header = request.headers.get("authorization", "")
    supplied = header[7:] if header.lower().startswith("bearer ") else request.query_params.get("token", "")
    if not supplied or not secrets.compare_digest(supplied, token):
        raise HTTPException(status_code=401, detail="Authentication required")
    return "local"


# ---------------------------------------------------------------- rate limit
class RateLimiter:
    """Sliding-window limiter per client key (thread-safe)."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        if self.per_minute <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if now - t < 60]
            if len(hits) >= self.per_minute:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True


rate_limiter = RateLimiter(settings.rate_limit_per_minute)


# ---------------------------------------------------------------- paths
def safe_join(base: Path, *parts: str) -> Path:
    """Join and guarantee the result stays inside `base` (prevents path traversal)."""
    base = base.resolve()
    target = base.joinpath(*parts).resolve()
    if target != base and base not in target.parents:
        raise HTTPException(status_code=400, detail="Invalid path")
    return target
