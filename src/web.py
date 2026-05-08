import asyncio
import csv
import io
import subprocess
import sys

import httpx
import streamlit as st

import src.cache as cache
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


# ── export ────────────────────────────────────────────────────────────────────

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


# ── selection helpers ─────────────────────────────────────────────────────────

def _article_key(article: Article) -> str:
    if article.doi:
        return article.doi
    return f"noDOI_{abs(hash(article.title))}_{article.year}"


def _clear_selection() -> None:
    st.session_state.selected = {}
    for k in list(st.session_state.keys()):
        if k.startswith("sel_"):
            st.session_state[k] = False


# ── Streamlit UI ──────────────────────────────────────────────────────────────

def _app() -> None:
    st.set_page_config(page_title="Academic Search", layout="wide")
    st.title("Academic Search")

    # Initialize persistent state without overwriting existing values
    if "results" not in st.session_state:
        st.session_state.results = []
    if "selected" not in st.session_state:
        st.session_state.selected = {}
    if "search_done" not in st.session_state:
        st.session_state.search_done = False

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
                    st.session_state.results = results
                    st.session_state.from_cache = from_cache
                except httpx.TimeoutException:
                    st.warning("Tempo de resposta excedido. Verifique sua conexao e tente novamente.")
                    st.session_state.results = []
                except Exception as exc:
                    st.warning(f"Erro ao buscar resultados: {exc}")
                    st.session_state.results = []
                finally:
                    st.session_state.search_done = True
                    # Drop selection and checkbox state from previous search
                    st.session_state.selected = {}
                    for k in list(st.session_state.keys()):
                        if k.startswith("sel_"):
                            del st.session_state[k]

    if not st.session_state.search_done:
        return

    results: list[Article] = st.session_state.results

    if not results:
        st.warning("Nenhum resultado encontrado.")
        return

    sliced = results[:limit]

    # ── header: count + export CSV ────────────────────────────────────────────
    header_col, export_col = st.columns([4, 1])
    with header_col:
        origin = "cache" if st.session_state.get("from_cache") else "API"
        st.caption(f"{len(sliced)} artigos encontrados  ({origin})")
    with export_col:
        st.download_button(
            label="Exportar CSV",
            data=_to_csv_bytes(sliced),
            file_name="resultados.csv",
            mime="text/csv",
            use_container_width=True,
        )

    # ── selection bar ─────────────────────────────────────────────────────────
    selected = st.session_state.selected
    sel_info_col, sel_btn_col = st.columns([4, 1])
    with sel_info_col:
        st.caption(f"{len(selected)} artigo(s) selecionado(s)")
    with sel_btn_col:
        if selected:
            if st.button("Limpar selecao", key="btn_clear_selection", use_container_width=True):
                _clear_selection()
                st.rerun()

    # ── article cards ─────────────────────────────────────────────────────────
    for i, a in enumerate(sliced, 1):
        article_key = _article_key(a)
        with st.container(border=True):
            title_col, sel_col = st.columns([5, 1])
            with title_col:
                title = a.title or "(sem titulo)"
                if a.doi:
                    doi_url = a.doi if a.doi.startswith("http") else f"https://doi.org/{a.doi}"
                    st.markdown(f"**{i}. [{title}]({doi_url})**")
                else:
                    st.markdown(f"**{i}. {title}**")
            with sel_col:
                cb_key = f"sel_{article_key}"
                if cb_key not in st.session_state:
                    st.session_state[cb_key] = False
                is_checked = st.checkbox("Selecionar", key=cb_key)

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
