# Contributing

Development guide for Academic Search. For what the project does and how to use
it, see the [README](README.md).

## Project layout

```
academic-search/
├── src/
│   ├── cli.py             # CLI entry point (Typer)
│   ├── web.py             # Web entry point (Streamlit)
│   ├── models.py          # Article dataclass — the single shared model
│   ├── cache.py           # SQLite via SQLAlchemy: searches, hidden articles, embeddings
│   ├── ranking.py         # score = similarity + recency + source + citations
│   ├── utils.py           # http_get, _normalize_doi, dedup_articles
│   ├── retractions.py     # Retraction Watch CSV downloader + matcher
│   ├── clustering.py      # KMeans with auto-K via silhouette score
│   └── providers/
│       ├── openalex.py        # OpenAlex Works API (free)
│       ├── elsevier.py        # Scopus Search API
│       ├── ieee.py            # IEEE Xplore API
│       └── semanticscholar.py # Semantic Scholar Graph API
├── tests/                 # pytest, 74 tests
├── docs/screenshots/      # README images
├── pyproject.toml         # deps + scripts (search, academic-web)
├── ROADMAP.txt            # open items and change history
├── CLAUDE.md              # architecture guide for Claude Code (and humans)
└── .env.example           # environment variable template
```

## What each module owns

| File | Responsibility |
|------|----------------|
| `cli.py` | `search main`, `clear-cache`, `refresh-retractions`. Applies post-fetch filters. |
| `web.py` | Streamlit app. Three tabs (Search / Export / Hidden), pre- and post-fetch filters, exports. |
| `models.py` | `@dataclass Article` — the fields every provider must produce. Hash and equality normalize the DOI. |
| `cache.py` | Three tables: `article_cache` (24h TTL), `hidden_articles` (no TTL, manual sweep), `article_embeddings` (BLOB vectors). |
| `ranking.py` | `score_articles()` — similarity 50% (embedding, keyword fallback) + recency 20% + source 15% + citations 15%. Three-layer embedding cache. |
| `utils.py` | `http_get()` / `http_get_sync()` with SSL fallback, `_normalize_doi()` (resolves URL-prefixed vs bare), `dedup_articles()` (DOI then title). |
| `retractions.py` | Local cache of the Retraction Watch CSV; `mark_retracted(articles)` does an O(1) lookup. |
| `clustering.py` | KMeans with auto-K (silhouette score); labels inferred from the top tokens of clustered titles. |
| `providers/*.py` | Each implements `async def search(query, limit) -> list[Article]`. |

## Data flow

```
user → cli.py / web.py
     → cache.get(query)          hit? return directly
                                 miss ↓
     → _search_all()   → providers/*.py   (concurrent, asyncio.gather)
                       → dedup_articles()  (utils.py)
                       → score_articles()  (ranking.py)
                       → mark_retracted()  (retractions.py, optional)
                       → cache.store()
     → cluster_articles()        (clustering.py, optional)
     → render / export
```

**Keep this pipeline order:** fetch → merge + dedup → rank → apply filters →
truncate to `limit`. The `limit` caps the *final* list, not the per-provider
fetch; both orchestrators request `min(limit * 3, 100)` candidates per provider
so ranking has headroom.

## Development commands

```bash
# All tests
uv run pytest tests/ -v

# One module
uv run pytest tests/test_cache.py -v

# Lint
uv run ruff check src/
uv run ruff check --fix src/

# Format
uv run ruff format src/

# Smoke test after touching a provider
uv run search main "machine learning" --limit 5

# Add a dependency
uv add <package>     # never `pip install`
```

## Conventions

- No emojis in strings or prints — Windows encoding issues.
- Conventional Commits: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`.
- Every provider's public entry point: `async def search(query: str, limit: int) -> list[Article]`.
- Every `Article` field must be populated by every provider — use `None` for
  unavailable data, never omit the field.
- When adding a field to `Article`, update all providers and the cache schema in
  the same change. `cache._migrate()` handles backward-compatible column adds.
- Use `logger = logging.getLogger(__name__)` — no bare `print()` inside `src/`.
- All UI and CLI strings are English. Keep it that way; `README.pt-BR.md` is the
  only Portuguese surface.
- Never call `httpx` directly. Use `http_get()` (async) or `http_get_sync()`
  (blocking) from `src/utils.py` — they carry the corporate-proxy SSL fallback
  that raw `httpx` calls silently lack.
- SQLite datetimes are always naive UTC — use `cache._utcnow()`.
- Always pass DOIs through `_normalize_doi()` before comparing or hashing.
- In `web.py`, persist results to `st.session_state` before any widget
  interaction. `query_input` is widget-bound: never assign to it after the
  `st.text_input` is created — mutate it from an `on_click` callback instead.

## Adding a provider

1. Create `src/providers/new.py` with `async def search(query, limit) -> list[Article]`.
2. Register it in `_search_all()` in **both** `src/cli.py` and `src/web.py`.
3. Add `"new"` to `_PROVIDER_NAMES` in both files.
4. Add it to `SOURCE_WEIGHTS` in `src/ranking.py`.
5. Add it to `_ALL_SOURCES` in `src/web.py` (the UI filter).
6. Normalize DOIs via `_normalize_doi` so cross-provider dedup works.

## Before you edit

1. **Read the relevant code first** — open the files you're about to touch
   before the first edit.
2. **Update `ROADMAP.txt`** when you find a bug, identify a new step, or finish
   an item (move it to the history section with a date). `ROADMAP.txt` is the
   single source of truth for the roadmap; `CLAUDE.md` deliberately does not
   duplicate it.

## Where to help

- **`ROADMAP.txt`** — open items live under "Pos-mediums vale considerar";
  item P7 (renewing the IEEE API key) is external.
- **`CLAUDE.md`** — detailed architecture notes for any assistant or developer
  new to the project.
