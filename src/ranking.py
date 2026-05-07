import math as math

from datetime import datetime

from src.models import Article

CURRENT_YEAR = datetime.now().year

SOURCE_WEIGHTS = {
    "elsevier": 1.0,
    "ieee": 1.0,
    "openalex": 0.9,
    "mdpi": 0.8,
}

WEIGHTS = {
    "similarity": 0.50,
    "recency": 0.20,
    "source": 0.15,
    "citations": 0.15,
}


def score_articles(articles: list[Article], query: str) -> list[Article]:
    if not articles:
        return []

    # calcula scores brutos
    similarities = _compute_similarity(articles, query)
    recencies = [_recency_score(a.year) for a in articles]
    sources = [SOURCE_WEIGHTS.get(a.source, 0.7) for a in articles]
    citations = _normalize_citations([a.citations for a in articles])

    for i, article in enumerate(articles):
        article.score = (
            WEIGHTS["similarity"] * similarities[i]
            + WEIGHTS["recency"] * recencies[i]
            + WEIGHTS["source"] * sources[i]
            + WEIGHTS["citations"] * citations[i]
        )

    return sorted(articles, key=lambda a: a.score, reverse=True)


def _compute_similarity(articles: list[Article], query: str) -> list[float]:
    """Similaridade simples por palavras-chave (sem GPU necessário)."""
    query_words = set(query.lower().split())
    scores = []
    for article in articles:
        text = f"{article.title} {article.abstract or ''}".lower()
        text_words = set(text.split())
        if not text_words:
            scores.append(0.0)
            continue
        overlap = len(query_words & text_words)
        scores.append(overlap / len(query_words))

    # normaliza entre 0 e 1
    max_s = max(scores) if max(scores) > 0 else 1
    return [s / max_s for s in scores]


def _recency_score(year: int | None) -> float:
    if not year:
        return 0.3
    age = CURRENT_YEAR - year
    return math.exp(-age / 5)


def _normalize_citations(citations: list[int]) -> list[float]:
    log_cites = [math.log1p(c) for c in citations]
    max_c = max(log_cites) if max(log_cites) > 0 else 1
    return [c / max_c for c in log_cites]
