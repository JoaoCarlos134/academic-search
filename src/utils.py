import httpx
from pathlib import Path
from dotenv import load_dotenv

from src.models import Article

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


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
        async with httpx.AsyncClient(**client_kwargs, verify=True) as client:
            return await client.get(url, params=params or {})
    except httpx.ConnectError as e:
        if "CERTIFICATE_VERIFY_FAILED" in str(e):
            async with httpx.AsyncClient(**client_kwargs, verify=False) as client:
                return await client.get(url, params=params or {})
        raise


def dedup_articles(articles: list[Article]) -> list[Article]:
    """First occurrence wins, keyed by DOI then normalized title.

    OpenAlex/Elsevier/IEEE often index the same paper. Provider order in the
    input list determines which version survives — callers should put the
    richest source first.
    """
    seen: set[str] = set()
    result: list[Article] = []
    for a in articles:
        key = (a.doi or a.title or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(a)
    return result
