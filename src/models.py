from dataclasses import dataclass
from typing import Optional


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

    def __hash__(self):
        return hash(self.doi or self.title)

    def __eq__(self, other):
        if self.doi and other.doi:
            return self.doi == other.doi
        return self.title == other.title
