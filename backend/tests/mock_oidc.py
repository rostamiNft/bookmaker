"""A tiny but real OpenID Connect provider for the tests.

It serves discovery + JWKS, signs ID tokens with an RSA key (RS256) and exchanges an
authorization code, so the login flow in :mod:`app.sso` is exercised end to end -
including signature and nonce verification - without any external service.
"""
from __future__ import annotations

import base64
import json
import socket
import threading
import time
from urllib.parse import parse_qs, urlparse

import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


class Provider:
    """Signed ID tokens for a configurable set of claims."""

    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "test-key-1"
        self.state: dict = {"claims": {"sub": "user-123", "email": "mila@example.com", "email_verified": True,
                                      "name": "Mila Mouse", "preferred_username": "mila"},
                            "codes": {}, "fail_token": False, "bad_nonce": False}

    def jwks(self) -> dict:
        nums = self.key.public_key().public_numbers()
        n, e = nums.n, nums.e
        return {"keys": [{"kty": "RSA", "kid": self.kid, "use": "sig", "alg": "RS256",
                          "n": _b64u(n.to_bytes((n.bit_length() + 7) // 8, "big")),
                          "e": _b64u(e.to_bytes((e.bit_length() + 7) // 8, "big"))}]}

    def sign(self, claims: dict) -> str:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        header = {"alg": "RS256", "typ": "JWT", "kid": self.kid}
        parts = [_b64u(json.dumps(header).encode()), _b64u(json.dumps(claims).encode())]
        signing_input = f"{parts[0]}.{parts[1]}".encode()
        sig = self.key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
        return f"{parts[0]}.{parts[1]}.{_b64u(sig)}"

    def id_token(self, nonce: str = "", issuer: str = "", audience: str = "", extra: dict | None = None) -> str:
        now = int(time.time())
        claims = {"iss": issuer, "aud": audience, "sub": self.state["claims"]["sub"], "iat": now, "exp": now + 300,
                  "email": self.state["claims"].get("email", ""),
                  "email_verified": self.state["claims"].get("email_verified", False),
                  "name": self.state["claims"].get("name", "")}
        if nonce:
            claims["nonce"] = "wrong-nonce" if self.state.get("bad_nonce") else nonce
        claims.update(extra or {})
        return self.sign(claims)


def build_app(prov: Provider) -> FastAPI:
    app = FastAPI()

    @app.get("/.well-known/openid-configuration")
    def discovery(request: Request):
        base = str(request.base_url).rstrip("/")
        return {"issuer": base, "authorization_endpoint": f"{base}/authorize",
                "token_endpoint": f"{base}/token", "jwks_uri": f"{base}/jwks",
                "response_types_supported": ["code"], "id_token_signing_alg_values_supported": ["RS256"]}

    @app.get("/jwks")
    def jwks():
        return prov.jwks()

    @app.get("/authorize")
    def authorize(request: Request, state: str = "", redirect_uri: str = "", nonce: str = "",
                  code_challenge: str = "", code_challenge_method: str = ""):
        code = "code-" + base64.urlsafe_b64encode(str(time.time()).encode()).decode().rstrip("=")
        prov.state["codes"][code] = {"nonce": nonce, "challenge": code_challenge}
        sep = "&" if "?" in redirect_uri else "?"
        return RedirectResponse(f"{redirect_uri}{sep}code={code}&state={state}")

    @app.post("/token")
    def token(request: Request, code: str = Form(default=""), code_verifier: str = Form(default=""),
              grant_type: str = Form(default=""), client_id: str = Form(default="")):
        if prov.state.get("fail_token"):
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        rec = prov.state["codes"].get(code) or {}
        # PKCE check: S256(code_verifier) must equal the challenge sent to /authorize
        import hashlib
        if rec.get("challenge") and code_verifier:
            calc = _b64u(hashlib.sha256(code_verifier.encode()).digest())
            if calc != rec["challenge"]:
                return JSONResponse({"error": "invalid_grant", "error_description": "PKCE mismatch"}, status_code=400)
        issuer = str(request.base_url).rstrip("/")
        aud = client_id or parse_qs(urlparse(str(request.url)).query).get("client_id", [""])[0] or "bookmaker-test"
        body = prov.id_token(rec.get("nonce", ""), issuer, aud)
        return {"access_token": "at-" + code, "token_type": "Bearer", "expires_in": 300, "id_token": body}
    return app


class Server:
    def __init__(self):
        self.provider = Provider()
        self.app = build_app(self.provider)
        self.port = 0
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> "Server":
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.port = sock.getsockname()[1]
        sock.close()
        config = uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="error")
        srv = uvicorn.Server(config)
        self._thread = threading.Thread(target=srv.run, daemon=True)
        self._thread.start()
        for _ in range(200):
            try:
                import urllib.request
                urllib.request.urlopen(f"{self.url}/jwks", timeout=1).read()
                return self
            except Exception:
                time.sleep(0.05)
        raise RuntimeError("mock OIDC server did not start")


def start() -> Server:
    return Server().start()
