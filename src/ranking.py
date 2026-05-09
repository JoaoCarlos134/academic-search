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

# In-process cache: maps DOI-or-title key → embedding tensor.
# Persists for the lifetime of the process (one Streamlit session).
# Avoids re-encoding the same article on every search interaction.
_embedding_cache: dict = {}


def _get_model():
    global _model, _model_loaded
    if _model_loaded:
        return _model
    try:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("all-MiniLM-L6-v2")
    except Exception as exc:
        print(f"[ranking] sentence-transformers unavailable, falling back to keyword similarity: {exc}")
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

    base_recency = WEIGHTS["recency"]
    base_citations = WEIGHTS["citations"]
    swap = base_recency + base_citations  # combined budget that may be redistributed

    for i, article in enumerate(articles):
        age = CURRENT_YEAR - (article.year or CURRENT_YEAR)
        # Recent papers (≤3 years) haven't had time to accrue citations,
        # so favor recency. Older papers reverse the priority.
        if age <= 3:
            w_recency = swap * 0.75
            w_citations = swap * 0.25
        else:
            w_recency = base_recency
            w_citations = base_citations
        article.score = (
            WEIGHTS["similarity"] * similarities[i]
            + w_recency * recencies[i]
            + WEIGHTS["source"] * sources[i]
            + w_citations * citations[i]
        )

    return sorted(articles, key=lambda a: a.score, reverse=True)


def _compute_similarity(articles: list[Article], query: str) -> list[float]:
    model = _get_model()
    if model is not None:
        return _embedding_similarity(articles, query, model)
    return _keyword_similarity(articles, query)


def _article_cache_key(article: Article) -> str:
    return article.doi or article.title or ""


def _embedding_similarity(articles: list[Article], query: str, model) -> list[float]:
    # Encode only articles not already in the in-process cache
    miss_indices = [i for i, a in enumerate(articles) if _article_cache_key(a) not in _embedding_cache]
    if miss_indices:
        miss_texts = [f"{articles[i].title} {articles[i].abstract or ''}" for i in miss_indices]
        miss_embs = model.encode(miss_texts, convert_to_tensor=True)
        for i, emb in zip(miss_indices, miss_embs):
            _embedding_cache[_article_cache_key(articles[i])] = emb

    doc_embs = [_embedding_cache[_article_cache_key(a)] for a in articles]
    query_emb = model.encode([query], convert_to_tensor=True)[0]
    scores = [float(model.similarity(query_emb, d)) for d in doc_embs]
    # similarity() returns [-1, 1]; shift to [0, 1]
    scores = [(s + 1) / 2 for s in scores]
    max_s = max(scores) if max(scores) > 0 else 1
    return [s / max_s for s in scores]


def _keyword_similarity(articles: list[Article], query: str) -> list[float]:
    query_words = set(query.lower().split())
    if not query_words:
        return [0.0] * len(articles)
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
