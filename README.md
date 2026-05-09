# Academic Search

Buscador de artigos academicos com ranking semantico, deduplicacao
cross-provider, deteccao de retratacoes e exportacao para CSV/xlsx/BibTeX.
Funciona como CLI ou interface web (Streamlit).

Provedores: **OpenAlex** (gratis), **Elsevier/Scopus** (chave institucional),
**IEEE Xplore** (chave gratis em developer.ieee.org).

---

## Para quem vai usar

### Pre-requisitos

- Python 3.11 ou superior (3.14 atual no `.python-version`)
- [uv](https://docs.astral.sh/uv/) instalado (ou `pip` + `venv`)

### Instalacao

```bash
git clone https://github.com/JoaoCarlos134/academic-search.git
cd academic-search
uv sync
```

### Configuracao das chaves de API

Copie o `.env.example` para `.env` e preencha:

```bash
cp .env.example .env
```

| Variavel             | Obrigatoria? | Como obter |
|----------------------|--------------|------------|
| `OPENALEX_EMAIL`     | nao          | Qualquer email — coloca voce na "polite pool" do OpenAlex (rate limit melhor) |
| `ELSEVIER_API_KEY`   | so para Scopus | https://dev.elsevier.com/ (precisa filiacao academica) |
| `ELSEVIER_INST_TOKEN`| so para Scopus | Sua instituicao fornece |
| `IEEE_API_KEY`       | so para IEEE   | https://developer.ieee.org/ (gratis) |
| `HTTPX_VERIFY_SSL`   | nao          | Defina `false` se estiver atras de proxy corporativo |
| `CACHE_DB`           | nao          | Caminho alternativo do SQLite (default `<repo>/academic_cache.db`) |

Sem chaves, OpenAlex funciona normalmente; os outros provedores ficam
inativos sem alarde (so logam debug).

### Como usar — CLI

```bash
# Busca simples
uv run search main "machine learning"

# Com filtros
uv run search main "neural networks" --limit 20 --year-from 2022 --oa-only

# So OpenAlex (economiza quota Elsevier/IEEE)
uv run search main "transformers" -s openalex

# Salvar em arquivo
uv run search main "deep learning" -o resultados.csv
uv run search main "deep learning" -o refs.bib --format bibtex

# Limpar cache de buscas
uv run search clear-cache

# Baixar lista de retratacoes (~64 MB, 1 vez por semana)
uv run search refresh-retractions
```

Flags do `search main`:

| Flag                  | Descricao |
|-----------------------|-----------|
| `--limit / -n`        | Numero de resultados (default 10) |
| `--output / -o`       | Arquivo de saida |
| `--format / -f`       | `csv` ou `bibtex` |
| `--year-from`         | Ano minimo |
| `--year-to`           | Ano maximo |
| `--source / -s`       | Filtrar por fonte (repetivel: `-s openalex -s ieee`) |
| `--oa-only`           | Somente artigos Open Access |

### Como usar — Web

```bash
uv run academic-web
```

Abre `http://localhost:8501`. A interface tem 3 abas:

- **Busca** — campo de pesquisa, filtros (ano, fonte, tipo, OA, agrupamento por
  topico), pills de buscas recentes, botao "Atualizar" para invalidar cache,
  paginacao "Mostrar mais"
- **Exportar** — selecionados na aba Busca podem ser exportados como TSV
  (colar no Excel), .xlsx ou BibTeX (Overleaf/Zotero)
- **Ocultos** — gerenciar artigos que voce escolheu nao ver de novo

A sidebar tem um painel "Retraction Watch" para baixar/atualizar a base de
papers retratados (~64 MB).

---

## Para quem vai desenvolver

### Estrutura do projeto

```
academic-search/
|-- src/
|   |-- cli.py             # entry point CLI (Typer)
|   |-- web.py             # entry point Web (Streamlit)
|   |-- models.py          # dataclass Article (modelo unico compartilhado)
|   |-- cache.py           # SQLite via SQLAlchemy: cache de buscas, ocultos, embeddings
|   |-- ranking.py         # score = similaridade + recencia + fonte + citacoes
|   |-- utils.py           # http_get, _normalize_doi, dedup_articles
|   |-- retractions.py     # Retraction Watch CSV downloader + matcher
|   |-- clustering.py      # KMeans + auto-K via silhouette para agrupar por topico
|   `-- providers/
|       |-- openalex.py    # OpenAlex Works API (gratis)
|       |-- elsevier.py    # Scopus Search API
|       `-- ieee.py        # IEEE Xplore API
|-- tests/                 # pytest, 74 testes
|-- pyproject.toml         # deps + scripts (search, academic-web)
|-- ROADMAP.txt            # itens pendentes/concluidos com complexidade
|-- CLAUDE.md              # guia de arquitetura para Claude Code (e devs)
|-- .env.example           # template das variaveis de ambiente
`-- main.py                # stub do `uv init`, NAO usado
```

### O que cada arquivo de `src/` faz

| Arquivo            | Responsabilidade |
|--------------------|------------------|
| `cli.py`           | Comandos `search main`, `clear-cache`, `refresh-retractions`. Aplica filtros pos-busca. |
| `web.py`           | App Streamlit. 3 abas (Busca/Exportar/Ocultos), filtros pre+pos-busca, paginacao, exports. |
| `models.py`        | `@dataclass Article` — campos compartilhados por todos os providers. Hash/eq normalizam DOI. |
| `cache.py`         | 3 tabelas: `article_cache` (TTL 24h), `hidden_articles` (sem TTL, sweep manual), `article_embeddings` (vetores BLOB). |
| `ranking.py`       | `score_articles()` ordena por similaridade (50%, embedding ou keyword fallback) + recencia (20%) + fonte (15%) + citacoes (15%). Cache 3-camadas para embeddings. |
| `utils.py`         | `http_get()` com fallback SSL, `_normalize_doi()` (resolve URL prefix vs bare), `dedup_articles()` (DOI-then-title). |
| `retractions.py`   | Cache local do CSV Retraction Watch, `mark_retracted(articles)` faz lookup O(1). |
| `clustering.py`    | KMeans com auto-K (silhouette score), labels inferidos por top tokens dos titulos. |
| `providers/*.py`   | Cada um implementa `async def search(query, limit) -> list[Article]`. |

### Fluxo de dados

```
usuario -> cli.py / web.py
       -> cache.get(query)  -- hit? retorna direto
                            -- miss:
       -> _search_all() -> providers/*.py (concorrente via asyncio.gather)
                        -> dedup_articles() (utils.py)
                        -> score_articles() (ranking.py)
                        -> mark_retracted() (retractions.py, opcional)
                        -> cache.store()
       -> cluster_articles() (clustering.py, opcional)
       -> renderizacao
```

### Comandos uteis para desenvolvimento

```bash
# Rodar todos os testes
uv run pytest tests/ -v

# Rodar so um modulo
uv run pytest tests/test_cache.py -v

# Lint
uv run ruff check src/
uv run ruff check --fix src/

# Format
uv run ruff format src/

# Smoke test apos mudancas em provider
uv run search main "machine learning" --limit 5

# Adicionar dependencia
uv add <pacote>     # NAO use pip install
```

### Workflow obrigatorio antes de editar (resumo do CLAUDE.md)

1. **Ler o codigo relevante primeiro** — antes da primeira edicao, abra os
   arquivos que vao ser tocados.
2. **Atualizar `ROADMAP.txt`** quando: encontrar bug, identificar etapa nova,
   concluir item (mover para historico com data).

### Convencoes

- Sem emojis em strings/prints (Windows tem problema de encoding)
- Conventional Commits: `feat:`, `fix:`, `chore:`, `docs:`, `refactor:`
- Toda funcao publica de provider: `async def search(query: str, limit: int) -> list[Article]`
- Use `logger = logging.getLogger(__name__)` — sem `print()` direto em `src/`
- Datetimes no SQLite: sempre naive UTC (use `cache._utcnow()`)
- DOI: passe sempre por `_normalize_doi()` antes de comparar/hash

### Como adicionar um novo provider

1. Criar `src/providers/novo.py` com `async def search(query, limit) -> list[Article]`
2. Registrar em `_search_all()` em `src/cli.py` E `src/web.py`
3. Adicionar `"novo"` em `_PROVIDER_NAMES` (em ambos os arquivos)
4. Adicionar em `SOURCE_WEIGHTS` em `src/ranking.py`
5. Adicionar em `_ALL_SOURCES` em `src/web.py` (filtro UI)
6. Garantir que DOIs sao normalizados via `_normalize_doi` para dedup
   funcionar cross-provider

### Onde olhar pra contribuir

- **`ROADMAP.txt`** — item P7 pendente (renovar IEEE API key, externo).
  Todos os outros pendentes estao na secao "Pos-mediums vale considerar".
- **`CLAUDE.md`** — visao detalhada de arquitetura para qualquer assistente
  ou dev novo no projeto.

---

## Arquivos auxiliares na raiz

| Arquivo            | Para que serve |
|--------------------|----------------|
| `pyproject.toml`   | Dependencias e scripts (`search`, `academic-web`) |
| `uv.lock`          | Lock file do uv (commitar sempre que rodar `uv add`) |
| `.env`             | Suas chaves de API (NAO commitar — esta no `.gitignore`) |
| `.env.example`     | Template das variaveis |
| `.python-version`  | Versao do Python (usado pelo uv) |
| `academic_cache.db`| SQLite local (ignorado pelo git) |
| `retractions_cache.csv` | CSV do Retraction Watch (ignorado) |
| `ROADMAP.txt`      | Itens pendentes e historico de mudancas |
| `CLAUDE.md`        | Guia de arquitetura |
| `main.py`          | Stub do `uv init`, nao e usado em lugar nenhum |
