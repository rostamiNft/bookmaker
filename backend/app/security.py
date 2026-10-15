"""Secrets encryption, auth, rate limiting and safe file paths."""
from __future__ import annotations

import hashlib
import hmac
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
# Three modes (BOOKMAKER_AUTH_MODE):
#   none     - single local user, no login (default for a personal machine)
#   token    - one shared bearer token (BOOKMAKER_API_TOKEN)
#   accounts - user accounts with roles (admin | user); each user sees only their own books
def _supplied_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    return header[7:].strip() if header.lower().startswith("bearer ") else request.query_params.get("token", "")


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_hex, dk_hex = stored.split("$")
        if algo != "scrypt":
            return False
        return hmac.compare_digest(hash_password(password, bytes.fromhex(salt_hex)).split("$")[2], dk_hex)
    except (ValueError, AttributeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def current_user(request: Request) -> dict:
    """Return {'id', 'role', 'username'} for the caller or raise 401."""
    cached = getattr(request.state, "user", None)
    if cached:
        return cached
    mode = settings.auth_mode
    if mode == "none":
        user = {"id": "local", "role": "admin", "username": "local"}
    elif mode == "token":
        supplied = _supplied_token(request)
        if not supplied or not settings.api_token or not secrets.compare_digest(supplied, settings.api_token):
            raise HTTPException(status_code=401, detail="Authentication required")
        user = {"id": "local", "role": "admin", "username": "local"}
    else:
        from datetime import datetime
        from .db import session_scope
        from .models import AuthSession, User
        supplied = _supplied_token(request)
        if not supplied:
            raise HTTPException(status_code=401, detail="Authentication required")
        with session_scope() as s:
            sess = s.get(AuthSession, token_hash(supplied))
            if sess is None or sess.expires_at < datetime.utcnow():
                raise HTTPException(status_code=401, detail="Session expired or invalid; please log in")
            u = s.get(User, sess.user_id)
            if u is None or not u.active:
                raise HTTPException(status_code=401, detail="Account disabled")
            user = {"id": u.id, "role": u.role, "username": u.username}
    request.state.user = user
    return user


def require_auth(request: Request) -> str:
    """Return the current user id (owner id of books)."""
    return current_user(request)["id"]


def require_admin(request: Request) -> str:
    u = current_user(request)
    if u["role"] != "admin":
        raise HTTPException(status_code=403, detail="Administrator role required")
    return u["id"]


class LoginThrottle:
    """Locks a username for 15 minutes after 5 failed logins (brute-force protection)."""

    def __init__(self, max_fail: int = 5, window: float = 900):
        self.max_fail, self.window = max_fail, window
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def blocked(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            f = [t for t in self._fails.get(key, []) if now - t < self.window]
            self._fails[key] = f
            return len(f) >= self.max_fail

    def fail(self, key: str):
        with self._lock:
            self._fails.setdefault(key, []).append(time.monotonic())

    def reset(self, key: str):
        with self._lock:
            self._fails.pop(key, None)


login_throttle = LoginThrottle()


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
