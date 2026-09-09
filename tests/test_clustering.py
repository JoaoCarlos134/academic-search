import pytest

from src.clustering import _infer_label, cluster_articles
from src.models import Article


def _art(title, doi=None):
    return Article(
        title=title, year=2020, doi=doi, abstract=None,
        authors=[], venue=None, source="openalex", url=None,
    )


class TestInferLabel:
    def test_top_tokens(self):
        titles = [
            "Deep learning for image recognition",
            "Deep learning convolutional networks",
            "Image classification with deep networks",
        ]
        label = _infer_label(titles)
        assert "Deep" in label or "deep" in label.lower()

    def test_filters_stopwords(self):
        titles = ["The study of the analysis of the methods"]
        label = _infer_label(titles)
        # Nothing meaningful left after stopwords
        assert label == "Outros"

    def test_min_word_length(self):
        titles = ["AI ML DL on the data"]  # all 2-letter or stopwords
        label = _infer_label(titles)
        assert label == "Outros"

    def test_empty_titles(self):
        assert _infer_label([]) == "Outros"
        assert _infer_label(["", None, ""]) == "Outros"


class TestClusterArticles:
    def test_few_articles_returns_single_cluster(self):
        arts = [_art("Title 1"), _art("Title 2")]
        out = cluster_articles(arts)
        assert len(out) == 1
        assert out[0]["label"] == "Results"
        assert out[0]["articles"] == arts

    def test_no_embeddings_returns_single_cluster(self):
        # 10 articles but no embeddings cached → fallback
        arts = [_art(f"Title {i}", doi=f"10.1/{i}") for i in range(10)]
        out = cluster_articles(arts)
        assert len(out) == 1
        assert out[0]["label"] == "Results"

    def test_clusters_when_embeddings_present(self):
        # Inject fake embeddings: two distinct topics
        from src.ranking import _embedding_cache, _article_cache_key
        import numpy as np
        try:
            import torch
        except ImportError:
            pytest.skip("torch unavailable")

        topic_a = [_art(f"deep learning paper {i}", doi=f"10.1/a{i}") for i in range(4)]
        topic_b = [_art(f"organic chemistry paper {i}", doi=f"10.1/b{i}") for i in range(4)]
        all_arts = topic_a + topic_b

        # Two clearly separated clusters in vector space
        for a in topic_a:
            _embedding_cache[_article_cache_key(a)] = torch.tensor([1.0, 0.0, 0.0, 0.0])
        for a in topic_b:
            _embedding_cache[_article_cache_key(a)] = torch.tensor([0.0, 1.0, 0.0, 0.0])

        out = cluster_articles(all_arts, max_k=3)
        # Should detect 2 clusters
        assert len(out) >= 2
        # All articles accounted for
        total = sum(len(c["articles"]) for c in out)
        assert total == len(all_arts)

        # Cleanup
        for a in all_arts:
            _embedding_cache.pop(_article_cache_key(a), None)
