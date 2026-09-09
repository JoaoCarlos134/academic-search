import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

from src.models import Article

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_VERIFY_SSL = os.getenv("HTTPX_VERIFY_SSL", "true").lower() != "false"


async def http_get(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
) -> httpx.Response:
    """GET with automatic SSL fallback for corporate proxy environments."""
    client_kwargs: dict = {"timeout": 15}
    if headers:
        client_kwargs["headers"] = headers
    try:
        async with httpx.AsyncClient(**client_kwargs, verify=_VERIFY_SSL) as client:
            return await client.get(url, params=params or {})
    except httpx.ConnectError as e:
        if _VERIFY_SSL and "CERTIFICATE_VERIFY_FAILED" in str(e):
            async with httpx.AsyncClient(**client_kwargs, verify=False) as client:
                return await client.get(url, params=params or {})
        raise


def http_get_sync(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int = 15,
) -> httpx.Response:
    """Synchronous GET with the same SSL fallback as `http_get`.

    Used by the blocking download paths (Retraction Watch CSV) so they behave
    identically to the async provider calls on corporate proxies.
    """
    client_kwargs: dict = {"timeout": timeout, "follow_redirects": True}
    if headers:
        client_kwargs["headers"] = headers
    try:
        with httpx.Client(**client_kwargs, verify=_VERIFY_SSL) as client:
            return client.get(url, params=params or {})
    except httpx.ConnectError as e:
        if _VERIFY_SSL and "CERTIFICATE_VERIFY_FAILED" in str(e):
            with httpx.Client(**client_kwargs, verify=False) as client:
                return client.get(url, params=params or {})
        raise


def _normalize_doi(doi: str | None) -> str:
    """Strip URL prefix and lowercase. OpenAlex returns full URLs, others bare DOIs."""
    if not doi:
        return ""
    s = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    return s


def dedup_articles(articles: list[Article]) -> list[Article]:
    """First occurrence wins, keyed by normalized DOI then normalized title.

    OpenAlex/Elsevier/IEEE often index the same paper. Provider order in the
    input list determines which version survives — callers should put the
    richest source first.
    """
    seen: set[str] = set()
    result: list[Article] = []
    for a in articles:
        key = _normalize_doi(a.doi) or (a.title or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(a)
    return result
