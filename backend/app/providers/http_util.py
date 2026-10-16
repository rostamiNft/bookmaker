from __future__ import annotations

import httpx

from ..logging_setup import redact
from .base import AuthError, ConfigError, ProviderError, RateLimitError, TransientError


def raise_for_response(r: httpx.Response) -> None:
    if r.status_code < 400:
        return
    body = redact(r.text[:400])
    if r.status_code in (401, 403):
        raise AuthError(f"Authentication failed ({r.status_code}): {body}", r.status_code)
    if r.status_code == 429:
        ra = r.headers.get("retry-after")
        try:
            wait = float(ra) if ra else None
        except ValueError:
            wait = None
        raise RateLimitError(f"Rate limited (429): {body}", 429, wait)
    if r.status_code in (404, 400, 422):
        raise ProviderError(f"Request rejected ({r.status_code}): {body}", r.status_code)
    if r.status_code >= 500 or r.status_code in (408, 409):
        raise TransientError(f"Server error ({r.status_code}): {body}", r.status_code)
    raise ProviderError(f"HTTP {r.status_code}: {body}", r.status_code)


def request(method: str, url: str, timeout: float = 120, **kw) -> httpx.Response:
    if not url.startswith(("http://", "https://")):
        raise ConfigError(f"Invalid base URL: {url!r}")
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as c:
            r = c.request(method, url, **kw)
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        raise TransientError(f"Cannot connect to {url.split('?')[0]}: {e.__class__.__name__}") from e
    except httpx.TimeoutException as e:
        raise TransientError(f"Timeout calling {url.split('?')[0]}") from e
    except httpx.HTTPError as e:
        raise TransientError(f"HTTP error calling {url.split('?')[0]}: {e.__class__.__name__}") from e
    raise_for_response(r)
    return r
