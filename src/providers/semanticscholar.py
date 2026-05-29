import asyncio
import logging
import os

from dotenv import load_dotenv

from src.models import Article
from src.utils import http_get

load_dotenv()
logger = logging.getLogger(__name__)

BASE_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
_FIELDS = (
    "title,year,abstract,authors,venue,externalIds,"
    "citationCount,openAccessPdf,publicationTypes,journal"
)


async def search(query: str, limit: int = 20) -> list[Article]:
    api_key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")
    if not api_key:
        logger.debug("SEMANTIC_SCHOLAR_API_KEY not set; proceeding anonymously.")

    # Avoid hitting the anonymous rate limit when running alongside other providers.
    await asyncio.sleep(0.2)

    headers = {}
    if api_key:
        headers["x-api-key"] = api_key

    params = {"query": query, "limit": limit, "fields": _FIELDS}
    response = await http_get(BASE_URL, params=params, headers=headers or None)
    if response.status_code == 429:
        logger.warning("Semantic Scholar rate limit hit (429); returning empty result.")
        return []
    response.raise_for_status()
    data = response.json()

    articles = []
    for item in data.get("data", []):
        doi = (item.get("externalIds") or {}).get("DOI")
        oa_pdf = item.get("openAccessPdf") or {}
        url = (
            f"https://doi.org/{doi}"
            if doi
            else f"https://www.semanticscholar.org/paper/{item.get('paperId', '')}"
        )
        venue_raw = item.get("venue") or (item.get("journal") or {}).get("name") or ""
        articles.append(
            Article(
                title=item.get("title") or "",
                year=item.get("year"),
                doi=doi,
                abstract=item.get("abstract") or "",
                authors=[a["name"] for a in item.get("authors", []) if a.get("name")][:5],
                venue=venue_raw or None,
                source="semanticscholar",
                url=url,
                citations=item.get("citationCount") or 0,
                score=0.0,
                venue_type=_map_venue_type(item.get("publicationTypes") or []),
                publisher=None,
                is_oa=oa_pdf.get("url") is not None,
                oa_url=oa_pdf.get("url"),
                concepts=None,
            )
        )
    return articles


def _map_venue_type(types: list[str]) -> str:
    for t in types:
        if t == "JournalArticle":
            return "journal"
        if t == "Conference":
            return "conference"
        if t in ("Repository", "Preprint"):
            return "repository"
    return "other"
