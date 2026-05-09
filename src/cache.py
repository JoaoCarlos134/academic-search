import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    delete,
    inspect,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Session

from src.models import Article

TTL = timedelta(hours=24)
_DEFAULT_DB = Path(__file__).resolve().parent.parent / "academic_cache.db"
_DB_PATH = os.getenv("CACHE_DB", str(_DEFAULT_DB))
_engine = create_engine(f"sqlite:///{_DB_PATH}")


def _utcnow() -> datetime:
    """Naive UTC datetime — SQLite does not preserve tzinfo."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class _Base(DeclarativeBase):
    pass


class _CachedArticle(_Base):
    __tablename__ = "article_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    query = Column(String, nullable=False, index=True)
    doi = Column(String, nullable=True)
    cached_at = Column(DateTime, nullable=False)
    title = Column(String, nullable=False)
    year = Column(Integer, nullable=True)
    abstract = Column(Text, nullable=True)
    authors = Column(Text, nullable=False)  # JSON-encoded list[str]
    venue = Column(String, nullable=True)
    source = Column(String, nullable=False)
    url = Column(String, nullable=True)
    citations = Column(Integer, nullable=False, default=0)
    score = Column(Float, nullable=False, default=0.0)
    venue_type = Column(String, nullable=True)
    publisher = Column(String, nullable=True)
    is_oa = Column(Boolean, nullable=True)
    oa_url = Column(String, nullable=True)
    concepts = Column(Text, nullable=True)  # JSON-encoded list[str] | None


class _HiddenArticle(_Base):
    __tablename__ = "hidden_articles"

    doi = Column(String, primary_key=True)
    title = Column(String, nullable=True)
    hidden_at = Column(DateTime, nullable=False, default=_utcnow)


_Base.metadata.create_all(_engine)
_EXTRA_COLS = {
    "venue_type": "VARCHAR",
    "publisher": "VARCHAR",
    "is_oa": "BOOLEAN",
    "oa_url": "VARCHAR",
    "concepts": "TEXT",
}


def _migrate() -> None:
    """Add columns introduced after initial schema creation."""
    if "article_cache" not in inspect(_engine).get_table_names():
        return
    existing = {c["name"] for c in inspect(_engine).get_columns("article_cache")}
    missing = {col: typ for col, typ in _EXTRA_COLS.items() if col not in existing}
    if not missing:
        return
    with _engine.connect() as conn:
        for col, typ in missing.items():
            conn.execute(text(f"ALTER TABLE article_cache ADD COLUMN {col} {typ}"))
        conn.commit()


_migrate()


def clear() -> int:
    """Delete all cached entries. Returns the number of rows removed."""
    with Session(_engine) as session:
        result = session.execute(delete(_CachedArticle))
        session.commit()
        return result.rowcount


def invalidate(query: str) -> int:
    """Delete cached entries for a single query. Returns rows removed."""
    with Session(_engine) as session:
        result = session.execute(
            delete(_CachedArticle).where(_CachedArticle.query == query)
        )
        session.commit()
        return result.rowcount


def list_recent_queries(limit: int = 10) -> list[str]:
    """Return the most recently cached queries (deduped, newest first)."""
    with Session(_engine) as session:
        rows = session.execute(
            select(_CachedArticle.query, _CachedArticle.cached_at)
            .order_by(_CachedArticle.cached_at.desc())
        ).all()
    seen: set[str] = set()
    out: list[str] = []
    for q, _ in rows:
        if q in seen:
            continue
        seen.add(q)
        out.append(q)
        if len(out) >= limit:
            break
    return out


def hide_article(doi: str, title: str) -> None:
    with Session(_engine) as session:
        existing = session.get(_HiddenArticle, doi)
        if existing is None:
            session.add(_HiddenArticle(doi=doi, title=title, hidden_at=_utcnow()))
            session.commit()


def get_hidden_dois() -> set[str]:
    with Session(_engine) as session:
        rows = session.scalars(select(_HiddenArticle.doi)).all()
    return set(rows)


def unhide_article(doi: str) -> None:
    with Session(_engine) as session:
        session.execute(delete(_HiddenArticle).where(_HiddenArticle.doi == doi))
        session.commit()


def clear_hidden() -> None:
    with Session(_engine) as session:
        session.execute(delete(_HiddenArticle))
        session.commit()


def get_hidden_articles() -> list[dict]:
    with Session(_engine) as session:
        rows = session.scalars(
            select(_HiddenArticle).order_by(_HiddenArticle.hidden_at.desc())
        ).all()
    return [{"doi": r.doi, "title": r.title, "hidden_at": r.hidden_at} for r in rows]


def sweep_hidden(days: int = 90) -> int:
    """Delete hidden articles older than `days` days. Returns count removed."""
    cutoff = _utcnow() - timedelta(days=days)
    with Session(_engine) as session:
        result = session.execute(
            delete(_HiddenArticle).where(_HiddenArticle.hidden_at < cutoff)
        )
        session.commit()
        return result.rowcount


def count_hidden() -> int:
    with Session(_engine) as session:
        return session.query(_HiddenArticle).count()


def get(query: str, min_size: int = 0) -> list[Article] | None:
    """Return cached articles for query if within TTL and count >= min_size.

    Pass min_size=limit so a cache entry from a smaller prior search never
    silently caps a larger request. Use `store(query, articles)` to save.
    """
    cutoff = _utcnow() - TTL
    with Session(_engine) as session:
        rows = session.scalars(
            select(_CachedArticle)
            .where(_CachedArticle.query == query)
            .where(_CachedArticle.cached_at >= cutoff)
        ).all()
    if not rows or len(rows) < min_size:
        return None
    return [_to_article(r) for r in rows]


def store(query: str, articles: list[Article]) -> None:
    """Persist articles for a query, replacing any existing entry."""
    now = _utcnow()
    with Session(_engine) as session:
        session.execute(delete(_CachedArticle).where(_CachedArticle.query == query))
        for article in articles:
            session.add(
                _CachedArticle(
                    query=query,
                    doi=article.doi,
                    cached_at=now,
                    title=article.title,
                    year=article.year,
                    abstract=article.abstract,
                    authors=json.dumps(article.authors),
                    venue=article.venue,
                    source=article.source,
                    url=article.url,
                    citations=article.citations,
                    score=article.score,
                    venue_type=article.venue_type,
                    publisher=article.publisher,
                    is_oa=article.is_oa,
                    oa_url=article.oa_url,
                    concepts=json.dumps(article.concepts) if article.concepts is not None else None,
                )
            )
        session.commit()


def _to_article(row: _CachedArticle) -> Article:
    return Article(
        title=row.title,
        year=row.year,
        doi=row.doi,
        abstract=row.abstract,
        authors=json.loads(row.authors),
        venue=row.venue,
        source=row.source,
        url=row.url,
        citations=row.citations,
        score=row.score,
        venue_type=row.venue_type,
        publisher=row.publisher,
        is_oa=row.is_oa,
        oa_url=row.oa_url,
        concepts=json.loads(row.concepts) if row.concepts is not None else None,
    )
