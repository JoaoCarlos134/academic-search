# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies (editable mode)
uv pip install -e .

# Run the CLI
uv run search "machine learning"
uv run search "neural networks" --limit 20

# Web interface
uv run streamlit run src/web.py

# Lint / format
ruff check src/
ruff check --fix src/
ruff format src/

# Smoke-test after changes
uv run search "machine learning" --limit 5
```

Python version is managed via `.python-version` (currently 3.14). There are no automated tests yet.

## Architecture

Academic paper search engine with CLI and web (Streamlit) interfaces.

**Data flow:** `cli.py` / `web.py` → `providers/*.py` (async fetch in parallel) → `ranking.py` (score) → display / export

**Entry points:**
- CLI: `search` command (defined in `pyproject.toml` → `src.cli:app`)
- Web: `uv run streamlit run src/web.py` or `uv run academic-web`

### Core model (`src/models.py`)

`Article` is the single shared dataclass all providers must produce. Deduplication uses DOI when available, falling back to title.

Fields: `title`, `year`, `doi`, `abstract`, `authors`, `venue`, `source`, `url`, `citations`, `score`, `venue_type`, `publisher`, `is_oa`, `oa_url`, `concepts`.

### Providers (`src/providers/`)

Each provider is `async def search(query: str, limit: int) -> list[Article]`. All three run in parallel via `asyncio.gather` in `_search_all()`.

| Provider | Status | Key required |
|----------|--------|--------------|
| `openalex.py` | Functional — all `Article` fields populated | None (polite pool via `mailto`) |
| `elsevier.py` | Functional — Scopus Search API | `ELSEVIER_API_KEY`, `ELSEVIER_INST_TOKEN` |
| `ieee.py` | Functional — but `venue_type`, `publisher`, `is_oa`, `oa_url`, `concepts` not yet extracted | `IEEE_API_KEY` |

**OpenAlex note:** set the `EMAIL` constant in `openalex.py` to a real address for better rate limits. Currently uses placeholder.

**Elsevier note:** year filter uses `date.today().year + 2` to allow in-press articles.

When adding a new provider: add it to `_search_all()` in both `cli.py` and `web.py`, and register its `source` string in `ranking.py`'s `SOURCE_WEIGHTS`.

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

Streamlit single-page app. Key design constraints:
- All search results are persisted in `st.session_state.results` — never re-fetch on widget events.
- Selected articles are persisted in `st.session_state.selected` (dict keyed by DOI or position index).
- Checkboxes use `sel_<key>` prefix in `st.session_state` so they can be cleared atomically.
- A new search clears all prior selection and checkbox state.

Current features: search form, results cards with `st.expander("Detalhes")`, checkbox selection per card, selection counter, "Limpar selecao" button, export full results as CSV.

### Environment

API keys are loaded from `.env` via `python-dotenv`. Required keys:
- `ELSEVIER_API_KEY`
- `ELSEVIER_INST_TOKEN`
- `IEEE_API_KEY`

Optional:
- `HTTPX_VERIFY_SSL=false` — disable SSL verification for corporate proxies with self-signed certs
- `CACHE_DB` — path for the SQLite cache file (default: `academic_cache.db`)

## Conventions

- No emojis in f-strings or print statements (Windows encoding issues)
- Use `uv add <package>` for new dependencies, never `pip install`
- Commits follow Conventional Commits: `feat:`, `fix:`, `chore:`, `refactor:`
- All providers must implement: `async def search(query: str, limit: int) -> list[Article]`
- All `Article` fields must be populated by every provider — use `None` for unavailable fields, never omit
- When adding a field to `Article`, update all providers and the cache schema simultaneously
- In `web.py`, always persist results to `st.session_state` before any widget interaction
- Run `uv run search "machine learning" --limit 5` to smoke-test after changes

## Next steps (priority order)

1. **Export selecionados na web** — nova secao em `web.py` que aparece quando `st.session_state.selected` nao esta vazio; gera tres formatos em `st.code` com botao de copia nativo:
   - TSV (cola direto no Excel/LibreOffice respeitando colunas)
   - BibTeX (para Mendeley/Zotero/LaTeX; reusar `_write_bibtex` de `cli.py`)
   - ABNT (citacao formatada em string: `SOBRENOME, Nome. Titulo. Venue, ano.`)

2. **Completar `ieee.py`** — extrair `venue_type`, `publisher`, `is_oa`, `oa_url`, `concepts` da resposta da IEEE Xplore API para manter schema consistente com OpenAlex e Elsevier

3. **EMAIL em `openalex.py`** — substituir placeholder `"seu@email.com"` pelo email real do usuario (joaojachenskijunior@gmail.com)

4. **Resumo em PT via LLM (opcional)** — botao "Gerar resumo" por artigo em `web.py`; comprime o abstract em 2-3 frases em portugues usando a API do Claude (`claude-haiku-4-5-20251001` por custo); resultado cacheado no SQLite para nao regerar; adicionar campo `summary_pt: str | None = None` no `Article` e na tabela de cache
