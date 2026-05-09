import pytest
from datetime import timedelta
from src.models import Article


def _art(doi, title="T", source="openalex", citations=0):
    return Article(
        title=title, year=2020, doi=doi, abstract="abstract text",
        authors=["Author A"], venue="Journal X", source=source,
        url=f"https://doi.org/{doi}", citations=citations,
        score=0.5, venue_type="journal", publisher=None,
        is_oa=True, oa_url=None, concepts=["ML"],
    )


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Isolated in-memory-like cache using a temp file DB."""
    db_path = str(tmp_path / "test_cache.db")
    monkeypatch.setenv("CACHE_DB", db_path)
    # Re-import to pick up new env var and create fresh engine/tables
    import importlib
    import src.cache as cache_mod
    importlib.reload(cache_mod)
    return cache_mod


class TestCacheStoreGet:
    def test_store_and_get(self, db):
        arts = [_art("10.1/a"), _art("10.1/b")]
        db.store("query1", arts)
        result = db.get("query1")
        assert result is not None
        assert len(result) == 2

    def test_get_missing_query_returns_none(self, db):
        assert db.get("nonexistent") is None

    def test_roundtrip_fields(self, db):
        art = _art("10.1/x", title="Test Paper", citations=42)
        db.store("q", [art])
        result = db.get("q")
        assert result[0].title == "Test Paper"
        assert result[0].citations == 42
        assert result[0].is_oa is True
        assert result[0].concepts == ["ML"]

    def test_store_overwrites_previous(self, db):
        db.store("q", [_art("10.1/old")])
        db.store("q", [_art("10.1/new1"), _art("10.1/new2")])
        result = db.get("q")
        assert len(result) == 2
        dois = {a.doi for a in result}
        assert "10.1/new1" in dois

    def test_min_size_hit(self, db):
        arts = [_art(f"10.1/{i}") for i in range(10)]
        db.store("q", arts)
        assert db.get("q", min_size=5) is not None
        assert db.get("q", min_size=10) is not None

    def test_min_size_miss(self, db):
        arts = [_art(f"10.1/{i}") for i in range(5)]
        db.store("q", arts)
        assert db.get("q", min_size=10) is None

    def test_ttl_expired_returns_none(self, db, monkeypatch):
        arts = [_art("10.1/ttl")]
        db.store("q_ttl", arts)
        # Shrink TTL to zero so the entry is immediately stale
        monkeypatch.setattr(db, "TTL", timedelta(seconds=0))
        assert db.get("q_ttl") is None


class TestCacheClear:
    def test_clear_removes_all(self, db):
        db.store("q1", [_art("10.1/a")])
        db.store("q2", [_art("10.1/b")])
        removed = db.clear()
        assert removed == 2
        assert db.get("q1") is None

    def test_clear_empty_returns_zero(self, db):
        assert db.clear() == 0


class TestInvalidate:
    def test_invalidate_single_query(self, db):
        db.store("q1", [_art("10.1/a")])
        db.store("q2", [_art("10.1/b")])
        removed = db.invalidate("q1")
        assert removed == 1
        assert db.get("q1") is None
        assert db.get("q2") is not None

    def test_invalidate_unknown_returns_zero(self, db):
        assert db.invalidate("never-cached") == 0


class TestEmbeddingsCache:
    def test_store_and_retrieve(self, db):
        vec = b"\x00\x01\x02\x03" * 96  # 384 bytes = 96 float32
        db.store_embeddings_batch([("10.1/x", vec, 96)])
        out = db.get_embeddings_batch(["10.1/x"])
        assert "10.1/x" in out
        assert out["10.1/x"] == (vec, 96)

    def test_get_batch_returns_only_present(self, db):
        db.store_embeddings_batch([("a", b"\x00" * 4, 1)])
        out = db.get_embeddings_batch(["a", "b", "c"])
        assert set(out.keys()) == {"a"}

    def test_store_skips_existing_key(self, db):
        db.store_embeddings_batch([("dup", b"\x01\x02\x03\x04", 1)])
        # Second store with different vec should be silently ignored
        db.store_embeddings_batch([("dup", b"\xff\xff\xff\xff", 1)])
        out = db.get_embeddings_batch(["dup"])
        assert out["dup"][0] == b"\x01\x02\x03\x04"

    def test_count_embeddings(self, db):
        assert db.count_embeddings() == 0
        db.store_embeddings_batch([
            ("k1", b"\x00" * 4, 1),
            ("k2", b"\x00" * 4, 1),
        ])
        assert db.count_embeddings() == 2

    def test_empty_batches_are_noops(self, db):
        db.store_embeddings_batch([])  # should not raise
        assert db.get_embeddings_batch([]) == {}


class TestRecentQueries:
    def test_empty_when_no_cache(self, db):
        assert db.list_recent_queries() == []

    def test_returns_distinct_newest_first(self, db):
        db.store("first", [_art("10.1/a")])
        db.store("second", [_art("10.1/b")])
        db.store("third", [_art("10.1/c")])
        recent = db.list_recent_queries()
        assert recent == ["third", "second", "first"]

    def test_dedupes_repeated_queries(self, db):
        db.store("same", [_art("10.1/a")])
        db.store("other", [_art("10.1/b")])
        db.store("same", [_art("10.1/c")])  # re-cached
        recent = db.list_recent_queries()
        assert recent.count("same") == 1
        assert recent[0] == "same"  # most recent

    def test_respects_limit(self, db):
        for i in range(5):
            db.store(f"q{i}", [_art(f"10.1/{i}")])
        recent = db.list_recent_queries(limit=3)
        assert len(recent) == 3


class TestHiddenArticles:
    def test_hide_and_get(self, db):
        db.hide_article("10.1/h", "Hidden Paper")
        dois = db.get_hidden_dois()
        assert "10.1/h" in dois

    def test_hide_idempotent(self, db):
        db.hide_article("10.1/h", "Paper")
        db.hide_article("10.1/h", "Paper")  # should not raise
        assert len(db.get_hidden_dois()) == 1

    def test_unhide(self, db):
        db.hide_article("10.1/h", "Paper")
        db.unhide_article("10.1/h")
        assert "10.1/h" not in db.get_hidden_dois()

    def test_clear_hidden(self, db):
        db.hide_article("10.1/a", "A")
        db.hide_article("10.1/b", "B")
        db.clear_hidden()
        assert db.get_hidden_dois() == set()
