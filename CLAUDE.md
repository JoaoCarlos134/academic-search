# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies (editable mode)
pip install -e .

# Run the CLI
search "machine learning"
search "neural networks" --limit 20

# Lint
ruff check src/
ruff check --fix src/

# Format
ruff format src/
```

Python version is managed via `.python-version` (currently 3.14). There are no tests yet.

## Architecture

This is a CLI academic paper search engine. The entry point is `search` (defined in `pyproject.toml` → `src.cli:app`).

**Data flow:** `cli.py` → `providers/*.py` (fetch) → `ranking.py` (score) → `cli.py` (display)

### Core model

`src/models.py` defines `Article`, the single shared dataclass that all providers must produce. Deduplication uses DOI when available, falling back to title.

### Providers (`src/providers/`)

Each provider is an async function `search(query, limit) -> list[Article]` that hits one external API and maps the response to `Article`. Currently only `openalex.py` is implemented; `elsevier.py` and `ieee.py` are empty stubs.

- **OpenAlex** (`openalex.py`): free, no key required. Uses the polite pool via `mailto` — the `EMAIL` constant at the top of the file should be set to a real address for better rate limits. Abstracts are reconstructed from OpenAlex's inverted index format.
- **Elsevier** (`elsevier.py`): requires `ELSEVIER_API_KEY` and `ELSEVIER_INST_TOKEN` from `.env`.
- **IEEE** (`ieee.py`): requires `IEEE_API_KEY` from `.env`.

When adding a new provider, add it to `_search_all()` in `cli.py` (currently only calls OpenAlex) and register its `source` string in `ranking.py`'s `SOURCE_WEIGHTS`.

### Ranking (`src/ranking.py`)

Scores each `Article` as a weighted sum of four components:

| Component | Weight | Method |
|-----------|--------|--------|
| similarity | 50% | Keyword overlap (query words ∩ title+abstract words), normalized |
| recency | 20% | Exponential decay: `exp(-age / 5)` |
| source | 15% | Lookup in `SOURCE_WEIGHTS` dict |
| citations | 15% | `log1p`-normalized citation count |

Note: `sentence-transformers` is listed as a dependency but not yet used — similarity is currently keyword-based, not embedding-based.

### Environment

API keys are loaded from `.env` (via `python-dotenv`). Required keys for non-OpenAlex providers:
- `ELSEVIER_API_KEY`
- `ELSEVIER_INST_TOKEN`
- `IEEE_API_KEY`

### Stubs

`src/cache.py` is an empty placeholder for future request caching (SQLAlchemy is already a dependency).

## Conventions

- No emojis in f-strings or print statements (Windows encoding issues)
- Use `uv add <package>` for new dependencies, never `pip install`
- Commits follow Conventional Commits: feat:, fix:, chore:
- All providers must implement: `async def search(query: str, limit: int) -> list[Article]`
- Run `search "machine learning" --limit 5` to smoke-test after changes

## Next steps (priority order)
1. src/cache.py — SQLite via SQLAlchemy, TTL 24h, keyed by query+doi
2. src/providers/elsevier.py — Scopus Search API, headers X-ELS-APIKey + X-ELS-Insttoken
3. src/providers/ieee.py — ieeexploreapi.ieee.org/api/v1/search/articles
4. Semantic similarity via sentence-transformers (all-MiniLM-L6-v2)
