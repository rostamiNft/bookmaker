"""OpenID Connect login (authorization code + PKCE).

Works with any compliant provider (Keycloak, Authentik, Zitadel, Okta, Entra ID,
Google, ...): discovery from ``{issuer}/.well-known/openid-configuration``, ID token
signature verified against the provider's JWKS, then the standard claim checks
(issuer, audience, expiry, nonce). Accounts are matched on the ``sub`` claim first and
on a verified e-mail second, so a user keeps their books when their e-mail changes.

PyJWT (MIT) is used for decoding; the keys come from the provider's JWKS, fetched over
HTTPS with a short cache. Nothing about the provider is hard-coded.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.parse
from dataclasses import dataclass

from .config import settings

_CACHE: dict = {}
CACHE_TTL = 300.0


class SsoError(RuntimeError):
    pass


def _get(url: str, timeout: float = 10.0) -> dict:
    from .providers.http_util import request
    r = request("GET", url, timeout=timeout)
    try:
        return r.json()
    except Exception as e:  # noqa: BLE001
        raise SsoError(f"{url} did not return JSON: {e}") from e


def discovery(force: bool = False) -> dict:
    """Provider metadata (cached 5 minutes)."""
    if not settings.oidc_configured:
        raise SsoError("OIDC is not configured (BOOKMAKER_OIDC_ISSUER / _CLIENT_ID)")
    key = ("disc", settings.oidc_issuer)
    if not force and key in _CACHE and time.monotonic() - _CACHE[key][0] < CACHE_TTL:
        return _CACHE[key][1]
    url = f"{settings.oidc_issuer}/.well-known/openid-configuration"
    data = _get(url)
    _CACHE[key] = (time.monotonic(), data)
    return data


def jwks(force: bool = False) -> dict:
    uri = discovery(force=force).get("jwks_uri")
    if not uri:
        raise SsoError("Provider metadata has no jwks_uri")
    key = ("jwks", uri)
    if not force and key in _CACHE and time.monotonic() - _CACHE[key][0] < CACHE_TTL:
        return _CACHE[key][1]
    data = _get(uri)
    _CACHE[key] = (time.monotonic(), data)
    return data


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def new_state() -> tuple[str, str, str]:
    """(state, code_verifier, nonce) for one login attempt."""
    return secrets.token_urlsafe(24), _b64u(secrets.token_bytes(32)), secrets.token_urlsafe(16)


def code_challenge(verifier: str) -> str:
    return _b64u(hashlib.sha256(verifier.encode()).digest())


def authorize_url(state: str, verifier: str, nonce: str, redirect_uri: str) -> str:
    d = discovery()
    auth = d.get("authorization_endpoint")
    if not auth:
        raise SsoError("Provider metadata has no authorization_endpoint")
    q = {"response_type": "code", "client_id": settings.oidc_client_id, "redirect_uri": redirect_uri,
         "scope": settings.oidc_scope, "state": state, "nonce": nonce,
         "code_challenge": code_challenge(verifier), "code_challenge_method": "S256"}
    return f"{auth}{'&' if '?' in auth else '?'}{urllib.parse.urlencode(q)}"


@dataclass
class Identity:
    subject: str
    email: str = ""
    name: str = ""
    email_verified: bool = False
    role: str = ""
    issuer: str = ""

    def as_dict(self) -> dict:
        return {"subject": self.subject, "email": self.email, "name": self.name,
                "email_verified": self.email_verified, "role": self.role, "issuer": self.issuer}


def exchange_code(code: str, verifier: str, redirect_uri: str) -> dict:
    """POST the code at the token endpoint. Returns the raw token response."""
    d = discovery()
    ep = d.get("token_endpoint")
    if not ep:
        raise SsoError("Provider metadata has no token_endpoint")
    from .providers.http_util import request
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "client_id": settings.oidc_client_id, "code_verifier": verifier}
    if settings.oidc_client_secret:
        data["client_secret"] = settings.oidc_client_secret
    r = request("POST", ep, data=urllib.parse.urlencode(data),
                headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=20)
    try:
        return r.json()
    except Exception as e:  # noqa: BLE001
        raise SsoError(f"Token endpoint did not return JSON: {e}") from e


def verify_id_token(id_token: str, nonce: str = "") -> Identity:
    """Verify signature (JWKS), issuer, audience, expiry and nonce, then build the identity."""
    try:
        import jwt
        from jwt import PyJWKClient
    except ImportError as e:  # pragma: no cover - optional dependency
        raise SsoError("PyJWT is not installed (pip install PyJWT)") from e
    d = discovery()
    issuer = d.get("issuer") or settings.oidc_issuer
    keys = jwks()
    try:
        signing = PyJWKClient.__new__(PyJWKClient)  # we already fetched the JWKS: verify offline
        from jwt.algorithms import RSAAlgorithm, ECAlgorithm
        header = jwt.get_unverified_header(id_token)
        kid = header.get("kid")
        jwk = next((k for k in keys.get("keys", []) if k.get("kid") == kid or len(keys.get("keys", [])) == 1), None)
        if jwk is None:
            # refresh once: the provider may have rotated its keys
            jwk = next((k for k in jwks(force=True).get("keys", []) if k.get("kid") == kid), None)
        if jwk is None:
            raise SsoError(f"No key {kid!r} in the provider JWKS")
        alg = RSAAlgorithm if jwk.get("kty") == "RSA" else ECAlgorithm
        key = alg.from_jwk(json.dumps(jwk))
        del signing
        claims = jwt.decode(id_token, key, algorithms=[header.get("alg", "RS256")],
                            audience=settings.oidc_client_id, issuer=issuer,
                            options={"require": ["exp", "aud", "iss"]})
    except SsoError:
        raise
    except Exception as e:  # noqa: BLE001
        raise SsoError(f"ID token rejected: {type(e).__name__}: {e}") from e
    if nonce and claims.get("nonce") not in (None, nonce):
        raise SsoError("ID token nonce does not match (possible replay)")
    sub = claims.get("sub")
    if not sub:
        raise SsoError("ID token has no 'sub' claim")
    role = ""
    if settings.oidc_role_claim:
        raw = claims.get(settings.oidc_role_claim)
        if isinstance(raw, list):
            raw = raw[0] if raw else ""
        role = "admin" if str(raw).lower() in ("admin", "administrator", "bookmaker-admin") else ("user" if raw else "")
    return Identity(subject=f"{issuer}|{sub}", email=(claims.get("email") or "").lower(),
                    name=claims.get("name") or claims.get("preferred_username") or "",
                    email_verified=bool(claims.get("email_verified")), role=role, issuer=issuer)


def provider_label() -> str:
    return settings.oidc_label or settings.oidc_issuer
