import os
from typing import Optional

import httpx
from dotenv import load_dotenv

from src.models import Article

load_dotenv()

BASE_URL = "https://ieeexploreapi.ieee.org/api/v1/search/articles"
_API_KEY = os.getenv("IEEE_API_KEY", "")


async def search(query: str, limit: int = 20) -> list[Article]:
    if not _API_KEY:
        return []

    params = {
        "apikey": _API_KEY,
        "querytext": query,
        "max_records": limit,
        "format": "json",
    }

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(BASE_URL, params=params)
        response.raise_for_status()
        data = response.json()

    articles = []
    for entry in data.get("articles", []):
        articles.append(
            Article(
                title=entry.get("title") or "",
                year=entry.get("publication_year"),
                doi=entry.get("doi"),
                abstract=entry.get("abstract"),
                authors=_extract_authors(entry.get("authors", {})),
                venue=entry.get("publication_title"),
                source="ieee",
                url=entry.get("html_url"),
                citations=entry.get("citing_paper_count", 0) or 0,
            )
        )

    return articles


def _extract_authors(authors_block: Optional[dict]) -> list[str]:
    if not authors_block:
        return []
    return [
        a["full_name"]
        for a in authors_block.get("authors", [])
        if a.get("full_name")
    ][:5]
