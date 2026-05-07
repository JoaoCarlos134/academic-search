import math
from datetime import datetime
from typing import Optional

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

_model = None
_model_loaded = False


def _get_model():
    global _model, _model_loaded
    if _model_loaded:
        return _model
    try:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    except Exception:
        _model = None
    _model_loaded = True
    return _model


def score_articles(articles: list[Article], query: str) -> list[Article]:
    if not articles:
        return []

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
    model = _get_model()
    if model is not None:
        return _embedding_similarity(articles, query, model)
    return _keyword_similarity(articles, query)


def _embedding_similarity(articles: list[Article], query: str, model) -> list[float]:
    texts = [f"{a.title} {a.abstract or ''}" for a in articles]
    query_emb, *doc_embs = model.encode([query] + texts, convert_to_tensor=True)
    scores = [float(model.similarity(query_emb, d)) for d in doc_embs]
    # similarity() returns [-1, 1]; shift to [0, 1]
    scores = [(s + 1) / 2 for s in scores]
    max_s = max(scores) if max(scores) > 0 else 1
    return [s / max_s for s in scores]


def _keyword_similarity(articles: list[Article], query: str) -> list[float]:
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
    max_s = max(scores) if max(scores) > 0 else 1
    return [s / max_s for s in scores]


def _recency_score(year: Optional[int]) -> float:
    if not year:
        return 0.3
    age = CURRENT_YEAR - year
    return math.exp(-age / 5)


def _normalize_citations(citations: list[int]) -> list[float]:
    log_cites = [math.log1p(c) for c in citations]
    max_c = max(log_cites) if max(log_cites) > 0 else 1
    return [c / max_c for c in log_cites]
