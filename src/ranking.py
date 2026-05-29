import logging
import math
from datetime import datetime
from typing import Optional

from src.models import Article

logger = logging.getLogger(__name__)

CURRENT_YEAR = datetime.now().year

SOURCE_WEIGHTS = {
    "elsevier": 1.0,
    "ieee": 1.0,
    "openalex": 0.9,
    "semanticscholar": 0.85,
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
        logger.warning("sentence-transformers unavailable, falling back to keyword similarity: %s", exc)
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
    """Match utils._normalize_doi to keep keys stable across providers."""
    doi = article.doi
    if doi:
        s = doi.strip().lower()
        for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
            if s.startswith(prefix):
                return s[len(prefix):]
        return s
    return (article.title or "").strip().lower()


def _tensor_to_bytes(tensor) -> bytes:
    import numpy as np
    return tensor.detach().cpu().numpy().astype(np.float32).tobytes()


def _bytes_to_tensor(data: bytes, dim: int):
    import numpy as np
    import torch
    arr = np.frombuffer(data, dtype=np.float32).reshape(dim).copy()
    return torch.from_numpy(arr)


def _embedding_similarity(articles: list[Article], query: str, model) -> list[float]:
    """Cache hierarchy: in-memory dict → SQLite → encode + persist."""
    from src import cache  # local import to avoid hard dependency in unit tests

    keys = [_article_cache_key(a) for a in articles]

    # Layer 1: hydrate in-memory cache from SQLite for missing keys
    persisted_misses = [k for k in keys if k and k not in _embedding_cache]
    if persisted_misses:
        try:
            stored = cache.get_embeddings_batch(persisted_misses)
            for k, (vec_bytes, dim) in stored.items():
                _embedding_cache[k] = _bytes_to_tensor(vec_bytes, dim)
        except Exception as exc:
            logger.warning("Could not load embeddings from SQLite: %s", exc)

    # Layer 2: encode anything still missing
    encode_indices = [i for i, k in enumerate(keys) if k and k not in _embedding_cache]
    if encode_indices:
        encode_texts = [f"{articles[i].title} {articles[i].abstract or ''}" for i in encode_indices]
        encoded = model.encode(encode_texts, convert_to_tensor=True)
        new_items = []
        for idx, emb in zip(encode_indices, encoded):
            k = keys[idx]
            _embedding_cache[k] = emb
            new_items.append((k, _tensor_to_bytes(emb), int(emb.shape[-1])))
        try:
            cache.store_embeddings_batch(new_items)
        except Exception as exc:
            logger.warning("Could not persist embeddings to SQLite: %s", exc)

    # Articles without a usable key (no DOI nor title) get encoded fresh each call.
    fallback_keys = {i: k for i, k in enumerate(keys) if not k}
    if fallback_keys:
        fallback_texts = [f"{articles[i].title or ''} {articles[i].abstract or ''}" for i in fallback_keys]
        fallback_embs = model.encode(fallback_texts, convert_to_tensor=True)
        fallback_map = dict(zip(fallback_keys.keys(), fallback_embs))
    else:
        fallback_map = {}

    doc_embs = [fallback_map[i] if not k else _embedding_cache[k] for i, k in enumerate(keys)]
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
