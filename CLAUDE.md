# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies
uv sync

# Run the CLI
search main "machine learning"
search main "neural networks" --limit 20 --year-from 2020 --source openalex --oa-only

# Run the web UI
academic-web   # opens http://localhost:8501

# Run tests
uv run pytest tests/ -v

# Lint / format
ruff check src/
ruff check --fix src/
ruff format src/
```

Python version is managed via `.python-version` (currently 3.14).

## Architecture

Two entry points share the same core:

- **CLI** — `search main <query>` (defined in `pyproject.toml` → `src.cli:app`)
- **Web UI** — `academic-web` → Streamlit app at `src/web.py`

**Data flow:** `cli.py` or `web.py` → `cache.py` (hit?) → `providers/*.py` (fetch) → `ranking.py` (score) → display

### Core model

`src/models.py` defines `Article`, the single shared dataclass all providers must produce.
Key fields: `title`, `year`, `doi`, `abstract`, `authors`, `venue`, `source`, `url`,
`citations`, `score`, `venue_type`, `publisher`, `is_oa`, `oa_url`, `concepts`.

Deduplication (`utils.dedup_articles`) keys by normalized DOI first, then title.
DOI normalization strips `https://doi.org/` and lowercases — needed because OpenAlex returns
full URLs while Elsevier/IEEE return bare DOIs.

### Providers (`src/providers/`)

Each provider is `async def search(query: str, limit: int) -> list[Article]`.
All three are called concurrently in `_search_all()` in both `cli.py` and `web.py`.

- **OpenAlex** (`openalex.py`): free, no key. `OPENALEX_EMAIL` enables polite pool.
  DOIs come as full `https://doi.org/...` URLs. Abstracts reconstructed from inverted index.
- **Elsevier** (`elsevier.py`): requires `ELSEVIER_API_KEY` + `ELSEVIER_INST_TOKEN`.
  Uses `prism:aggregationType` (not `subtypeDescription`) for `venue_type`.
  Returns bare DOIs; `_normalize_doi` handles the mismatch with OpenAlex at dedup time.
- **IEEE** (`ieee.py`): requires `IEEE_API_KEY`. Returns empty list when key is absent.

When adding a new provider: add to `_search_all()` in both `cli.py` and `web.py`,
register `source` string in `ranking.py`'s `SOURCE_WEIGHTS`, ensure DOIs pass through
`_normalize_doi` so dedup works cross-provider.

### Ranking (`src/ranking.py`)

Weighted sum of four components:

| Component  | Base weight | Method |
|------------|-------------|--------|
| similarity | 50%         | Cosine (sentence-transformers all-MiniLM-L6-v2) or keyword overlap fallback |
| recency    | 20%         | Exponential decay `exp(-age / 5)` |
| source     | 15%         | `SOURCE_WEIGHTS` dict lookup |
| citations  | 15%         | `log1p`-normalized |

For papers ≤3 years old the recency/citations budget (35%) splits 75/25 in favor of
recency (young papers haven't accumulated citations yet).

Sentence-transformers loads lazily; on failure logs a warning and falls back to keywords.
`_embedding_cache` (module-level dict) maps DOI/title → tensor to avoid re-encoding
articles already seen in the current process lifetime.

### Cache (`src/cache.py`)

SQLite via SQLAlchemy. Two tables:

- **`article_cache`** — search results keyed by query string, TTL 24h.
  `cache.get(query, min_size=N)` returns `None` if fewer than N rows exist (forces re-fetch
  when user requests more results than were originally cached).
  `_CACHE_FETCH_LIMIT = 50` in `web.py` — always fetches 50 internally so filter changes
  don't require a new API round-trip.
- **`hidden_articles`** — per-article hide state, persistent (no TTL).
  `sweep_hidden(days=90)` removes entries older than N days.
  `count_hidden()` for quick total.

All datetimes stored naive UTC — SQLite does not preserve `tzinfo`.
DB path defaults to `<repo>/academic_cache.db`; override with `CACHE_DB` env var.

### Web UI (`src/web.py`)

Streamlit app with three tabs:

- **Busca** — filter panel (always visible, pre-search), query input, limit slider, results
  with "Mostrar mais" pagination (operates within cached pool, no extra API calls),
  article cards with hide/select/details.
- **Exportar** — export selected articles as TSV, .xlsx, or BibTeX.
- **Ocultos** — manage hidden articles; "Limpar antigos (90d)" sweep button;
  warning shown when total exceeds 100.

Source filter in the filter panel skips excluded providers at fetch time (real pre-fetch).
Year/type/open-access filters apply post-fetch over the cached pool.
`display_limit` in `session_state` tracks pagination state; resets on new search.

### CLI (`src/cli.py`)

```
search main <query> [OPTIONS]
  --limit / -n       Number of results (default 10)
  --output / -o      Save to file
  --format / -f      csv | bibtex
  --year-from        Min publication year (post-fetch filter)
  --year-to          Max publication year (post-fetch filter)
  --source / -s      Filter by provider (repeatable: -s openalex -s ieee)
  --oa-only          Open access only

search clear-cache   Remove all cached search results
```

### Tests (`tests/`)

46 tests, all passing. Run with `uv run pytest tests/ -v`.

- `test_utils.py` — `_normalize_doi` (8 cases), `dedup_articles` (7 cases)
- `test_ranking.py` — keyword similarity, recency, citations normalization, `score_articles` edge cases
- `test_cache.py` — get/set roundtrip, TTL, `min_size`, hidden article CRUD

### Environment

All keys loaded from `.env` via `python-dotenv`:

| Variable             | Required | Purpose |
|----------------------|----------|---------|
| `ELSEVIER_API_KEY`   | for Elsevier | Scopus Search API key |
| `ELSEVIER_INST_TOKEN`| for Elsevier | Scopus institution token |
| `IEEE_API_KEY`       | for IEEE | IEEE Xplore API key |
| `OPENALEX_EMAIL`     | no | Polite pool (better rate limits) |
| `HTTPX_VERIFY_SSL`   | no | Set `false` for corporate proxies |
| `CACHE_DB`           | no | Override SQLite path |

## Conventions

- No emojis in f-strings or print statements (Windows encoding issues)
- Use `uv add <package>` for new dependencies, never `pip install`
- Commits follow Conventional Commits: `feat:`, `fix:`, `chore:`
- All providers must implement: `async def search(query: str, limit: int) -> list[Article]`
- Use `logger = logging.getLogger(__name__)` for warnings — no bare `print()` in src/
- Run `uv run pytest tests/ -q` before committing
- Smoke-test with `search main "machine learning" --limit 5` after provider changes

## Workflow obrigatorio antes de qualquer alteracao

1. **Ler o codigo relevante primeiro** — antes da primeira edicao em qualquer sessao,
   leia os arquivos que serao tocados. Nunca edite sem ter lido o estado atual.

2. **Atualizar ROADMAP.txt** sempre que:
   - Um bug for encontrado → adicionar em `ALTA PRIORIDADE` ou `MEDIA PRIORIDADE`
     com `[ ]` e descricao clara do problema e impacto.
   - Uma etapa nova for identificada → adicionar na secao de prioridade correta.
   - Um item for concluido → marcar `[x]` e mover para a secao `CONCLUIDO`
     com a data no formato `AAAA-MM-DD`.
   - O trabalho estiver em andamento → marcar `[-]`.

   Formato de entrada no ROADMAP.txt:
   ```
   [ ] Titulo curto
       Descricao do problema ou etapa. Impacto esperado.
   ```
