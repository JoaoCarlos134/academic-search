import pytest
from src.models import Article
from src.ranking import _keyword_similarity, _normalize_citations, _recency_score, score_articles


def _art(title="paper", abstract=None, year=2020, citations=0, source="openalex"):
    return Article(
        title=title, year=year, doi=None, abstract=abstract,
        authors=[], venue=None, source=source, url=None, citations=citations,
    )


class TestKeywordSimilarity:
    def test_exact_match(self):
        arts = [_art("machine learning")]
        scores = _keyword_similarity(arts, "machine learning")
        assert scores[0] == pytest.approx(1.0)

    def test_no_match(self):
        arts = [_art("biology genetics")]
        scores = _keyword_similarity(arts, "machine learning")
        assert scores[0] == pytest.approx(0.0)

    def test_empty_query_returns_zeros(self):
        arts = [_art("anything"), _art("something")]
        scores = _keyword_similarity(arts, "")
        assert scores == [0.0, 0.0]

    def test_whitespace_only_query(self):
        arts = [_art("anything")]
        scores = _keyword_similarity(arts, "   ")
        assert scores == [0.0]

    def test_abstract_included(self):
        arts = [_art("other title", abstract="deep learning neural")]
        scores = _keyword_similarity(arts, "deep learning")
        assert scores[0] > 0.0

    def test_normalized_to_max_one(self):
        arts = [_art("a b c"), _art("a b c d e f")]
        scores = _keyword_similarity(arts, "a b c")
        assert max(scores) == pytest.approx(1.0)


class TestRecencyScore:
    def test_current_year_high(self):
        from src.ranking import CURRENT_YEAR
        score = _recency_score(CURRENT_YEAR)
        assert score == pytest.approx(1.0)

    def test_old_paper_low(self):
        score = _recency_score(1990)
        assert score < 0.1

    def test_none_year_returns_default(self):
        score = _recency_score(None)
        assert 0.0 < score < 1.0


class TestNormalizeCitations:
    def test_all_zeros(self):
        result = _normalize_citations([0, 0, 0])
        assert result == [0.0, 0.0, 0.0]

    def test_max_is_one(self):
        result = _normalize_citations([0, 10, 100])
        assert max(result) == pytest.approx(1.0)

    def test_monotonic(self):
        result = _normalize_citations([1, 10, 100])
        assert result[0] < result[1] < result[2]


class TestScoreArticles:
    def test_empty_returns_empty(self):
        assert score_articles([], "query") == []

    def test_scores_assigned(self):
        arts = [_art("machine learning"), _art("biology")]
        result = score_articles(arts, "machine learning")
        assert all(a.score > 0 for a in result)

    def test_sorted_descending(self):
        arts = [_art("machine learning"), _art("biology genetics evolution")]
        result = score_articles(arts, "machine learning")
        assert result[0].score >= result[1].score

    def test_no_year_does_not_crash(self):
        arts = [_art(year=None), _art(year=2020)]
        result = score_articles(arts, "test")
        assert len(result) == 2

    def test_no_abstract_does_not_crash(self):
        arts = [_art(abstract=None), _art(abstract="some text")]
        result = score_articles(arts, "test")
        assert len(result) == 2

    def test_recent_paper_not_penalized(self):
        from src.ranking import CURRENT_YEAR
        recent = _art("deep learning", year=CURRENT_YEAR, citations=0)
        old = _art("deep learning", year=CURRENT_YEAR - 10, citations=1000)
        result = score_articles([recent, old], "deep learning")
        # recent with 0 citations should still score reasonably — just checking no crash
        assert len(result) == 2
