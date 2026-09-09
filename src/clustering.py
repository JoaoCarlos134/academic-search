"""Topic clustering of search results using sentence-transformer embeddings.

Operates on embeddings already in ranking._embedding_cache (populated during
scoring). Picks K via silhouette score; infers cluster labels from the top
non-stopword tokens across the cluster's titles.
"""

import logging
import re
from collections import Counter

from src.models import Article
from src.ranking import _article_cache_key, _embedding_cache

logger = logging.getLogger(__name__)


# Minimal stop-word list for label inference (English academic titles).
_STOP = {
    "the", "a", "an", "of", "in", "for", "to", "and", "or", "with", "on",
    "from", "by", "as", "is", "are", "was", "were", "be", "been", "being",
    "this", "that", "these", "those", "it", "its", "we", "our", "their",
    "using", "via", "based", "study", "studies", "approach", "approaches",
    "method", "methods", "case", "review", "analysis", "research",
    "novel", "new", "model", "models", "use", "used", "system", "systems",
    "data", "results", "result", "performance", "evaluation",
}


def cluster_articles(
    articles: list[Article],
    max_k: int = 5,
    min_articles_per_cluster: int = 2,
) -> list[dict]:
    """Group articles into topical clusters.

    Returns a list of dicts: [{"label": str, "articles": [...]}], sorted by
    cluster size desc. Falls back to a single "Results" cluster when:
    - sklearn is unavailable
    - too few articles to cluster (< 2 * min_articles_per_cluster)
    - no embeddings available (sentence-transformers fallback to keywords)
    """
    if len(articles) < max(3, 2 * min_articles_per_cluster):
        return [{"label": "Results", "articles": list(articles)}]

    try:
        import numpy as np
        from sklearn.cluster import KMeans
        from sklearn.metrics import silhouette_score
    except ImportError as exc:
        logger.warning("sklearn unavailable, skipping clustering: %s", exc)
        return [{"label": "Results", "articles": list(articles)}]

    # Collect embeddings for articles that have one in the cache
    indexed = []
    for i, a in enumerate(articles):
        key = _article_cache_key(a)
        emb = _embedding_cache.get(key) if key else None
        if emb is not None:
            indexed.append((i, emb))

    if len(indexed) < max(3, 2 * min_articles_per_cluster):
        return [{"label": "Results", "articles": list(articles)}]

    X = np.array([emb.detach().cpu().numpy() if hasattr(emb, "detach") else np.asarray(emb)
                  for _, emb in indexed])
    n = len(indexed)
    upper_k = min(max_k, n // min_articles_per_cluster)
    if upper_k < 2:
        return [{"label": "Results", "articles": list(articles)}]

    best_k, best_score, best_labels = 2, -1.0, None
    for k in range(2, upper_k + 1):
        km = KMeans(n_clusters=k, random_state=42, n_init=10).fit(X)
        if len(set(km.labels_)) < 2:
            continue
        score = silhouette_score(X, km.labels_)
        if score > best_score:
            best_k, best_score, best_labels = k, score, km.labels_

    if best_labels is None:
        return [{"label": "Results", "articles": list(articles)}]

    # Group articles by cluster id (using the indexed positions)
    groups: dict[int, list[Article]] = {}
    indexed_set = {i for i, _ in indexed}
    for (orig_idx, _), cid in zip(indexed, best_labels):
        groups.setdefault(int(cid), []).append(articles[orig_idx])

    # Articles without an embedding fall into a misc bucket
    misc = [a for i, a in enumerate(articles) if i not in indexed_set]
    if misc:
        groups[-1] = misc

    # Build output sorted by group size desc
    result = []
    for cid, arts in groups.items():
        label = "Sem topico" if cid == -1 else _infer_label([a.title for a in arts])
        result.append({"label": label, "articles": arts})
    result.sort(key=lambda c: len(c["articles"]), reverse=True)
    return result


def _infer_label(titles: list[str], max_words: int = 3) -> str:
    """Top N most-frequent non-stopword tokens (>=3 chars) across the titles."""
    counter: Counter = Counter()
    for t in titles:
        if not t:
            continue
        for word in re.findall(r"[a-zA-Z]{3,}", t.lower()):
            if word not in _STOP:
                counter[word] += 1
    top = [w for w, _ in counter.most_common(max_words)]
    return " / ".join(w.capitalize() for w in top) if top else "Outros"
