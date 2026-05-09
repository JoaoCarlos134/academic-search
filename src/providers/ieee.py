import logging
import os
from typing import Optional

from dotenv import load_dotenv

from src.models import Article
from src.utils import http_get

load_dotenv()
logger = logging.getLogger(__name__)

BASE_URL = "https://ieeexploreapi.ieee.org/api/v1/search/articles"
_API_KEY = os.getenv("IEEE_API_KEY", "")

_CONTENT_TYPE_MAP = {
    "journals": "journal",
    "early access": "journal",
    "conferences": "conference",
    "books": "book",
}


async def search(query: str, limit: int = 20) -> list[Article]:
    if not _API_KEY:
        logger.debug("IEEE_API_KEY not set; skipping IEEE provider.")
        return []

    params = {
        "apikey": _API_KEY,
        "querytext": query,
        "max_records": limit,
        "format": "json",
    }

    response = await http_get(BASE_URL, params=params)
    response.raise_for_status()
    data = response.json()

    articles = []
    for entry in data.get("articles", []):
        is_oa = _extract_oa(entry)
        html_url = entry.get("html_url")
        articles.append(
            Article(
                title=entry.get("title") or "",
                year=_extract_year(entry.get("publication_year")),
                doi=entry.get("doi") or None,
                abstract=entry.get("abstract") or None,
                authors=_extract_authors(entry.get("authors", {})),
                venue=entry.get("publication_title") or None,
                source="ieee",
                url=html_url,
                citations=int(entry.get("citing_paper_count") or 0),
                venue_type=_extract_venue_type(entry.get("content_type")),
                publisher=entry.get("publisher") or "IEEE",
                is_oa=is_oa,
                oa_url=html_url if is_oa else None,
                concepts=_extract_concepts(entry.get("index_terms")),
            )
        )

    return articles


def _extract_year(raw) -> Optional[int]:
    from datetime import date
    try:
        year = int(raw)
    except (TypeError, ValueError):
        return None
    return year if 1000 <= year <= date.today().year + 2 else None


def _extract_authors(authors_block: Optional[dict]) -> list[str]:
    if not authors_block:
        return []
    return [
        a["full_name"]
        for a in authors_block.get("authors", [])
        if a.get("full_name")
    ][:5]


def _extract_venue_type(content_type: Optional[str]) -> str:
    if not content_type:
        return "other"
    return _CONTENT_TYPE_MAP.get(content_type.lower(), "other")


def _extract_oa(entry: dict) -> Optional[bool]:
    flag = entry.get("open_access_flag")
    if flag is not None:
        return str(flag).upper() == "T"
    raw = entry.get("is_open_access")
    if raw is None:
        return None
    return bool(raw)


def _extract_concepts(index_terms: Optional[dict]) -> Optional[list[str]]:
    if not index_terms:
        return None
    terms: list[str] = []
    for section in ("ieee_terms", "author_terms"):
        terms += index_terms.get(section, {}).get("terms", [])
    unique = list(dict.fromkeys(terms))[:5]
    return unique if unique else None
