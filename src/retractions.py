"""Detect retracted papers via the Retraction Watch dataset (Crossref Labs).

The CSV (~64 MB) is downloaded on demand and cached locally. Membership tests
are O(1) on a normalized-DOI set held in memory.
"""

import csv
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from src.utils import _normalize_doi, http_get_sync

if TYPE_CHECKING:
    from src.models import Article

logger = logging.getLogger(__name__)

CSV_URL = "https://api.labs.crossref.org/data/retractionwatch"
CACHE_FILE = Path(__file__).resolve().parent.parent / "retractions_cache.csv"
TTL = timedelta(days=7)

_dois_set: set[str] | None = None


def is_cache_present() -> bool:
    return CACHE_FILE.exists()


def is_cache_fresh() -> bool:
    if not CACHE_FILE.exists():
        return False
    age = datetime.now() - datetime.fromtimestamp(CACHE_FILE.stat().st_mtime)
    return age < TTL


def cache_age_days() -> float | None:
    if not CACHE_FILE.exists():
        return None
    age = datetime.now() - datetime.fromtimestamp(CACHE_FILE.stat().st_mtime)
    return age.total_seconds() / 86400


def download() -> int:
    """Download the CSV synchronously. ~64 MB / 30-60s typical. Returns bytes."""
    logger.info("Downloading Retraction Watch CSV from %s ...", CSV_URL)
    r = http_get_sync(CSV_URL, timeout=120)
    r.raise_for_status()
    CACHE_FILE.write_bytes(r.content)
    global _dois_set
    _dois_set = None
    logger.info("Saved %d bytes to %s", len(r.content), CACHE_FILE)
    return len(r.content)


def _load_dois() -> set[str]:
    if not CACHE_FILE.exists():
        return set()
    dois: set[str] = set()
    try:
        with CACHE_FILE.open(encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                doi = (row.get("OriginalPaperDOI") or "").strip()
                if doi and doi.lower() != "unavailable":
                    dois.add(_normalize_doi(doi))
    except Exception as exc:
        logger.warning("Failed to parse Retraction Watch CSV: %s", exc)
    return dois


def get_retracted_dois() -> set[str]:
    """Return the in-memory set of normalized retracted DOIs (loaded lazily)."""
    global _dois_set
    if _dois_set is None:
        _dois_set = _load_dois()
    return _dois_set


def mark_retracted(articles: "list[Article]") -> int:
    """Set article.retracted=True for any DOI matching the dataset.

    Articles without a DOI keep retracted=None (cannot be checked).
    Returns the number of articles marked as retracted.
    """
    if not is_cache_present():
        return 0  # silent no-op when dataset unavailable
    retracted = get_retracted_dois()
    if not retracted:
        return 0
    n = 0
    for a in articles:
        if a.doi:
            hit = _normalize_doi(a.doi) in retracted
            a.retracted = hit
            if hit:
                n += 1
    return n
