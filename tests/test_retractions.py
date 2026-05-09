import pytest

from src.models import Article


def _art(doi):
    return Article(
        title="T", year=2020, doi=doi, abstract=None,
        authors=[], venue=None, source="openalex", url=None,
    )


@pytest.fixture
def retractions_mod(tmp_path, monkeypatch):
    """Isolated retractions module with a temp CSV path."""
    import importlib
    import src.retractions as r
    monkeypatch.setattr(r, "CACHE_FILE", tmp_path / "retractions.csv")
    importlib.reload(r)
    monkeypatch.setattr(r, "CACHE_FILE", tmp_path / "retractions.csv")
    r._dois_set = None
    return r


def _write_csv(path, dois):
    header = "Record ID,OriginalPaperDOI,RetractionDate\n"
    rows = "\n".join(f"{i},{d},2024-01-01" for i, d in enumerate(dois))
    path.write_text(header + rows + "\n", encoding="utf-8")


class TestCacheLifecycle:
    def test_no_cache_initially(self, retractions_mod):
        assert not retractions_mod.is_cache_present()
        assert retractions_mod.get_retracted_dois() == set()

    def test_loads_after_csv_present(self, retractions_mod):
        _write_csv(retractions_mod.CACHE_FILE, ["10.1/x", "10.1/y"])
        retractions_mod._dois_set = None  # force reload
        dois = retractions_mod.get_retracted_dois()
        assert "10.1/x" in dois
        assert "10.1/y" in dois

    def test_skip_unavailable_doi(self, retractions_mod):
        _write_csv(retractions_mod.CACHE_FILE, ["Unavailable", "10.1/real"])
        retractions_mod._dois_set = None
        dois = retractions_mod.get_retracted_dois()
        assert "10.1/real" in dois
        assert "unavailable" not in dois


class TestMarkRetracted:
    def test_no_op_without_cache(self, retractions_mod):
        arts = [_art("10.1/x"), _art("10.1/y")]
        n = retractions_mod.mark_retracted(arts)
        assert n == 0
        assert all(a.retracted is None for a in arts)

    def test_marks_matching_dois(self, retractions_mod):
        _write_csv(retractions_mod.CACHE_FILE, ["10.1/bad"])
        retractions_mod._dois_set = None
        arts = [_art("10.1/bad"), _art("10.1/good")]
        n = retractions_mod.mark_retracted(arts)
        assert n == 1
        assert arts[0].retracted is True
        assert arts[1].retracted is False

    def test_handles_doi_url_prefix(self, retractions_mod):
        _write_csv(retractions_mod.CACHE_FILE, ["10.1/bad"])
        retractions_mod._dois_set = None
        a = _art("https://doi.org/10.1/bad")
        retractions_mod.mark_retracted([a])
        assert a.retracted is True

    def test_no_doi_stays_none(self, retractions_mod):
        _write_csv(retractions_mod.CACHE_FILE, ["10.1/x"])
        retractions_mod._dois_set = None
        a = _art(None)
        retractions_mod.mark_retracted([a])
        assert a.retracted is None
