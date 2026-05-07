import httpx
from src.models import Article
from typing import Optional

BASE_URL = "https://api.openalex.org/works"
EMAIL = "seu@email.com"  # coloque seu email aqui (melhora rate limit)


async def search(query: str, limit: int = 20) -> list[Article]:
    params = {
        "search": query,
        "per-page": limit,
        "select": "title,publication_year,doi,abstract_inverted_index,authorships,primary_location,cited_by_count",
        "mailto": EMAIL,
    }

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(BASE_URL, params=params)
        response.raise_for_status()
        data = response.json()

    articles = []
    for work in data.get("results", []):
        articles.append(
            Article(
                title=work.get("title") or "",
                year=work.get("publication_year"),
                doi=work.get("doi"),
                abstract=_reconstruct_abstract(work.get("abstract_inverted_index")),
                authors=_extract_authors(work.get("authorships", [])),
                venue=_extract_venue(work.get("primary_location")),
                source="openalex",
                url=work.get("doi"),
                citations=work.get("cited_by_count", 0),
            )
        )

    return articles


def _reconstruct_abstract(inverted: Optional[dict]) -> Optional[str]:
    if not inverted:
        return None
    words = {}
    for word, positions in inverted.items():
        for pos in positions:
            words[pos] = word
    return " ".join(words[i] for i in sorted(words))


def _extract_authors(authorships: list) -> list[str]:
    return [
        a["author"]["display_name"]
        for a in authorships
        if a.get("author", {}).get("display_name")
    ][:5]  # máximo 5 autores


def _extract_venue(location: Optional[dict]) -> Optional[str]:
    if not location:
        return None
    source = location.get("source")
    if source:
        return source.get("display_name")
    return None


# fix: adicionar Optional no impor
