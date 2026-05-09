import pytest
from src.models import Article
from src.utils import _normalize_doi, dedup_articles


def _art(doi=None, title="T", source="openalex"):
    return Article(
        title=title, year=2020, doi=doi, abstract=None,
        authors=[], venue=None, source=source, url=None,
    )


class TestNormalizeDoi:
    def test_bare_doi(self):
        assert _normalize_doi("10.1000/xyz") == "10.1000/xyz"

    def test_https_prefix(self):
        assert _normalize_doi("https://doi.org/10.1000/xyz") == "10.1000/xyz"

    def test_http_prefix(self):
        assert _normalize_doi("http://doi.org/10.1000/xyz") == "10.1000/xyz"

    def test_doi_colon_prefix(self):
        assert _normalize_doi("doi:10.1000/xyz") == "10.1000/xyz"

    def test_uppercase_normalized(self):
        assert _normalize_doi("https://doi.org/10.1000/XYZ") == "10.1000/xyz"

    def test_none_returns_empty(self):
        assert _normalize_doi(None) == ""

    def test_empty_string(self):
        assert _normalize_doi("") == ""

    def test_whitespace_stripped(self):
        assert _normalize_doi("  10.1000/xyz  ") == "10.1000/xyz"


class TestDedupArticles:
    def test_same_bare_doi(self):
        arts = [_art("10.1000/x", source="openalex"), _art("10.1000/x", source="elsevier")]
        result = dedup_articles(arts)
        assert len(result) == 1
        assert result[0].source == "openalex"  # first wins

    def test_doi_url_vs_bare_deduped(self):
        """OpenAlex returns https://doi.org/10.x, Elsevier returns 10.x — must dedup."""
        arts = [
            _art("https://doi.org/10.1000/x", source="openalex"),
            _art("10.1000/x", source="elsevier"),
        ]
        result = dedup_articles(arts)
        assert len(result) == 1

    def test_different_dois_kept(self):
        arts = [_art("10.1000/x"), _art("10.1000/y")]
        result = dedup_articles(arts)
        assert len(result) == 2

    def test_title_fallback_when_no_doi(self):
        arts = [_art(None, "Same Title"), _art(None, "Same Title")]
        result = dedup_articles(arts)
        assert len(result) == 1

    def test_empty_title_no_doi_skipped(self):
        arts = [_art(None, ""), _art("10.x", "Real")]
        result = dedup_articles(arts)
        assert len(result) == 1
        assert result[0].doi == "10.x"

    def test_order_preserved(self):
        arts = [_art("10.1"), _art("10.2"), _art("10.3")]
        result = dedup_articles(arts)
        assert [a.doi for a in result] == ["10.1", "10.2", "10.3"]

    def test_empty_input(self):
        assert dedup_articles([]) == []
