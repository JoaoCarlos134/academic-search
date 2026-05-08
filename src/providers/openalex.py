from typing import Optional

from src.models import Article
from src.utils import http_get

BASE_URL = "https://api.openalex.org/works"
EMAIL = "seu@email.com"  # coloque seu email aqui (melhora rate limit)

_VENUE_TYPE_MAP = {
    "journal": "journal",
    "conference": "conference",
    "repository": "repository",
    "book series": "book",
}


async def search(query: str, limit: int = 20) -> list[Article]:
    params = {
        "search": query,
        "per-page": limit,
        "select": (
            "title,publication_year,doi,abstract_inverted_index,"
            "authorships,primary_location,cited_by_count,"
            "open_access,concepts"
        ),
        "mailto": EMAIL,
    }

    response = await http_get(BASE_URL, params=params)
    response.raise_for_status()
    data = response.json()

    articles = []
    for work in data.get("results", []):
        location = work.get("primary_location") or {}
        oa = work.get("open_access") or {}
        articles.append(
            Article(
                title=work.get("title") or "",
                year=work.get("publication_year"),
                doi=work.get("doi"),
                abstract=_reconstruct_abstract(work.get("abstract_inverted_index")),
                authors=_extract_authors(work.get("authorships", [])),
                venue=_extract_venue_name(location),
                source="openalex",
                url=work.get("doi"),
                citations=work.get("cited_by_count", 0),
                venue_type=_extract_venue_type(location),
                publisher=_extract_publisher(location),
                is_oa=oa.get("is_oa"),
                oa_url=oa.get("oa_url"),
                concepts=_extract_concepts(work.get("concepts", [])),
            )
        )

    return articles


def _reconstruct_abstract(inverted: Optional[dict]) -> Optional[str]:
    if not inverted:
        return None
    positions: dict[int, str] = {}
    for word, pos_list in inverted.items():
        for pos in pos_list:
            positions[pos] = word
    return " ".join(positions[i] for i in sorted(positions))


def _extract_authors(authorships: list) -> list[str]:
    return [
        a["author"]["display_name"]
        for a in authorships
        if a.get("author", {}).get("display_name")
    ][:5]


def _extract_venue_name(location: dict) -> Optional[str]:
    source = location.get("source") or {}
    return source.get("display_name")


def _extract_venue_type(location: dict) -> str:
    source = location.get("source") or {}
    raw = (source.get("type") or "").lower()
    return _VENUE_TYPE_MAP.get(raw, "other")


def _extract_publisher(location: dict) -> Optional[str]:
    source = location.get("source") or {}
    return source.get("host_organization_name")


def _extract_concepts(concepts: list) -> Optional[list[str]]:
    names = [c["display_name"] for c in concepts[:5] if c.get("display_name")]
    return names if names else None
