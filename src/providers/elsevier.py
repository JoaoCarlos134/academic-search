import os
from datetime import date
from typing import Optional

from dotenv import load_dotenv

from src.models import Article
from src.utils import http_get

load_dotenv()

BASE_URL = "https://api.elsevier.com/content/search/scopus"
_API_KEY = os.getenv("ELSEVIER_API_KEY", "")
_INST_TOKEN = os.getenv("ELSEVIER_INST_TOKEN", "")

_VENUE_TYPE_MAP = {
    "journal": "journal",
    "trade journal": "journal",
    "conference proceeding": "conference",
    "book series": "book",
}


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
        "field": (
            "dc:title,prism:doi,prism:coverDate,dc:description,"
            "author,prism:publicationName,citedby-count,prism:url,"
            "subtypeDescription,openaccess"
        ),
    }

    response = await http_get(BASE_URL, params=params, headers=headers)
    response.raise_for_status()
    data = response.json()

    articles = []
    for entry in data.get("search-results", {}).get("entry", []):
        citations = int(entry.get("citedby-count") or 0)
        if citations == 0:
            continue
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
                citations=citations,
                venue_type=_extract_venue_type(entry.get("subtypeDescription")),
                publisher=None,
                is_oa=_extract_oa(entry.get("openaccess")),
                oa_url=None,
                concepts=None,
            )
        )

    return articles


def _extract_year(cover_date: Optional[str]) -> Optional[int]:
    if not cover_date:
        return None
    try:
        year = int(cover_date[:4])
    except (ValueError, IndexError):
        return None
    current_year = date.today().year
    return year if 1000 <= year <= current_year + 2 else None


def _extract_authors(authors: list) -> list[str]:
    return [a["authname"] for a in authors if a.get("authname")][:5]


def _extract_venue_type(subtype: Optional[str]) -> str:
    if not subtype:
        return "other"
    return _VENUE_TYPE_MAP.get(subtype.lower(), "other")


def _extract_oa(raw: Optional[str | int]) -> Optional[bool]:
    if raw is None:
        return None
    return str(raw) == "1"
