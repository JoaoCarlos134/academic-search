import os
from typing import Optional

import httpx
from dotenv import load_dotenv

from src.models import Article

load_dotenv()

BASE_URL = "https://api.elsevier.com/content/search/scopus"
_API_KEY = os.getenv("ELSEVIER_API_KEY", "")
_INST_TOKEN = os.getenv("ELSEVIER_INST_TOKEN", "")


async def search(query: str, limit: int = 20) -> list[Article]:
    if not _API_KEY:
        return []

    headers = {
        "X-ELS-APIKey": _API_KEY,
        "X-ELS-Insttoken": _INST_TOKEN,
        "Accept": "application/json",
    }
    params = {
        "query": query,
        "count": limit,
        "field": "dc:title,prism:doi,prism:coverDate,dc:description,author,prism:publicationName,citedby-count,prism:url",
    }

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(BASE_URL, headers=headers, params=params)
        response.raise_for_status()
        data = response.json()

    articles = []
    for entry in data.get("search-results", {}).get("entry", []):
        articles.append(
            Article(
                title=entry.get("dc:title") or "",
                year=_extract_year(entry.get("prism:coverDate")),
                doi=entry.get("prism:doi"),
                abstract=entry.get("dc:description"),
                authors=_extract_authors(entry.get("author", [])),
                venue=entry.get("prism:publicationName"),
                source="elsevier",
                url=entry.get("prism:url"),
                citations=int(entry.get("citedby-count") or 0),
            )
        )

    return articles


def _extract_year(cover_date: Optional[str]) -> Optional[int]:
    if not cover_date:
        return None
    try:
        return int(cover_date[:4])
    except (ValueError, IndexError):
        return None


def _extract_authors(authors: list) -> list[str]:
    return [a["authname"] for a in authors if a.get("authname")][:5]
