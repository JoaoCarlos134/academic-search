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

Each provider is an async function `search(query, limit) -> list[Article]` that hits one external API and maps the response to `Article`. All three (openalex, elsevier, ieee) are implemented and called concurrently from `_search_all()` in both `cli.py` and `web.py`.

- **OpenAlex** (`openalex.py`): free, no key required. `OPENALEX_EMAIL` env var enables the polite pool with better rate limits. DOIs come pre-formatted as `https://doi.org/...` URLs. Abstracts are reconstructed from OpenAlex's inverted index format.
- **Elsevier** (`elsevier.py`): requires `ELSEVIER_API_KEY` and `ELSEVIER_INST_TOKEN` from `.env`. Bare DOIs (no URL prefix) — the `_normalize_doi` helper in `utils.py` reconciles this with OpenAlex's URL-prefixed DOIs during dedup.
- **IEEE** (`ieee.py`): requires `IEEE_API_KEY` from `.env`. Returns empty list when key is absent.

When adding a new provider, add it to `_search_all()` in both `cli.py` and `web.py`, register its `source` string in `ranking.py`'s `SOURCE_WEIGHTS`, and ensure DOIs are normalized through `_normalize_doi` for dedup to work.

### Ranking (`src/ranking.py`)

Scores each `Article` as a weighted sum of four components:

| Component | Base weight | Method |
|-----------|-------------|--------|
| similarity | 50% | Embedding cosine similarity if sentence-transformers loads, else keyword overlap |
| recency | 20% | Exponential decay: `exp(-age / 5)` |
| source | 15% | Lookup in `SOURCE_WEIGHTS` dict |
| citations | 15% | `log1p`-normalized citation count |

For papers ≤3 years old, the recency/citations budget (35% combined) is redistributed 75/25 in favor of recency, since young papers haven't had time to accumulate citations.

The sentence-transformers model loads lazily on first call. If the import fails (no model cached, no internet), it logs a warning and falls back to keyword similarity.

### Environment

API keys are loaded from `.env` (via `python-dotenv`). Variables:
- `ELSEVIER_API_KEY`, `ELSEVIER_INST_TOKEN` — Scopus
- `IEEE_API_KEY` — IEEE Xplore
- `OPENALEX_EMAIL` — optional, enables polite pool
- `HTTPX_VERIFY_SSL=false` — disable SSL verification (corporate proxies)
- `CACHE_DB` — override sqlite path (default: `<repo>/academic_cache.db`)

### Cache (`src/cache.py`)

SQLite via SQLAlchemy. `article_cache` table holds search results keyed by query string with 24h TTL. `hidden_articles` table tracks per-article hide state (no TTL, persistent). All datetimes are stored naive in UTC — SQLite does not preserve `tzinfo`.

## Conventions

- No emojis in f-strings or print statements (Windows encoding issues)
- Use `uv add <package>` for new dependencies, never `pip install`
- Commits follow Conventional Commits: feat:, fix:, chore:
- All providers must implement: `async def search(query: str, limit: int) -> list[Article]`
- Run `search "machine learning" --limit 5` to smoke-test after changes

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

## Next steps (priority order)
1. Tests — there are none yet. Start with `dedup_articles`, `_normalize_doi`, ranking edge cases (empty query, no articles), and cache get/set roundtrip.
2. Cache `limit` awareness — cached results are stored complete, but a query cached with `limit=10` then re-queried with `limit=50` only returns 10. Either re-fetch when requested limit > cached size, or store unbounded and slice on read.
3. Hidden articles cleanup — `hidden_articles` table grows unbounded and has no UI for bulk export/import. Consider a soft-delete TTL or "archive after 90 days" sweep.
4. Better failure UI — when a provider fails, the warning lists names but not why. Surface the first error string per provider for easier diagnosis.
5. Embedding cache — sentence-transformers re-encodes the same articles on every search. Persist embeddings keyed by DOI to skip recomputation.

## Known limitations / surface-level cleanups
- `print()` warnings should move to `logging` (cli prints rich, web prints to stdout/streamlit logs).
- `cache.py` raw SQL `INSERT OR IGNORE` was replaced with ORM `session.get` + insert; remaining raw `text()` is only the `_migrate` ALTER TABLE.
