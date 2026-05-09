from dataclasses import dataclass
from typing import Optional


def _norm_doi(doi: Optional[str]) -> str:
    """Strip URL prefix and lowercase. Mirrors utils._normalize_doi to keep
    Article hashable without circular import."""
    if not doi:
        return ""
    s = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if s.startswith(prefix):
            return s[len(prefix):]
    return s


@dataclass
class Article:
    title: str
    year: Optional[int]
    doi: Optional[str]
    abstract: Optional[str]
    authors: list[str]
    venue: Optional[str]
    source: str  # "openalex", "elsevier", "ieee"
    url: Optional[str]
    citations: int = 0
    score: float = 0.0
    venue_type: str | None = None       # "journal" | "conference" | "repository" | "book" | "other"
    publisher: str | None = None
    is_oa: bool | None = None
    oa_url: str | None = None
    concepts: list[str] | None = None
    retracted: bool | None = None       # None = nao verificado; True = retratado (Retraction Watch)

    def __hash__(self):
        return hash(_norm_doi(self.doi) or (self.title or ""))

    def __eq__(self, other):
        if not isinstance(other, Article):
            return NotImplemented
        if self.doi and other.doi:
            return _norm_doi(self.doi) == _norm_doi(other.doi)
        return self.title == other.title
