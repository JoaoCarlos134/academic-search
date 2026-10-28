# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Read `ROADMAP.txt` (repo root) before starting any task.** It is the canonical, always-current roadmap and changelog: open priorities, completed work (with dates), and explicitly discarded ideas all live there. After finishing a unit of work, update `ROADMAP.txt` in the same style. Do not keep a second copy of the roadmap in this file.

## Commands

```bash
# Install dependencies (creates/syncs .venv from uv.lock)
uv sync

# Run the CLI — the `main` subcommand is required (the Typer app also exposes
# `clear-cache` and `refresh-retractions`).
uv run search main "machine learning"
uv run search main "neural networks" --limit 20
uv run search main "deep learning" --source openalex --source semanticscholar

# Web interface
uv run streamlit run src/web.py

# Lint / format
ruff check src/
ruff check --fix src/
ruff format src/

# Run the test suite
uv run pytest

# Smoke-test after changes
uv run search main "machine learning" --limit 5
```

Python version is managed via `.python-version` (currently 3.14). Tests live in `tests/` (pytest) — run them with `uv run pytest`. There is no `test_web.py`; the Streamlit layer is verified by running the app.

## Architecture

Academic paper search engine with CLI and web (Streamlit) interfaces.

**Data flow:** `cli.py` / `web.py` → `providers/*.py` (async fetch in parallel) → `ranking.py` (score) → display / export

**Entry points:**
- CLI: `search` command (defined in `pyproject.toml` → `src.cli:app`)
- Web: `uv run streamlit run src/web.py` or `uv run academic-web`

### Core model (`src/models.py`)

`Article` is the single shared dataclass all providers must produce. Deduplication uses DOI when available, falling back to title.

Fields: `title`, `year`, `doi`, `abstract`, `authors`, `venue`, `source`, `url`, `citations`, `score`, `venue_type`, `publisher`, `is_oa`, `oa_url`, `concepts`, `retracted`.

### Providers (`src/providers/`)

Each provider is `async def search(query: str, limit: int) -> list[Article]`. All four run in parallel via `asyncio.gather` in `_search_all()`.

| Provider | Status | Key required |
|----------|--------|--------------|
| `openalex.py` | Functional — all `Article` fields populated | None (polite pool via `OPENALEX_EMAIL`) |
| `elsevier.py` | Functional — Scopus Search API (`view=COMPLETE` caps count at 25) | `ELSEVIER_API_KEY`, `ELSEVIER_INST_TOKEN` |
| `ieee.py` | Functional — but `venue_type`, `publisher`, `is_oa`, `oa_url`, `concepts` not yet extracted | `IEEE_API_KEY` |
| `semanticscholar.py` | Functional — Graph API; `publisher`/`concepts` left `None`; 0.2s pre-request sleep + soft-fail on HTTP 429 | Optional `SEMANTIC_SCHOLAR_API_KEY` (anonymous tier otherwise) |

**OpenAlex note:** set `OPENALEX_EMAIL` in `.env` to a real address to join the polite pool (better rate limits).

**Elsevier note:** year filter uses `date.today().year + 2` to allow in-press articles.

When adding a new provider: add it to `_search_all()` in both `cli.py` and `web.py`, and register its `source` string in `ranking.py`'s `SOURCE_WEIGHTS`.

**Limit semantics:** the `limit` (CLI `--limit` / web slider) caps the *final* ranked list — it is not a per-provider fetch size. Both orchestrators fetch `min(limit * 3, 100)` candidates per provider for ranking headroom. Keep this pipeline order: fetch → merge + dedup → rank → apply filters → truncate to `limit`.

### Ranking (`src/ranking.py`)

Scores each `Article` as a weighted sum of four components:

| Component | Weight | Method |
|-----------|--------|--------|
| similarity | 50% | Embedding-based (sentence-transformers `all-MiniLM-L6-v2`) with keyword overlap fallback if model unavailable |
| recency | 20% | Exponential decay: `exp(-age / 5)` |
| source | 15% | Lookup in `SOURCE_WEIGHTS` dict |
| citations | 15% | `log1p`-normalized citation count |

Model is lazy-loaded on first use; if `sentence-transformers` is not installed or fails, falls back silently to keyword overlap.

### Cache (`src/cache.py`)

SQLite via SQLAlchemy. TTL 24h, keyed by `query` string. Stores all `Article` fields including the extended ones. Has a `_migrate()` function that adds missing columns to existing databases (backward-compatible schema evolution).

### Web interface (`src/web.py`)

Streamlit single-page app (tabs: **Search**, **Export**, **Hidden**). All UI strings are English. Key design constraints:
- All search results are persisted in `st.session_state.results` — never re-fetch on widget events.
- Selected articles are persisted in `st.session_state.selected` (dict keyed by DOI or position index).
- Checkboxes use `sel_<key>` prefix in `st.session_state` so they can be cleared atomically.
- A new search clears all prior selection and checkbox state.
- `query_input` is widget-bound: never assign to it after the `st.text_input` is created. The recent-search buttons mutate it via an `on_click` callback (`_set_query_from_recent`), which runs before widgets instantiate.
- Provider failures are surfaced regardless of result count, so a full outage never looks like "no results for your filters".

Current features: search form with filters (year range, source, venue type, Open Access), recent-search shortcuts, optional topic clustering, ranked result cards with `st.expander("Details")`, per-card checkbox selection and hide, retraction flagging, CSV export of the shown results, an **Export** tab (TSV / xlsx / BibTeX), and a **Hidden** tab to restore hidden articles. The slider caps the displayed list.

### Environment

API keys are loaded from `.env` via `python-dotenv`. Required keys:
- `ELSEVIER_API_KEY`
- `ELSEVIER_INST_TOKEN`
- `IEEE_API_KEY`

Optional:
- `SEMANTIC_SCHOLAR_API_KEY` — higher rate limits for Semantic Scholar; if omitted, the anonymous tier is used
- `OPENALEX_EMAIL` — joins the OpenAlex polite pool (better rate limits)
- `HTTPX_VERIFY_SSL=false` — disable SSL verification for corporate proxies with self-signed certs
- `CACHE_DB` — path for the SQLite cache file (default: `academic_cache.db`)

All outbound HTTP goes through `src/utils.py`: `http_get()` (async, used by every
provider) and `http_get_sync()` (blocking, used by the Retraction Watch download).
Both honour `HTTPX_VERIFY_SSL` and retry once with verification off on
`CERTIFICATE_VERIFY_FAILED`. Never call `httpx` directly from a new module.

## Conventions

- No emojis in f-strings or print statements (Windows encoding issues)
- Use `uv add <package>` for new dependencies, never `pip install`
- Commits follow Conventional Commits: `feat:`, `fix:`, `chore:`, `refactor:`
- All providers must implement: `async def search(query: str, limit: int) -> list[Article]`
- All `Article` fields must be populated by every provider — use `None` for unavailable fields, never omit
- When adding a field to `Article`, update all providers and the cache schema simultaneously
- In `web.py`, always persist results to `st.session_state` before any widget interaction
- Run `uv run search main "machine learning" --limit 5` to smoke-test after changes

## Roadmap

**Status (2026-10-28): complete, in maintenance only.** OpenAlex, Elsevier/Scopus (the author has a working institutional key) and Semantic Scholar are in use. There is no IEEE key: treat the IEEE provider as untested against the live API and don't propose work that depends on it.

The roadmap and changelog live in **`ROADMAP.txt`** (repo root) — read it first and keep it current after each unit of work. Don't maintain a second copy here. Note one discarded idea worth not re-proposing: PT-BR abstract summarization via LLM was evaluated and dropped (hallucination risk once Elsevier abstracts were fixed) — see the DESCARTADAS section there.
