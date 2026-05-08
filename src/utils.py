import httpx
from pathlib import Path
from dotenv import load_dotenv

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
