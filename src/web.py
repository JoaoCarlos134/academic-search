import asyncio
import csv
import io
import logging
import re
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# When Streamlit runs this file it adds src/ to sys.path, breaking `import src.*`.
# Insert the project root so the src package is always resolvable.
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

import httpx
import streamlit as st
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

import src.cache as cache
from src.cache import (
    clear_hidden,
    count_hidden,
    get_hidden_articles,
    get_hidden_dois,
    hide_article,
    invalidate as cache_invalidate,
    list_recent_queries,
    sweep_hidden,
    unhide_article,
)
from src.models import Article
from src.providers.elsevier import search as elsevier_search
from src.providers.ieee import search as ieee_search
from src.providers.openalex import search as openalex_search
from src.ranking import score_articles
from src.utils import dedup_articles
from src import retractions
from src.clustering import cluster_articles

# ── async orchestrator (same logic as cli.py) ─────────────────────────────────

_PROVIDER_NAMES = ("openalex", "elsevier", "ieee")


async def _search_all(
    query: str,
    limit: int,
    sources: set[str] | None = None,
) -> tuple[list[Article], list[str], dict[str, str]]:
    """Return (deduplicated articles, failed provider names, errors dict)."""
    _fns = [openalex_search, elsevier_search, ieee_search]
    tasks, names = [], []
    for name, fn in zip(_PROVIDER_NAMES, _fns):
        if sources is None or name in sources:
            tasks.append(fn(query, limit))
            names.append(name)

    raw = await asyncio.gather(*tasks, return_exceptions=True)
    articles: list[Article] = []
    failed: list[str] = []
    errors: dict[str, str] = {}
    for name, result in zip(names, raw):
        if isinstance(result, BaseException):
            failed.append(name)
            errors[name] = str(result)
            logger.warning("[%s] provider failed: %s", name, result)
            continue
        articles.extend(result)
    return dedup_articles(articles), failed, errors


# Sempre busca esse total internamente para ter margem de filtragem pós-fetch.
# O cache guarda todos; a UI fatia para o limit pedido pelo usuário.
_CACHE_FETCH_LIMIT = 50


def _fetch(
    query: str,
    limit: int,
    sources: set[str] | None = None,
) -> tuple[list[Article], bool, list[str], dict[str, str]]:
    """Returns (articles, from_cache, failed_providers, provider_errors).

    Articles are unfiltered (year/type/oa filters applied in the UI).
    The cache always stores up to _CACHE_FETCH_LIMIT results so changing
    filters doesn't require a new API round-trip.
    """
    cached = cache.get(query, min_size=min(limit, _CACHE_FETCH_LIMIT))
    if cached is not None:
        # Re-mark retractions in case the dataset was updated since the cache.
        retractions.mark_retracted(cached)
        return cached, True, [], {}
    articles, failed, errors = asyncio.run(
        _search_all(query, _CACHE_FETCH_LIMIT, sources=sources)
    )
    ranked = score_articles(articles, query)
    retractions.mark_retracted(ranked)
    cache.store(query, ranked)
    return ranked, False, failed, errors


# ── shared field helpers ───────────────────────────────────────────────────────

def _article_link(article: Article) -> str:
    """URL canonica: DOI → oa_url → vazio."""
    if article.doi:
        doi = article.doi
        return doi if doi.startswith("http") else f"https://doi.org/{doi}"
    return article.oa_url or ""


def _author_str(article: Article) -> str:
    """Primeiro autor; 'et al.' se houver mais de um."""
    if not article.authors:
        return ""
    if len(article.authors) == 1:
        return article.authors[0]
    return f"{article.authors[0]} et al."


def _content_str(article: Article) -> str:
    """Abstract truncado em 300 caracteres."""
    if not article.abstract:
        return ""
    if len(article.abstract) <= 300:
        return article.abstract
    return article.abstract[:300] + "..."


# ── export helpers ────────────────────────────────────────────────────────────

def _to_csv_bytes(articles: list[Article]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=["title", "year", "doi", "venue", "authors", "citations", "score", "source", "url"],
    )
    writer.writeheader()
    for a in articles:
        writer.writerow({
            "title": a.title,
            "year": a.year or "",
            "doi": a.doi or "",
            "venue": a.venue or "",
            "authors": "; ".join(a.authors),
            "citations": a.citations,
            "score": f"{a.score:.4f}",
            "source": a.source,
            "url": a.url or "",
        })
    return buf.getvalue().encode("utf-8")


def _tsv_clean(value) -> str:
    """Replace tab/newline chars so a single field never breaks the row layout."""
    if value is None:
        return ""
    s = str(value)
    return s.replace("\t", " ").replace("\r\n", " ").replace("\n", " ").replace("\r", " ")


def _bib_escape(s: str) -> str:
    """Escape & and % which unconditionally break .bib parsing when unescaped."""
    return s.replace("&", r"\&").replace("%", r"\%")


# ── export: aba Exportar (formato planilha) ────────────────────────────────────

_REF_HEADERS = ["Revista", "Autor", "Ano", "Conteudo", "Titulo", "Link"]


def _ref_row(article: Article) -> list:
    return [
        article.venue or "",
        _author_str(article),
        article.year if article.year else "",
        _content_str(article),
        article.title or "",
        _article_link(article),
    ]


def _to_ref_tsv(articles: list[Article]) -> str:
    lines = ["\t".join(_tsv_clean(v) for v in _ref_row(a)) for a in articles]
    return "\n".join(lines)


def _to_ref_xlsx(articles: list[Article]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Referencias"

    bold = Font(bold=True)
    gray = PatternFill(fill_type="solid", fgColor="D9D9D9")
    wrap = Alignment(wrap_text=True)

    for col, header in enumerate(_REF_HEADERS, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = bold
        cell.fill = gray

    for row_idx, a in enumerate(articles, 2):
        for col_idx, value in enumerate(_ref_row(a), 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            if col_idx in (4, 5):  # Conteudo, Titulo
                cell.alignment = wrap

    col_widths = [20, 25, 8, 50, 45, 40]
    for col_idx, width in enumerate(col_widths, 1):
        ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = width

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _export_bibtex_key(article: Article) -> str:
    surname = article.authors[0].split()[-1].capitalize() if article.authors else "Unknown"
    first_word = (
        re.sub(r"[^a-zA-Z]", "", (article.title or "").split()[0]).capitalize()
        if article.title else "Untitled"
    )
    return f"{surname}{article.year or '0000'}{first_word}"


def _to_export_bibtex(articles: list[Article]) -> str:
    buf = io.StringIO()
    for a in articles:
        key = _export_bibtex_key(a)
        authors = _bib_escape(" and ".join(a.authors)) if a.authors else "Unknown"
        link = _article_link(a)
        buf.write(f"@article{{{key},\n")
        buf.write(f"  author   = {{{authors}}},\n")
        buf.write(f"  title    = {{{_bib_escape(a.title or '')}}},\n")
        if a.venue:
            buf.write(f"  journal  = {{{_bib_escape(a.venue)}}},\n")
        if a.year:
            buf.write(f"  year     = {{{a.year}}},\n")
        if a.doi:
            buf.write(f"  doi      = {{{a.doi}}},\n")
        if link:
            buf.write(f"  url      = {{{link}}},\n")
        if a.abstract:
            buf.write(f"  abstract = {{{_bib_escape(a.abstract)}}},\n")
        if a.citations:
            buf.write(f"  note     = {{Citacoes: {a.citations}}},\n")
        buf.write("}\n\n")
    return buf.getvalue()


# ── selection helpers ─────────────────────────────────────────────────────────

def _hide_key(article: Article) -> str:
    """Chave estavel para ocultar: DOI ou hash do titulo (independente de posicao)."""
    if article.doi:
        return article.doi
    return f"notitle_{abs(hash(article.title or ''))}"


def _article_key(article: Article, idx: int) -> str:
    if article.doi:
        return article.doi
    return str(idx)


def _clear_selection() -> None:
    st.session_state.selected = {}
    for k in list(st.session_state.keys()):
        if k.startswith("sel_"):
            st.session_state[k] = False


# ── Streamlit UI ──────────────────────────────────────────────────────────────

def _retractions_sidebar() -> None:
    """Sidebar panel: cache status + manual download trigger for Retraction Watch."""
    with st.sidebar:
        st.subheader("Retraction Watch")
        if not retractions.is_cache_present():
            st.caption("Lista de retratacoes nao baixada (~64 MB).")
            if st.button("Baixar agora", key="btn_dl_retractions", use_container_width=True):
                with st.spinner("Baixando lista de retratacoes (pode levar ate 1 min)..."):
                    try:
                        size = retractions.download()
                        st.success(f"Baixado ({size // 1024 // 1024} MB).")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Falha no download: {exc}")
        else:
            age = retractions.cache_age_days() or 0
            n_dois = len(retractions.get_retracted_dois())
            stale = age >= 7
            st.caption(f"{n_dois:,} DOIs cadastrados | atualizado ha {age:.1f} dia(s)")
            if stale:
                st.warning("Lista desatualizada (>7 dias).")
            if st.button("Atualizar lista", key="btn_refresh_retractions", use_container_width=True):
                with st.spinner("Atualizando..."):
                    try:
                        retractions.download()
                        st.success("Lista atualizada.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Falha: {exc}")


def _app() -> None:
    st.set_page_config(page_title="Academic Search", layout="wide")
    st.title("Academic Search")
    _retractions_sidebar()

    # Initialize persistent state (outside tabs, always runs)
    if "results" not in st.session_state:
        st.session_state.results = []
    if "selected" not in st.session_state:
        st.session_state.selected = {}
    if "search_done" not in st.session_state:
        st.session_state.search_done = False
    if "search_error" not in st.session_state:
        st.session_state.search_error = None
    if "display_limit" not in st.session_state:
        st.session_state.display_limit = 10

    tab_busca, tab_export, tab_ocultos = st.tabs(["Busca", "Exportar", "Ocultos"])

    # ── constantes de filtro ──────────────────────────────────────────────────
    _ALL_SOURCES = ["openalex", "elsevier", "ieee"]
    _ALL_TYPES = ["journal", "conference", "repository", "book", "other"]
    _TYPE_LABELS = {
        "journal": "Journal",
        "conference": "Conference",
        "repository": "Repositorio (preprint)",
        "book": "Livro / Capitulo",
        "other": "Outro",
    }

    # ── aba Busca ─────────────────────────────────────────────────────────────
    with tab_busca:

        # ── filtros (sempre visiveis, antes da busca) ─────────────────────────
        with st.expander("Filtros de busca", expanded=False):
            f_col1, f_col2 = st.columns(2)
            with f_col1:
                f_year_from = st.number_input(
                    "Ano a partir de", min_value=1900, max_value=2100,
                    value=None, placeholder="qualquer", key="filter_year_from",
                )
                f_year_to = st.number_input(
                    "Ano ate", min_value=1900, max_value=2100,
                    value=None, placeholder="qualquer", key="filter_year_to",
                )
                f_oa = st.checkbox("Somente Open Access", key="filter_oa")
                f_cluster = st.checkbox(
                    "Agrupar por topico",
                    key="filter_cluster",
                    help="K-means sobre os embeddings dos resultados; rotulo via top palavras dos titulos.",
                )
            with f_col2:
                f_sources = st.multiselect(
                    "Fontes a consultar",
                    options=_ALL_SOURCES,
                    default=_ALL_SOURCES,
                    key="filter_sources",
                )
                f_types = st.multiselect(
                    "Tipo de publicacao",
                    options=_ALL_TYPES,
                    default=_ALL_TYPES,
                    format_func=lambda t: _TYPE_LABELS.get(t, t),
                    key="filter_types",
                )

        # Contar filtros nao-padrao para mostrar badge
        n_active = sum([
            f_year_from is not None,
            f_year_to is not None,
            f_oa,
            set(f_sources) != set(_ALL_SOURCES),
            set(f_types) != set(_ALL_TYPES),
        ])

        query = st.text_input(
            "Termo de busca",
            placeholder="ex: machine learning transformers",
            key="query_input",
        )

        # ── historico de buscas (pills clicaveis) ─────────────────────────────
        recent = list_recent_queries(limit=8)
        if recent:
            st.caption("Buscas recentes (clique para reusar):")
            cols = st.columns(min(len(recent), 4))
            for i, rq in enumerate(recent):
                with cols[i % len(cols)]:
                    if st.button(rq[:30] + ("..." if len(rq) > 30 else ""),
                                 key=f"recent_{i}", use_container_width=True):
                        st.session_state.query_input = rq
                        st.rerun()

        col_lim, col_btn, col_refresh = st.columns([3, 1, 1])
        with col_lim:
            limit = st.slider("Limite de resultados", min_value=5, max_value=50, value=10)
        with col_btn:
            st.write("")
            search_clicked = st.button("Buscar", use_container_width=True)
        with col_refresh:
            st.write("")
            refresh_clicked = st.button(
                "Atualizar",
                use_container_width=True,
                help="Invalida o cache desta query e busca de novo nas APIs.",
            )

        if n_active:
            st.info(f"{n_active} filtro(s) ativo(s) — a busca sera realizada ja com esses filtros aplicados.")

        if refresh_clicked and query.strip():
            removed = cache_invalidate(query.strip())
            st.toast(f"Cache invalidado ({removed} entrada(s)). Buscando de novo...")
            search_clicked = True  # cai no fluxo de busca abaixo

        if search_clicked:
            if not query.strip():
                st.warning("Digite um termo de busca.")
            else:
                with st.spinner("Buscando..."):
                    try:
                        active_sources = set(f_sources) if f_sources else set(_ALL_SOURCES)
                        results, from_cache, failed, errors = _fetch(
                            query.strip(), limit, sources=active_sources
                        )
                        hidden = get_hidden_dois()
                        visible = [a for a in results if _hide_key(a) not in hidden]
                        st.session_state.results = visible
                        st.session_state.hidden_count = len(results) - len(visible)
                        st.session_state.from_cache = from_cache
                        st.session_state.failed_providers = failed
                        st.session_state.provider_errors = errors
                        st.session_state.search_error = None
                    except httpx.TimeoutException:
                        st.session_state.results = []
                        st.session_state.search_error = "Tempo de resposta excedido. Verifique sua conexao e tente novamente."
                    except Exception as exc:
                        st.session_state.results = []
                        st.session_state.search_error = str(exc)
                    finally:
                        st.session_state.search_done = True
                        st.session_state.display_limit = limit
                        st.session_state.selected = {}
                        for k in list(st.session_state.keys()):
                            if k.startswith("sel_"):
                                del st.session_state[k]
                # Força novo script run limpo antes de renderizar os checkboxes.
                # Sem isso, o Streamlit pode reutilizar o estado interno do widget
                # anterior para o primeiro artigo (mesma chave no run anterior),
                # causando dessincronizacao apenas no primeiro item da lista.
                st.rerun()

        if st.session_state.search_done:
            if err := st.session_state.get("search_error"):
                st.warning(err)
            else:
                results: list[Article] = st.session_state.results

                # aplicar filtros pos-fetch (ano, tipo, oa) sobre o pool completo
                def _apply_filters(articles: list[Article]) -> list[Article]:
                    out = []
                    for a in articles:
                        if f_year_from is not None and (a.year is None or a.year < f_year_from):
                            continue
                        if f_year_to is not None and (a.year is None or a.year > f_year_to):
                            continue
                        if f_oa and not a.is_oa:
                            continue
                        if set(f_types) != set(_ALL_TYPES) and (a.venue_type or "other") not in f_types:
                            continue
                        out.append(a)
                    return out

                filtered = _apply_filters(results)
                display_limit = st.session_state.display_limit
                sliced = filtered[:display_limit]

                if not sliced:
                    st.warning("Nenhum resultado encontrado com os filtros atuais.")
                else:
                    # ── header: count + export CSV ────────────────────────────────────────
                    header_col, export_col = st.columns([4, 1])
                    with header_col:
                        origin = "cache" if st.session_state.get("from_cache") else "API"
                        pool = len(results)
                        if len(filtered) < pool:
                            total_label = f"{len(sliced)} de {len(filtered)} (filtrado de {pool})"
                        else:
                            total_label = str(len(sliced))
                        st.caption(f"{total_label} artigos encontrados  ({origin})")
                        hidden_count = st.session_state.get("hidden_count", 0)
                        if hidden_count:
                            st.caption(f"{hidden_count} artigo(s) oculto(s) nesta busca")
                        failed = st.session_state.get("failed_providers") or []
                        if failed:
                            errors = st.session_state.get("provider_errors") or {}
                            lines = []
                            for p in failed:
                                msg = errors.get(p, "erro desconhecido")
                                lines.append(f"**{p}**: {msg}")
                            st.warning(
                                "Provedores indisponiveis nesta busca (resultados parciais):\n\n"
                                + "\n\n".join(lines)
                            )
                    with export_col:
                        st.download_button(
                            label="Exportar CSV",
                            data=_to_csv_bytes(sliced),
                            file_name="resultados.csv",
                            mime="text/csv",
                            use_container_width=True,
                        )

                    # ── selection bar ─────────────────────────────────────────────────────
                    selected = st.session_state.selected
                    sel_info_col, sel_btn_col = st.columns([4, 1])
                    with sel_info_col:
                        st.caption(f"{len(selected)} artigo(s) selecionado(s)")
                    with sel_btn_col:
                        if selected:
                            if st.button("Limpar selecao", key="btn_clear_selection", use_container_width=True):
                                _clear_selection()
                                st.rerun()

                    # ── article cards (agrupados por topico se f_cluster) ─────────────
                    if f_cluster:
                        groups = cluster_articles(sliced)
                    else:
                        groups = [{"label": None, "articles": sliced}]

                    i = 0
                    for grp in groups:
                        if grp["label"] is not None and len(groups) > 1:
                            st.markdown(f"### {grp['label']}  *(n={len(grp['articles'])})*")
                        for a in grp["articles"]:
                            i += 1
                            article_key = _article_key(a, i)
                            hide_key = _hide_key(a)
                            cb_key = f"sel_{article_key}"
                            with st.container(border=True):
                                title_col, sel_col, hide_col = st.columns([6, 2, 1])
                                with title_col:
                                    title = a.title or "(sem titulo)"
                                    link = _article_link(a)
                                    prefix = ":red[**[RETRATADO]**] " if a.retracted else ""
                                    if link:
                                        st.markdown(f"{prefix}**{i}. [{title}]({link})**")
                                    else:
                                        st.markdown(f"{prefix}**{i}. {title}**")
                                with sel_col:
                                    is_checked = st.checkbox("Selecionar", key=cb_key)
                                with hide_col:
                                    if st.button("Ocultar", key=f"hide_{article_key}"):
                                        hide_article(hide_key, a.title or "")
                                        st.session_state.results = [
                                            r for r in st.session_state.results
                                            if _hide_key(r) != hide_key
                                        ]
                                        st.session_state.selected.pop(article_key, None)
                                        st.session_state.pop(cb_key, None)
                                        st.rerun()

                                # Sync checkbox state → selected dict
                                if is_checked:
                                    st.session_state.selected[article_key] = a
                                else:
                                    st.session_state.selected.pop(article_key, None)

                                left, right = st.columns([3, 1])
                                with left:
                                    if a.authors:
                                        st.caption(", ".join(a.authors))
                                    meta = " | ".join(filter(None, [a.venue, str(a.year) if a.year else None]))
                                    if meta:
                                        st.caption(meta)
                                with right:
                                    st.caption(f"Score: {a.score:.3f}")
                                    st.caption(f"Citacoes: {a.citations}  |  [{a.source}]")

                                with st.expander("Detalhes", expanded=False):
                                    if a.venue_type:
                                        st.write(f"Tipo de publicacao: {a.venue_type}")
                                    if a.publisher:
                                        st.write(f"Editora: {a.publisher}")
                                    if a.is_oa is not None:
                                        st.write(f"Acesso aberto: {'Sim' if a.is_oa else 'Nao'}")
                                    if a.oa_url:
                                        st.link_button("Acessar versao aberta", a.oa_url)
                                    if a.concepts:
                                        st.write(f"Topicos: {' | '.join(a.concepts)}")
                                    if a.abstract:
                                        st.caption(a.abstract)
                                    else:
                                        st.caption("Abstract nao disponivel")

                    # ── paginacao dentro do pool cacheado ─────────────────────────────
                    has_more = len(filtered) > display_limit
                    if has_more or display_limit > limit:
                        pg_col, reset_col = st.columns([3, 1])
                        with pg_col:
                            if has_more:
                                remaining = len(filtered) - display_limit
                                if st.button(
                                    f"Mostrar mais ({min(10, remaining)} de {remaining} restantes)",
                                    key="btn_show_more",
                                ):
                                    st.session_state.display_limit += 10
                                    st.rerun()
                            else:
                                st.caption("Todos os resultados do pool estao exibidos.")
                        with reset_col:
                            if display_limit > limit:
                                if st.button("Recolher", key="btn_collapse", use_container_width=True):
                                    st.session_state.display_limit = limit
                                    st.rerun()

                    if st.session_state.selected:
                        st.divider()
                        st.info(
                            f"{len(st.session_state.selected)} artigo(s) selecionado(s). "
                            "Va para a aba **Exportar** para baixar em TSV, xlsx ou BibTeX."
                        )

    # ── aba Exportar ──────────────────────────────────────────────────────────
    with tab_export:
        selected_list = list(st.session_state.selected.values())

        if not selected_list:
            st.warning("Nenhum artigo selecionado. Volte a aba Busca e marque os artigos.")
        else:
            n = len(selected_list)
            st.write(f"{n} artigo(s) selecionado(s) para exportacao")

            ocultar_apos = st.checkbox(
                "Ocultar artigos selecionados apos exportar",
                value=True,
                key="ocultar_apos_export",
            )

            fmt = st.radio(
                "Formato de exportacao",
                [
                    "TSV para Excel (colar direto na planilha)",
                    "Arquivo .xlsx (download direto)",
                    "BibTeX para Overleaf (.bib)",
                ],
            )

            st.divider()

            def _do_hide_selected() -> None:
                for article in selected_list:
                    hide_article(_hide_key(article), article.title or "")
                st.session_state.selected = {}
                st.info("Artigos exportados foram marcados como ocultos.")

            if fmt == "TSV para Excel (colar direto na planilha)":
                st.write("Copie o texto abaixo e cole na primeira linha vazia da sua planilha")
                st.text_area(
                    label="TSV para colar na planilha",
                    value=_to_ref_tsv(selected_list),
                    height=200,
                    key="tsv_output",
                    label_visibility="collapsed",
                )
                st.caption(
                    "O campo Conteudo e o abstract original. Revise e adapte para o "
                    "seu resumo em portugues antes de colar na planilha."
                )
                if ocultar_apos:
                    if st.button("Confirmar exportacao e ocultar artigos", key="btn_hide_tsv"):
                        _do_hide_selected()
                        st.rerun()

            elif fmt == "Arquivo .xlsx (download direto)":
                xlsx_clicked = st.download_button(
                    label="Baixar .xlsx",
                    data=_to_ref_xlsx(selected_list),
                    file_name="referencias_academicas.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
                if xlsx_clicked and ocultar_apos:
                    _do_hide_selected()
                    st.rerun()

            else:  # BibTeX
                bib = _to_export_bibtex(selected_list)
                st.write("Copie e cole no seu arquivo .bib no Overleaf ou Zotero")
                st.code(bib, language=None)
                bib_clicked = st.download_button(
                    label="Baixar referencias.bib",
                    data=bib.encode("utf-8"),
                    file_name="referencias.bib",
                    mime="text/plain",
                )
                if bib_clicked and ocultar_apos:
                    _do_hide_selected()
                    st.rerun()

    # ── aba Ocultos ───────────────────────────────────────────────────────────
    with tab_ocultos:
        ocultos = get_hidden_articles()
        total_hidden = len(ocultos)

        if not ocultos:
            st.info("Nenhum artigo oculto.")
        else:
            top_col, sweep_col, btn_col = st.columns([3, 1, 1])
            with top_col:
                st.write(f"{total_hidden} artigo(s) oculto(s)")
                if total_hidden >= 100:
                    st.warning(f"{total_hidden} artigos ocultos. Use 'Limpar antigos' para remover os de mais de 90 dias.")
            with sweep_col:
                if st.button("Limpar antigos (90d)", key="btn_sweep_hidden", use_container_width=True):
                    removed = sweep_hidden(days=90)
                    st.toast(f"{removed} artigo(s) removido(s).")
                    st.rerun()
            with btn_col:
                if st.button("Limpar todos", key="btn_clear_hidden", use_container_width=True):
                    clear_hidden()
                    st.rerun()

            for entry in ocultos:
                title_display = (entry["title"] or entry["doi"])[:80]
                hidden_at = entry.get("hidden_at")
                row_col, restore_col = st.columns([5, 1])
                with row_col:
                    st.write(title_display)
                    if hidden_at:
                        st.caption(f"Oculto em {hidden_at.strftime('%d/%m/%Y')}")
                with restore_col:
                    if st.button("Restaurar", key=f"restore_{entry['doi']}"):
                        unhide_article(entry["doi"])
                        st.rerun()


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    """Usado pelo script `academic-web` para iniciar o servidor Streamlit."""
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", __file__],
        check=True,
    )


# Streamlit re-executa este arquivo a cada interacao do usuario.
# O guard garante que _app() so roda dentro de uma sessao Streamlit ativa,
# evitando erros quando o modulo e importado pelo script `academic-web`.
# IMPORTANTE: capturamos apenas ImportError (esperado fora do contexto Streamlit).
# Erros dentro de _app() devem propagar para nao serem silenciados.
try:
    from streamlit.runtime.scriptrunner import get_script_run_ctx as _get_ctx
except ImportError:
    _get_ctx = None

if _get_ctx is not None and _get_ctx() is not None:
    _app()
