"""Login, first-run setup and user administration (BOOKMAKER_AUTH_MODE=accounts)."""
from __future__ import annotations

import re
import secrets
import urllib.parse
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import get_db
from ..mailer import reset_mail, send_mail
from ..models import AuthSession, OidcState, PasswordReset, Project, User
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


# ------------------------------------------------------------------ password reset
class ResetRequest(BaseModel):
    username: str = Field("", max_length=200)
    email: str = Field("", max_length=200)


class ResetConfirm(BaseModel):
    token: str = Field(..., min_length=20, max_length=200)
    password: str = Field(..., max_length=200)


RESET_MINUTES = 60


def _reset_link(token: str, base: str = "") -> str:
    base = (base or settings.public_url or "").rstrip("/")
    return f"{base}/#/reset?token={urllib.parse.quote(token)}"


def _find_user(db: Session, username: str, email: str) -> User | None:
    username, email = (username or "").strip().lower(), (email or "").strip().lower()
    if email:
        u = db.scalar(select(User).where(func.lower(User.email) == email))
        if u is not None:
            return u
    if username:
        return db.scalar(select(User).where(func.lower(User.username) == username))
    return None


def _make_reset(db: Session, u: User) -> str:
    from datetime import timedelta
    token = secrets.token_urlsafe(32)
    db.execute(delete(PasswordReset).where(PasswordReset.expires_at < datetime.utcnow()))
    db.add(PasswordReset(token_hash=token_hash(token), user_id=u.id,
                         expires_at=datetime.utcnow() + timedelta(minutes=RESET_MINUTES)))
    db.commit()
    return token


@router.post("/reset/request")
def reset_request(body: ResetRequest, request: Request, db: Session = Depends(get_db)):
    """E-mail a one-time reset link. Always answers 200 so the endpoint cannot be used to
    find out which accounts exist. With SMTP disabled (or for an admin) the link is
    returned instead, so the flow stays usable and testable without a mail server."""
    if settings.auth_mode != "accounts":
        raise HTTPException(400, "Accounts are not enabled")
    is_admin = False
    try:
        is_admin = current_user(request)["role"] == "admin"
    except HTTPException:
        pass
    u = _find_user(db, body.username, body.email)
    out = {"ok": True, "mailed": False, "method": "none"}
    if u is None or not u.active:
        return out                                   # same answer either way
    token = _make_reset(db, u)
    link = _reset_link(token, str(request.base_url).rstrip("/") if not settings.public_url else settings.public_url)
    subj, plain, html = reset_mail(link, RESET_MINUTES, u.username)
    res = send_mail(u.email or "", subj, plain, html)
    out.update({"mailed": res.sent, "method": res.method, "error": res.error})
    if not res.sent:  # no mail server: let an administrator pass the link on by hand
        if is_admin or settings.env != "production":
            out["link"] = link
            out["note"] = "SMTP is not configured: pass this link to the user yourself"
        else:
            out["note"] = "SMTP is not configured; ask an administrator for a reset link"
    return out


@router.post("/reset/confirm")
def reset_confirm(body: ResetConfirm, db: Session = Depends(get_db)):
    if settings.auth_mode != "accounts":
        raise HTTPException(400, "Accounts are not enabled")
    h = token_hash(body.token)
    row = db.get(PasswordReset, h)
    if row is None or row.used_at is not None or row.expires_at < datetime.utcnow():
        raise HTTPException(400, "Reset link is invalid or has expired")
    u = db.get(User, row.user_id)
    if u is None or not u.active:
        raise HTTPException(400, "Account is not active")
    _check_password(body.password)
    u.password_hash = hash_password(body.password)
    row.used_at = datetime.utcnow()
    db.execute(delete(AuthSession).where(AuthSession.user_id == u.id))     # log out everywhere
    db.execute(delete(PasswordReset).where(PasswordReset.user_id == u.id))
    db.commit()
    return {"ok": True, "username": u.username}


@router.post("/users/{uid}/reset-link", dependencies=[Depends(require_admin)])
def admin_reset_link(uid: str, request: Request, db: Session = Depends(get_db)):
    """Administrator path: a reset link without e-mail (when no SMTP server is set up)."""
    u = db.get(User, uid)
    if not u:
        raise HTTPException(404, "User not found")
    token = _make_reset(db, u)
    link = _reset_link(token, settings.public_url or str(request.base_url).rstrip("/"))
    return {"ok": True, "link": link, "expires_minutes": RESET_MINUTES,
            "email": u.email, "mailed": False if not u.email else send_mail(
                u.email, *reset_mail(link, RESET_MINUTES, u.username)).sent}


# ------------------------------------------------------------------ single sign-on (OIDC)
@router.get("/sso")
def sso_status():
    from ..sso import provider_label
    if not settings.oidc_configured:
        return {"enabled": False, "note": "Set BOOKMAKER_OIDC_ISSUER and BOOKMAKER_OIDC_CLIENT_ID to enable single sign-on"}
    return {"enabled": True, "label": provider_label(), "issuer": settings.oidc_issuer,
            "auto_create": settings.oidc_auto_create}


@router.get("/sso/authorize")
def sso_authorize(request: Request, redirect_uri: str = "", db: Session = Depends(get_db)):
    """Start an OIDC login: stores the PKCE verifier and returns the provider URL."""
    from datetime import timedelta
    from ..sso import SsoError, authorize_url, new_state
    if settings.auth_mode != "accounts":
        raise HTTPException(400, "Accounts are not enabled")
    if not settings.oidc_configured:
        raise HTTPException(400, "Single sign-on is not configured")
    state, verifier, nonce = new_state()
    uri = redirect_uri or settings.oidc_redirect_uri or f"{str(request.base_url).rstrip('/')}/api/auth/sso/callback"
    try:
        url = authorize_url(state, verifier, nonce, uri)
    except SsoError as e:
        raise HTTPException(502, f"Identity provider unavailable: {e}")
    db.execute(delete(OidcState).where(OidcState.expires_at < datetime.utcnow()))
    db.add(OidcState(state=state, code_verifier=verifier, nonce=nonce, redirect_to=uri,
                     expires_at=datetime.utcnow() + timedelta(minutes=10)))
    db.commit()
    return {"url": url, "state": state}


@router.get("/sso/callback")
def sso_callback(code: str = "", state: str = "", error: str = "", db: Session = Depends(get_db)):
    """Provider redirects here. Exchanges the code, verifies the ID token, logs the user in."""
    from fastapi.responses import RedirectResponse
    from ..sso import SsoError, exchange_code, verify_id_token
    if error or not code or not state:
        raise HTTPException(400, f"Single sign-on failed: {error or 'missing code'}")
    row = db.get(OidcState, state)
    if row is None or row.expires_at < datetime.utcnow():
        raise HTTPException(400, "Login request expired or unknown: please start again")
    verifier, nonce, redirect_to = row.code_verifier, row.nonce, row.redirect_to
    db.delete(row)
    db.commit()
    if not settings.oidc_configured:
        raise HTTPException(400, "Single sign-on is not configured")
    try:
        tokens = exchange_code(code, verifier, settings.oidc_redirect_uri or redirect_to or
                               f"{settings.public_url}/api/auth/sso/callback")
        ident = verify_id_token(tokens.get("id_token", ""), nonce)
    except SsoError as e:
        raise HTTPException(401, str(e))
    u = db.scalar(select(User).where(User.sso_subject == ident.subject))
    if u is None and ident.email_verified and ident.email:
        u = db.scalar(select(User).where(func.lower(User.email) == ident.email))
        if u is not None:
            u.sso_subject, u.sso_provider = ident.subject, ident.issuer
    if u is None:
        if not settings.oidc_auto_create:
            raise HTTPException(403, "No account for this identity and automatic creation is disabled")
        base = re.sub(r"[^a-z0-9._-]", "", (ident.name or ident.email.split("@")[0]).lower())[:40] or "sso"
        name, i = base, 1
        while db.scalar(select(User).where(func.lower(User.username) == name)):
            i += 1
            name = f"{base}{i}"
        u = User(username=name, display_name=ident.name or name, password_hash=hash_password(secrets.token_urlsafe(24)),
                 role=ident.role or "user", email=ident.email, sso_subject=ident.subject, sso_provider=ident.issuer)
        db.add(u)
        db.flush()
    elif not u.active:
        raise HTTPException(403, "Account is disabled")
    token = _issue(db, u)
    # the token travels in the fragment: browsers never send it to the server in a URL
    return RedirectResponse(f"{settings.public_url or ''}/#/sso?token={urllib.parse.quote(token)}", status_code=302)


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
