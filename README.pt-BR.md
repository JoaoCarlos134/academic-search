# Academic Search

[English](README.md) · **Português (pt-BR)**

Busca em quatro bases academicas ao mesmo tempo, colapsa as duplicatas que elas
retornam e ranqueia o resto por uma combinacao ponderada de similaridade
semantica, recencia, autoridade da fonte e numero de citacoes — com artigos
retratados sinalizados automaticamente contra a base do Retraction Watch.

Funciona como CLI ou app web (Streamlit). Exporta para CSV, TSV, xlsx e BibTeX.

![Interface de busca com resultados ranqueados](docs/screenshots/01-search-overview.jpg)

## Por que isso existe

Buscar literatura em OpenAlex, Scopus, IEEE Xplore e Semantic Scholar significa
quatro abas, quatro formatos de resultado e o mesmo artigo aparecendo tres vezes
com metadados ligeiramente diferentes. Cada base tambem tem a sua propria nocao
de relevancia, e nenhuma delas avisa que o artigo que voce acabou de citar foi
retratado no ano passado.

Esta ferramenta consulta as quatro em paralelo, funde os resultados em uma unica
lista ranqueada e devolve algo que da para colar direto no Excel ou no Overleaf.

## O que e tecnicamente dificil aqui

| | |
|---|---|
| **Deduplicacao cross-provider** | O mesmo artigo chega de quatro APIs com quatro formatos de metadado. A dedup usa o DOI normalizado como chave (formas com prefixo de URL e "cruas" resolvem para a mesma chave) e cai para o titulo normalizado quando o provedor omite o DOI. A ordem dos provedores decide qual copia sobrevive, entao o registro mais rico vence. |
| **Ranking ponderado** | Os resultados sao pontuados como similaridade 50% + recencia 20% + autoridade da fonte 15% + citacoes 15%. A similaridade e um embedding do sentence-transformers (`all-MiniLM-L6-v2`) contra a query, com fallback de sobreposicao de keywords se o modelo nao carregar — a ferramenta degrada em vez de quebrar. A recencia decai exponencialmente; as citacoes sao normalizadas com `log1p` para que um survey de 10.000 citacoes nao achate todo o resto. |
| **Deteccao de retratacoes** | O conjunto de DOIs do Retraction Watch (via Crossref) e cacheado localmente e comparado em O(1) contra cada resultado, entao um artigo retratado e rotulado antes de voce cita-lo. |
| **Isolamento de falhas** | Os provedores rodam em paralelo sob `asyncio.gather(return_exceptions=True)`. Uma chave de API morta ou um 429 degrada apenas aquela fonte e aparece na UI — nunca vira um silencioso "nenhum resultado". |
| **Folga para o ranking** | O `--limit` limita a lista *final*, nao a busca. Cada provedor recebe um pedido de `min(limit * 3, 100)` candidatos para o ranking ter de onde escolher: fetch → dedup → rank → filtros → truncar. |

## Fontes

> **Status do projeto (outubro de 2026): concluido.** Na pratica so o OpenAlex (e o
> Semantic Scholar no nivel anonimo) e usado — o autor nunca obteve chaves da Elsevier
> ou do IEEE e nao pretende obter. Esses dois provedores estao implementados, mas nao
> foram testados com chaves reais. A ferramenta atende o que o autor precisa assim.

| Provedor | Cobertura | Chave necessaria |
|----------|-----------|------------------|
| **OpenAlex** | ~250M trabalhos, catalogo aberto | Nenhuma — defina `OPENALEX_EMAIL` para entrar na polite pool |
| **Elsevier / Scopus** | Scopus Search API | `ELSEVIER_API_KEY` + `ELSEVIER_INST_TOKEN` (institucional) |
| **IEEE Xplore** | Periodicos e conferencias IEEE | `IEEE_API_KEY` (gratis em developer.ieee.org) |
| **Semantic Scholar** | Graph API, cobertura forte em CS | `SEMANTIC_SCHOLAR_API_KEY` opcional (senao usa o tier anonimo) |

Sem nenhuma chave, OpenAlex e Semantic Scholar continuam funcionando; os outros
ficam inativos sem derrubar a busca.

## Mais capturas de tela

<table>
<tr>
<td width="50%"><img src="docs/screenshots/02-ranked-results.jpg" alt="Cards de resultado ranqueados"><br><sub>Cards ranqueados com score, veiculo, ano, numero de citacoes e fonte de origem.</sub></td>
<td width="50%"><img src="docs/screenshots/03-result-details.jpg" alt="Detalhes do resultado expandidos"><br><sub>Detalhes expandidos: tipo de publicacao, editora, link de acesso aberto, topicos, resumo.</sub></td>
</tr>
<tr>
<td width="50%"><img src="docs/screenshots/04-filters.jpg" alt="Filtros de busca"><br><sub>Filtros pre-busca: faixa de anos, quais provedores consultar, tipo de publicacao, somente OA, agrupamento por topico.</sub></td>
<td width="50%"><img src="docs/screenshots/05-export-bibtex.jpg" alt="Exportacao BibTeX"><br><sub>Selecione artigos e exporte como BibTeX para Overleaf ou Zotero — tambem TSV para Excel e .xlsx.</sub></td>
</tr>
</table>

### Deteccao de retratacoes

Buscar o estudo Surgisphere/Lancet sobre COVID-19 — retratado em 2020 e ainda
com mais de 1.200 citacoes — sinaliza o artigo antes de voce cita-lo:

![Artigo retratado sinalizado na lista de resultados](docs/screenshots/06-retraction-flag.jpg)

> **Nota:** a interface e as mensagens do CLI estao em ingles. Apenas esta
> traducao do README esta em portugues.

## Como comecar

**Pre-requisitos:** Python 3.11+ (3.14 fixado no `.python-version`) e
[uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/JoaoCarlos134/academic-search.git
cd academic-search
uv sync
```

Depois copie o template de ambiente e preencha as chaves que voce tiver:

```bash
cp .env.example .env
```

| Variavel | Obrigatoria? | Como obter |
|----------|--------------|------------|
| `OPENALEX_EMAIL` | Nao | Qualquer email — coloca voce na polite pool do OpenAlex (rate limit melhor) |
| `ELSEVIER_API_KEY` | So para Scopus | https://dev.elsevier.com/ (precisa de filiacao academica) |
| `ELSEVIER_INST_TOKEN` | So para Scopus | Fornecido pela Elsevier para ativar a API |
| `IEEE_API_KEY` | So para IEEE | https://developer.ieee.org/ (gratis) |
| `SEMANTIC_SCHOLAR_API_KEY` | Nao | https://www.semanticscholar.org/product/api — aumenta o rate limit |
| `HTTPX_VERIFY_SSL` | Nao | Defina `false` atras de proxy corporativo com certificado self-signed |
| `CACHE_DB` | Nao | Caminho alternativo do SQLite (default `academic_cache.db`) |

### CLI

```bash
# Busca simples
uv run search main "machine learning"

# Com filtros
uv run search main "neural networks" --limit 20 --year-from 2022 --oa-only

# So um provedor (economiza quota Elsevier/IEEE)
uv run search main "transformers" -s openalex

# Salvar em arquivo
uv run search main "deep learning" -o resultados.csv
uv run search main "deep learning" -o refs.bib --format bibtex

# Limpar o cache local de buscas
uv run search clear-cache

# Baixar a base de retratacoes (~64 MB, atualizar semanalmente)
uv run search refresh-retractions
```

Flags do `search main`:

| Flag | Descricao |
|------|-----------|
| `--limit / -n` | Numero de resultados (default 10) |
| `--output / -o` | Arquivo de saida |
| `--format / -f` | `csv` ou `bibtex` |
| `--year-from` | Ano minimo de publicacao |
| `--year-to` | Ano maximo de publicacao |
| `--source / -s` | Filtrar por fonte (repetivel: `-s openalex -s ieee`) |
| `--oa-only` | Somente artigos Open Access |

### Web

```bash
uv run academic-web
```

Abre `http://localhost:8501`. Tres abas (rotulos em ingles na interface):

- **Search** (Busca) — campo de pesquisa, filtros (ano, fonte, tipo de
  publicacao, OA, agrupamento por topico), atalhos de buscas recentes, botao
  "Refresh" para invalidar o cache e cards ranqueados com selecionar/ocultar.
- **Export** (Exportar) — tudo o que foi selecionado na aba Search, como TSV
  (colar no Excel), .xlsx ou BibTeX (Overleaf / Zotero).
- **Hidden** (Ocultos) — restaurar artigos que voce escolheu nao ver de novo.

A sidebar tem o painel do Retraction Watch para baixar e atualizar a base de
artigos retratados.

## Como funciona

```
cli.py / web.py
  └─ cache.get(query)              hit → retorna
                                   miss ↓
  └─ _search_all()  ──┬─ openalex ─┐
                      ├─ elsevier ─┤ asyncio.gather (paralelo, falhas isoladas)
                      ├─ ieee ─────┤
                      └─ semantic ─┘
        └─ dedup_articles()        DOI normalizado, depois titulo normalizado
        └─ score_articles()        similaridade + recencia + fonte + citacoes
        └─ mark_retracted()        lookup no Retraction Watch
        └─ cache.store()           SQLite, TTL 24h
  └─ cluster_articles()            KMeans opcional com K automatico
  └─ renderizacao / exportacao
```

Pesos do score, em `src/ranking.py`:

| Componente | Peso | Metodo |
|------------|------|--------|
| Similaridade | 50% | sentence-transformers `all-MiniLM-L6-v2`, fallback por keywords |
| Recencia | 20% | `exp(-idade / 5)` |
| Fonte | 15% | Peso de autoridade por provedor |
| Citacoes | 15% | Normalizado com `log1p` |

## Stack

Python 3.14 · httpx (async) · SQLAlchemy + SQLite · sentence-transformers ·
scikit-learn · Typer + Rich · Streamlit · pytest (74 testes) · ruff · uv

## Contribuindo

Notas de arquitetura, responsabilidade de cada modulo, convencoes e o passo a
passo para adicionar um novo provedor estao em
[CONTRIBUTING.md](CONTRIBUTING.md) (em ingles). Itens abertos e historico de
mudancas em [`ROADMAP.txt`](ROADMAP.txt).
