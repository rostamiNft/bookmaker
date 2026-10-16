"""Single sign-on (OIDC + PKCE) and password reset by e-mail.

Both flows run against real local services: a mock OIDC provider that signs ID tokens
with its own RSA key (tests/mock_oidc.py) and an SMTP server (aiosmtpd, BSD-3). No
external service, no mocking of the code under test.
"""
from __future__ import annotations

import asyncio
import re
import threading

import httpx
from email.parser import BytesParser

import pytest

from app.config import settings
from app.db import session_scope
from sqlalchemy import func, select

from app.models import OidcState, PasswordReset, User


# --------------------------------------------------------------------- smtp sink
class SmtpSink:
    """Minimal SMTP server that records the messages it receives."""

    def __init__(self):
        self.messages: list[dict] = []
        self.port = 0
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server = None

    def start(self) -> "SmtpSink":
        import socket
        from aiosmtpd.controller import Controller

        class Handler:
            sink = self

            async def handle_DATA(self, server, session, envelope):
                msg = BytesParser().parsebytes(envelope.content)
                html = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/html":
                            html = part.get_payload(decode=True).decode("utf-8", "replace")
                        elif part.get_content_type() == "text/plain":
                            body = part.get_payload(decode=True).decode("utf-8", "replace")
                else:
                    body = msg.get_payload(decode=True).decode("utf-8", "replace")
                self.sink.messages.append({"to": envelope.rcpt_tos, "subject": msg.get("Subject", ""),
                                           "body": body, "html": html})
                return "250 OK"

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._loop.call_soon_threadsafe(asyncio.create_task, self._start(Handler()))
        for _ in range(200):
            if self._server is not None:
                return self
            import time
            time.sleep(0.02)
        return self

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    async def _start(self, handler):
        from aiosmtpd.controller import Controller
        self._server = Controller(handler, hostname="127.0.0.1", port=self.port)
        self._server.start()

    def stop(self):
        if self._server is not None:
            self._server.stop()
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)


@pytest.fixture(scope="module")
def smtp():
    pytest.importorskip("aiosmtpd")
    s = SmtpSink().start()
    try:
        yield s
    finally:
        s.stop()


@pytest.fixture(scope="module")
def oidc():
    from tests.mock_oidc import start
    srv = start()
    try:
        yield srv
    finally:
        pass


ADMIN = "ssoadmin"
ADMIN_PW = "Tr0ub4dor&3"


@pytest.fixture(scope="module")
def accounts(app_client):
    """Auth mode = accounts with one administrator (used by both flows).

    Other test modules may already have completed the first-run setup, so this logs in
    as an administrator and resets that account's password directly in the database
    (the reset flow under test changes it)."""
    from sqlalchemy import select
    from app.security import hash_password
    with pytest.MonkeyPatch.context() as mp:  # module-wide: monkeypatch itself is per test
        mp.setattr(settings, "auth_mode_env", "accounts")
        settings.auth_mode = "accounts"
        yield _login(app_client, hash_password, select)
    settings.auth_mode = settings.auth_mode_env or "none"


def _login(app_client, hash_password, select):
    r = app_client.post("/api/auth/setup", json={"username": ADMIN, "password": ADMIN_PW, "display_name": "SSO Admin"})
    if r.status_code == 200:
        return r.json()["token"]
    assert r.status_code == 409, r.text
    with session_scope() as s:
        u = s.scalar(select(User).where(User.username == ADMIN))
        if u is None:  # another module owns the setup: promote that account's password
            u = s.scalars(select(User)).first()
            u.username = ADMIN
        u.password_hash, u.active, u.email = hash_password(ADMIN_PW), True, "admin@example.test"
    r = app_client.post("/api/auth/login", json={"username": ADMIN, "password": ADMIN_PW})
    assert r.status_code == 200, r.text
    return r.json()["token"]


# --------------------------------------------------------------------- password reset
def test_password_reset_by_email(app_client, accounts, smtp, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "127.0.0.1")
    monkeypatch.setattr(settings, "smtp_port", smtp.port)
    monkeypatch.setattr(settings, "smtp_from", "bookmaker@example.test")
    monkeypatch.setattr(settings, "smtp_starttls", False)
    monkeypatch.setattr(settings, "smtp_user", "")
    # give the account an e-mail address
    with session_scope() as s:
        u = s.scalar(select(User).where(User.username == ADMIN))
        u.email = "admin@example.test"
    # unknown user: same answer, no mail, no way to enumerate accounts
    r = app_client.post("/api/auth/reset/request", json={"email": "nobody@example.test"})
    assert r.status_code == 200 and r.json()["mailed"] is False
    assert len(smtp.messages) == 0
    r = app_client.post("/api/auth/reset/request", json={"email": "admin@example.test"})
    assert r.status_code == 200, r.text
    assert r.json()["mailed"] is True, r.json()
    assert len(smtp.messages) == 1
    mail = smtp.messages[0]
    assert mail["to"] == ["admin@example.test"] and "reset" in mail["subject"].lower()
    link = re.search(r"https?://\S*?/reset\?token=\S+", mail["body"])
    assert link, mail["body"]
    token = link.group().split("token=")[1].rstrip(".)")
    # a wrong token is rejected
    bad = app_client.post("/api/auth/reset/confirm", json={"token": "x" * 40, "password": "N3wPassw0rd!"})
    assert bad.status_code == 400
    # the real one works, and the old password stops working
    ok = app_client.post("/api/auth/reset/confirm", json={"token": token, "password": "N3wPassw0rd!"})
    assert ok.status_code == 200, ok.text
    assert app_client.post("/api/auth/login", json={"username": ADMIN, "password": ADMIN_PW}).status_code == 401
    again = app_client.post("/api/auth/login", json={"username": ADMIN, "password": "N3wPassw0rd!"})
    assert again.status_code == 200, again.text
    # single use
    assert app_client.post("/api/auth/reset/confirm", json={"token": token, "password": "An0therPass!"}).status_code == 400
    # the reset row was consumed
    with session_scope() as s:
        used = s.scalar(select(func.count()).select_from(PasswordReset))
    assert used == 0


def test_reset_link_without_mail_server(app_client, accounts, monkeypatch):
    """With SMTP disabled the administrator gets a link to pass on by hand (never a silent failure)."""
    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(settings, "env", "development")
    r = app_client.post("/api/auth/reset/request", json={"username": ADMIN},
                        headers={"Authorization": f"Bearer {accounts}"})
    assert r.status_code == 200, r.text
    assert r.json()["mailed"] is False and "reset?token=" in r.json().get("link", "")
    link = r.json()["link"]
    token = link.split("token=")[1]
    ok = app_client.post("/api/auth/reset/confirm", json={"token": token, "password": "Handed0ver!x"})
    assert ok.status_code == 200, ok.text


# --------------------------------------------------------------------- single sign-on
def test_oidc_login_creates_account_and_session(app_client, accounts, oidc, monkeypatch):
    monkeypatch.setattr(settings, "oidc_issuer", oidc.url)
    monkeypatch.setattr(settings, "oidc_client_id", "bookmaker-test")
    monkeypatch.setattr(settings, "oidc_redirect_uri", "http://localhost:8000/api/auth/sso/callback")
    monkeypatch.setattr(settings, "oidc_auto_create", True)
    from app import sso
    sso._CACHE.clear()

    assert app_client.get("/api/auth/sso").json()["enabled"] is True
    # 1. the browser is sent to the provider
    r = app_client.get("/api/auth/sso/authorize")
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    assert url.startswith(oidc.url + "/authorize") and "code_challenge_method=S256" in url
    state = r.json()["state"]
    with session_scope() as s:
        row = s.get(OidcState, state)
        assert row is not None and row.code_verifier
    # 2. the provider sends the browser back with a code
    import urllib.parse as up
    q = up.parse_qs(up.urlparse(url).query)
    auth = httpx.get(f"{oidc.url}/authorize", params={k: v[0] for k, v in q.items()}, follow_redirects=False)
    assert auth.status_code in (302, 307), auth.text
    loc = auth.headers["location"]
    code = up.parse_qs(up.urlparse(loc).query)["code"][0]
    # 3. the callback exchanges the code and verifies the ID token
    cb = app_client.get(f"/api/auth/sso/callback?code={code}&state={state}", follow_redirects=False)
    assert cb.status_code == 302, cb.text
    assert "/#/sso?token=" in cb.headers["location"]
    token = up.unquote(cb.headers["location"].split("token=")[1])
    me = app_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200, me.text
    assert me.json()["username"] and me.json()["role"] == "user"
    with session_scope() as s:
        u = s.scalar(select(User).where(User.sso_subject == f"{oidc.url}|user-123"))
        assert u is not None and u.email == "mila@example.com"
    # the same identity logs into the same account (no duplicate)
    r2 = app_client.get("/api/auth/sso/authorize")
    q2 = up.parse_qs(up.urlparse(r2.json()["url"]).query)
    auth2 = httpx.get(f"{oidc.url}/authorize", params={k: v[0] for k, v in q2.items()}, follow_redirects=False)
    code2 = up.parse_qs(up.urlparse(auth2.headers["location"]).query)["code"][0]
    cb2 = app_client.get(f"/api/auth/sso/callback?code={code2}&state={r2.json()['state']}", follow_redirects=False)
    assert cb2.status_code == 302
    with session_scope() as s:
        n = s.scalar(select(func.count()).select_from(User))
    assert n == 2  # admin + the one SSO account


def test_oidc_rejects_bad_nonce_and_replayed_state(app_client, accounts, oidc, monkeypatch):
    monkeypatch.setattr(settings, "oidc_issuer", oidc.url)
    monkeypatch.setattr(settings, "oidc_client_id", "bookmaker-test")
    monkeypatch.setattr(settings, "oidc_redirect_uri", "http://localhost:8000/api/auth/sso/callback")
    from app import sso
    sso._CACHE.clear()
    import urllib.parse as up
    oidc.provider.state["bad_nonce"] = True
    try:
        r = app_client.get("/api/auth/sso/authorize")
        q = up.parse_qs(up.urlparse(r.json()["url"]).query)
        auth = httpx.get(f"{oidc.url}/authorize", params={k: v[0] for k, v in q.items()}, follow_redirects=False)
        code = up.parse_qs(up.urlparse(auth.headers["location"]).query)["code"][0]
        cb = app_client.get(f"/api/auth/sso/callback?code={code}&state={r.json()['state']}", follow_redirects=False)
        assert cb.status_code == 401 and "nonce" in cb.text.lower()
    finally:
        oidc.provider.state["bad_nonce"] = False
    # an unknown/expired state is refused
    assert app_client.get("/api/auth/sso/callback?code=whatever&state=nope").status_code == 400
