import asyncio
import csv
import io
import re
import subprocess
import sys
from pathlib import Path

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
    get_hidden_articles,
    get_hidden_dois,
    hide_article,
    unhide_article,
)
from src.models import Article
from src.providers.elsevier import search as elsevier_search
from src.providers.ieee import search as ieee_search
from src.providers.openalex import search as openalex_search
from src.ranking import score_articles


# ── async orchestrator (same logic as cli.py) ─────────────────────────────────

async def _search_all(query: str, limit: int) -> list[Article]:
    openalex, elsevier, ieee = await asyncio.gather(
        openalex_search(query, limit),
        elsevier_search(query, limit),
        ieee_search(query, limit),
    )
    return openalex + elsevier + ieee


def _fetch(query: str, limit: int) -> tuple[list[Article], bool]:
    cached = cache.get(query)
    if cached is not None:
        return cached, True
    articles = asyncio.run(_search_all(query, limit))
    ranked = score_articles(articles, query)
    cache.set(query, ranked)
    return ranked, False


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


# ── export: aba Busca (formato legado) ────────────────────────────────────────

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


def _to_tsv_str(articles: list[Article]) -> str:
    buf = io.StringIO()
    buf.write("titulo\tano\tautores\tvenue\tdoi\tcitacoes\tscore\tfonte\n")
    for a in articles:
        buf.write("\t".join([
            a.title or "",
            str(a.year) if a.year else "",
            "; ".join(a.authors),
            a.venue or "",
            a.doi or "",
            str(a.citations),
            f"{a.score:.4f}",
            a.source,
        ]) + "\n")
    return buf.getvalue()


def _bibtex_key(article: Article) -> str:
    surname = article.authors[0].split()[-1] if article.authors else "unknown"
    first_word = re.sub(r"[^a-zA-Z]", "", (article.title or "").split()[0]) if article.title else "untitled"
    return f"{surname.lower()}{article.year or '0000'}{first_word.lower()}"


def _to_bibtex_str(articles: list[Article]) -> str:
    buf = io.StringIO()
    for a in articles:
        authors = " and ".join(a.authors) if a.authors else "Unknown"
        buf.write(f"@article{{{_bibtex_key(a)},\n")
        buf.write(f"  title   = {{{a.title}}},\n")
        buf.write(f"  author  = {{{authors}}},\n")
        if a.year:
            buf.write(f"  year    = {{{a.year}}},\n")
        if a.doi:
            buf.write(f"  doi     = {{{a.doi}}},\n")
        if a.venue:
            buf.write(f"  journal = {{{a.venue}}},\n")
        buf.write("}\n\n")
    return buf.getvalue()


def _abnt_author(name: str) -> str:
    parts = name.strip().split()
    if len(parts) == 1:
        return parts[0].upper()
    return f"{parts[-1].upper()}, {' '.join(parts[:-1])}"


def _to_abnt_str(articles: list[Article]) -> str:
    entries = []
    for a in articles:
        if not a.authors:
            author_str = "AUTOR DESCONHECIDO"
        elif len(a.authors) <= 3:
            author_str = "; ".join(_abnt_author(n) for n in a.authors)
        else:
            author_str = _abnt_author(a.authors[0]) + " et al."

        venue_year = ", ".join(filter(None, [a.venue, str(a.year) if a.year else None]))
        doi_str = f" DOI: {a.doi}." if a.doi else ""
        entries.append(f"{author_str.rstrip('.')}. {a.title or 'Sem titulo'}. {venue_year}.{doi_str}")
    return "\n\n".join(entries)


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
    lines = ["\t".join(str(v) for v in _ref_row(a)) for a in articles]
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
        authors = " and ".join(a.authors) if a.authors else "Unknown"
        link = _article_link(a)
        buf.write(f"@article{{{key},\n")
        buf.write(f"  author   = {{{authors}}},\n")
        buf.write(f"  title    = {{{a.title or ''}}},\n")
        if a.venue:
            buf.write(f"  journal  = {{{a.venue}}},\n")
        if a.year:
            buf.write(f"  year     = {{{a.year}}},\n")
        if a.doi:
            buf.write(f"  doi      = {{{a.doi}}},\n")
        if link:
            buf.write(f"  url      = {{{link}}},\n")
        if a.abstract:
            buf.write(f"  abstract = {{{a.abstract}}},\n")
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

def _app() -> None:
    st.set_page_config(page_title="Academic Search", layout="wide")
    st.title("Academic Search")

    # Initialize persistent state (outside tabs, always runs)
    if "results" not in st.session_state:
        st.session_state.results = []
    if "selected" not in st.session_state:
        st.session_state.selected = {}
    if "search_done" not in st.session_state:
        st.session_state.search_done = False
    if "search_error" not in st.session_state:
        st.session_state.search_error = None

    tab_busca, tab_export, tab_ocultos = st.tabs(["Busca", "Exportar", "Ocultos"])

    # ── aba Busca ─────────────────────────────────────────────────────────────
    with tab_busca:
        query = st.text_input("Termo de busca", placeholder="ex: machine learning transformers")
        col_lim, col_btn = st.columns([4, 1])
        with col_lim:
            limit = st.slider("Limite de resultados", min_value=5, max_value=50, value=10)
        with col_btn:
            st.write("")  # alinha verticalmente com o slider
            search_clicked = st.button("Buscar", use_container_width=True)

        if search_clicked:
            if not query.strip():
                st.warning("Digite um termo de busca.")
            else:
                with st.spinner("Buscando..."):
                    try:
                        results, from_cache = _fetch(query.strip(), limit)
                        hidden = get_hidden_dois()
                        visible = [a for a in results if _hide_key(a) not in hidden]
                        st.session_state.results = visible
                        st.session_state.hidden_count = len(results) - len(visible)
                        st.session_state.from_cache = from_cache
                        st.session_state.search_error = None
                    except httpx.TimeoutException:
                        st.session_state.results = []
                        st.session_state.search_error = "Tempo de resposta excedido. Verifique sua conexao e tente novamente."
                    except Exception as exc:
                        st.session_state.results = []
                        st.session_state.search_error = str(exc)
                    finally:
                        st.session_state.search_done = True
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
                if not results:
                    st.warning("Nenhum resultado encontrado.")
                else:
                    sliced = results[:limit]

                    # ── header: count + export CSV ────────────────────────────────────────
                    header_col, export_col = st.columns([4, 1])
                    with header_col:
                        origin = "cache" if st.session_state.get("from_cache") else "API"
                        st.caption(f"{len(sliced)} artigos encontrados  ({origin})")
                        hidden_count = st.session_state.get("hidden_count", 0)
                        if hidden_count:
                            st.caption(f"{hidden_count} artigo(s) oculto(s) nesta busca")
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

                    # ── article cards ─────────────────────────────────────────────────────
                    for i, a in enumerate(sliced, 1):
                        article_key = _article_key(a, i)
                        hide_key = _hide_key(a)
                        cb_key = f"sel_{article_key}"
                        with st.container(border=True):
                            title_col, sel_col, hide_col = st.columns([6, 2, 1])
                            with title_col:
                                title = a.title or "(sem titulo)"
                                link = _article_link(a)
                                if link:
                                    st.markdown(f"**{i}. [{title}]({link})**")
                                else:
                                    st.markdown(f"**{i}. {title}**")
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
                                    st.session_state[cb_key] = False
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

                    # ── exportar selecionados (secao legada) ──────────────────────────────
                    if st.session_state.selected:
                        selected_list = list(st.session_state.selected.values())
                        n = len(selected_list)
                        st.divider()
                        st.subheader(f"Exportar {n} artigo(s) selecionado(s)")
                        tab_tsv, tab_bib, tab_abnt = st.tabs(["TSV", "BibTeX", "ABNT"])
                        with tab_tsv:
                            st.caption("Cole diretamente no Excel ou LibreOffice Calc (Ctrl+V).")
                            st.code(_to_tsv_str(selected_list), language=None)
                        with tab_bib:
                            st.caption("Importe no Mendeley, Zotero ou compile com LaTeX.")
                            st.code(_to_bibtex_str(selected_list), language="bibtex")
                        with tab_abnt:
                            st.caption("Citacoes no formato ABNT NBR 6023 (aproximado).")
                            st.code(_to_abnt_str(selected_list), language=None)

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
                    label="",
                    value=_to_ref_tsv(selected_list),
                    height=200,
                    key="tsv_output",
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

        if not ocultos:
            st.info("Nenhum artigo oculto.")
        else:
            top_col, btn_col = st.columns([4, 1])
            with top_col:
                st.write(f"{len(ocultos)} artigo(s) oculto(s)")
            with btn_col:
                if st.button("Limpar todos os ocultos", key="btn_clear_hidden", use_container_width=True):
                    clear_hidden()
                    st.rerun()

            for entry in ocultos:
                title_display = (entry["title"] or entry["doi"])[:80]
                row_col, restore_col = st.columns([5, 1])
                with row_col:
                    st.write(title_display)
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
try:
    from streamlit.runtime.scriptrunner import get_script_run_ctx as _get_ctx

    if _get_ctx() is not None:
        _app()
except Exception:
    pass
