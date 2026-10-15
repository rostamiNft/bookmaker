"""Login, first-run setup and user administration (BOOKMAKER_AUTH_MODE=accounts)."""
from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..models import AuthSession, Project, User
from ..security import (current_user, hash_password, login_throttle, require_admin, token_hash, verify_password)

router = APIRouter(prefix="/api/auth")

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{3,64}$")


def _user_out(u: User, db: Session | None = None) -> dict:
    out = {"id": u.id, "username": u.username, "display_name": u.display_name, "role": u.role, "active": u.active,
           "created_at": u.created_at.isoformat() + "Z", "last_login": u.last_login.isoformat() + "Z" if u.last_login else None}
    if db is not None:
        out["books"] = db.scalar(select(func.count()).select_from(Project).where(Project.owner_id == u.id)) or 0
    return out


def _check_password(pw: str):
    if len(pw) < 8:
        raise HTTPException(422, "Password must have at least 8 characters")
    if pw.isdigit() or pw.lower() == pw and pw.isalpha():
        raise HTTPException(422, "Password is too simple: mix letters with digits or symbols")


def _issue(db: Session, u: User) -> str:
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(token_hash=token_hash(token), user_id=u.id,
                       expires_at=datetime.utcnow() + timedelta(days=settings.session_days)))
    db.execute(delete(AuthSession).where(AuthSession.expires_at < datetime.utcnow()))
    u.last_login = datetime.utcnow()
    db.commit()
    return token


@router.get("/status")
def status(db: Session = Depends(get_db)):
    users = db.scalar(select(func.count()).select_from(User)) if settings.auth_mode == "accounts" else 0
    return {"mode": settings.auth_mode, "needs_setup": settings.auth_mode == "accounts" and users == 0}


class Credentials(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=1, max_length=200)
    display_name: str = Field("", max_length=120)


@router.post("/setup")
def setup(body: Credentials, db: Session = Depends(get_db)):
    """Create the first administrator. Only possible while no account exists."""
    if settings.auth_mode != "accounts":
        raise HTTPException(400, "Accounts are not enabled (BOOKMAKER_AUTH_MODE=accounts)")
    if db.scalar(select(func.count()).select_from(User)):
        raise HTTPException(409, "Setup already completed")
    if not USERNAME_RE.match(body.username):
        raise HTTPException(422, "Username: 3-64 letters, digits, dot, dash or underscore")
    _check_password(body.password)
    u = User(username=body.username, display_name=body.display_name or body.username, password_hash=hash_password(body.password), role="admin")
    db.add(u)
    db.flush()
    # books created before accounts were enabled belong to the first administrator
    for p in db.scalars(select(Project).where(Project.owner_id == "local")):
        p.owner_id = u.id
    token = _issue(db, u)
    return {"token": token, "user": _user_out(u)}


@router.post("/login")
def login(body: Credentials, request: Request, db: Session = Depends(get_db)):
    if settings.auth_mode != "accounts":
        raise HTTPException(400, "Accounts are not enabled")
    key = body.username.lower()
    if login_throttle.blocked(key):
        raise HTTPException(429, "Too many failed logins; try again in 15 minutes")
    u = db.scalar(select(User).where(func.lower(User.username) == key))
    if u is None or not verify_password(body.password, u.password_hash) or not u.active:
        login_throttle.fail(key)
        raise HTTPException(401, "Wrong username or password")
    login_throttle.reset(key)
    return {"token": _issue(db, u), "user": _user_out(u)}


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    current_user(request)
    if settings.auth_mode == "accounts":
        h = request.headers.get("authorization", "")
        tok = h[7:].strip() if h.lower().startswith("bearer ") else ""
        if tok:
            db.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash(tok)))
            db.commit()
    return {"ok": True}


@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)):
    u = current_user(request)
    if settings.auth_mode == "accounts":
        row = db.get(User, u["id"])
        return {**_user_out(row), "mode": settings.auth_mode}
    return {**u, "display_name": "", "mode": settings.auth_mode}


class PasswordChange(BaseModel):
    current_password: str = Field(..., max_length=200)
    new_password: str = Field(..., max_length=200)


@router.post("/password")
def change_password(body: PasswordChange, request: Request, db: Session = Depends(get_db)):
    u = current_user(request)
    if settings.auth_mode != "accounts":
        raise HTTPException(400, "Accounts are not enabled")
    row = db.get(User, u["id"])
    if not verify_password(body.current_password, row.password_hash):
        raise HTTPException(401, "Current password is wrong")
    _check_password(body.new_password)
    row.password_hash = hash_password(body.new_password)
    db.execute(delete(AuthSession).where(AuthSession.user_id == row.id))  # log out other sessions
    db.commit()
    return {"token": _issue(db, row)}


# ------------------------------------------------------------------ admin
class UserIn(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., max_length=200)
    display_name: str = Field("", max_length=120)
    role: str = Field("user", pattern="^(admin|user)$")


class UserPatch(BaseModel):
    display_name: str | None = Field(None, max_length=120)
    role: str | None = Field(None, pattern="^(admin|user)$")
    active: bool | None = None
    password: str | None = Field(None, max_length=200)


@router.get("/users", dependencies=[Depends(require_admin)])
def list_users(db: Session = Depends(get_db)):
    return [_user_out(u, db) for u in db.scalars(select(User).order_by(User.created_at))]


@router.post("/users", status_code=201, dependencies=[Depends(require_admin)])
def create_user(body: UserIn, db: Session = Depends(get_db)):
    if settings.auth_mode != "accounts":
        raise HTTPException(400, "Accounts are not enabled")
    if not USERNAME_RE.match(body.username):
        raise HTTPException(422, "Username: 3-64 letters, digits, dot, dash or underscore")
    if db.scalar(select(User).where(func.lower(User.username) == body.username.lower())):
        raise HTTPException(409, "Username already exists")
    _check_password(body.password)
    u = User(username=body.username, display_name=body.display_name or body.username, password_hash=hash_password(body.password), role=body.role)
    db.add(u)
    db.commit()
    return _user_out(u, db)


def _admins_left(db: Session, excluding: str) -> int:
    return db.scalar(select(func.count()).select_from(User).where(User.role == "admin", User.active.is_(True), User.id != excluding)) or 0


@router.patch("/users/{uid}")
def patch_user(uid: str, body: UserPatch, request: Request, db: Session = Depends(get_db), _admin: str = Depends(require_admin)):
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "User not found")
    demote = (body.role == "user" and u.role == "admin") or (body.active is False and u.role == "admin")
    if demote and _admins_left(db, u.id) == 0:
        raise HTTPException(409, "At least one active administrator must remain")
    if body.display_name is not None:
        u.display_name = body.display_name
    if body.role is not None:
        u.role = body.role
    if body.active is not None:
        u.active = body.active
        if not body.active:
            db.execute(delete(AuthSession).where(AuthSession.user_id == u.id))
    if body.password:
        _check_password(body.password)
        u.password_hash = hash_password(body.password)
        db.execute(delete(AuthSession).where(AuthSession.user_id == u.id))
    db.commit()
    return _user_out(u, db)


@router.delete("/users/{uid}")
def delete_user(uid: str, db: Session = Depends(get_db), admin_id: str = Depends(require_admin)):
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "User not found")
    if u.id == admin_id:
        raise HTTPException(409, "You cannot delete your own account")
    if u.role == "admin" and _admins_left(db, u.id) == 0:
        raise HTTPException(409, "At least one active administrator must remain")
    books = db.scalar(select(func.count()).select_from(Project).where(Project.owner_id == u.id)) or 0
    if books:
        # books are never deleted implicitly: they are transferred to the deleting administrator
        for p in db.scalars(select(Project).where(Project.owner_id == u.id)):
            p.owner_id = admin_id
    db.delete(u)
    db.commit()
    return {"ok": True, "books_transferred": books}
