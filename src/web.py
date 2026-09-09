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
from src.providers.semanticscholar import search as semanticscholar_search
from src.ranking import score_articles
from src.utils import dedup_articles
from src import retractions
from src.clustering import cluster_articles

# ── async orchestrator (same logic as cli.py) ─────────────────────────────────

_PROVIDER_NAMES = ("openalex", "elsevier", "ieee", "semanticscholar")


async def _search_all(
    query: str,
    limit: int,
    sources: set[str] | None = None,
) -> tuple[list[Article], list[str], dict[str, str], int]:
    """Return (deduplicated articles, failed provider names, errors dict, total_raw_count)."""
    _fns = [openalex_search, elsevier_search, ieee_search, semanticscholar_search]
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
    total_raw = len(articles)
    return dedup_articles(articles), failed, errors, total_raw


def _fetch(
    query: str,
    limit: int,
    sources: set[str] | None = None,
) -> tuple[list[Article], bool, list[str], dict[str, str], int, int]:
    """Returns (articles, from_cache, failed_providers, provider_errors, total_raw, total_dedup).

    Fetches min(limit * 3, 100) candidates per provider so the filter pipeline
    can still deliver `limit` visible results after post-fetch filtering.
    The UI truncates to the real limit after applying filters.
    """
    fetch_limit = min(limit * 3, 100)
    cached = cache.get(query, min_size=fetch_limit)
    if cached is not None:
        retractions.mark_retracted(cached)
        return cached, True, [], {}, 0, len(cached)
    articles, failed, errors, total_raw = asyncio.run(
        _search_all(query, fetch_limit, sources=sources)
    )
    ranked = score_articles(articles, query)
    retractions.mark_retracted(ranked)
    cache.store(query, ranked)
    return ranked, False, failed, errors, total_raw, len(ranked)


# ── shared field helpers ───────────────────────────────────────────────────────

def _article_link(article: Article) -> str:
    """Canonical URL: DOI -> oa_url -> empty."""
    if article.doi:
        doi = article.doi
        return doi if doi.startswith("http") else f"https://doi.org/{doi}"
    return article.oa_url or ""


def _author_str(article: Article) -> str:
    """First author; 'et al.' when there is more than one."""
    if not article.authors:
        return ""
    if len(article.authors) == 1:
        return article.authors[0]
    return f"{article.authors[0]} et al."


def _content_str(article: Article) -> str:
    """Abstract truncated to 300 characters."""
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


# ── export: Export tab (spreadsheet format) ────────────────────────────────────

_REF_HEADERS = ["Journal", "Author", "Year", "Content", "Title", "Link"]


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
    ws.title = "References"

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
            if col_idx in (4, 5):  # Content, Title
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
            buf.write(f"  note     = {{Cited by: {a.citations}}},\n")
        buf.write("}\n\n")
    return buf.getvalue()


# ── selection helpers ─────────────────────────────────────────────────────────

def _hide_key(article: Article) -> str:
    """Stable hide key: DOI, or a hash of the title (position-independent)."""
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
            st.caption("Retraction list not downloaded (~64 MB).")
            if st.button("Download now", key="btn_dl_retractions", use_container_width=True):
                with st.spinner("Downloading retraction list (may take up to 1 min)..."):
                    try:
                        size = retractions.download()
                        st.success(f"Downloaded ({size // 1024 // 1024} MB).")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Download failed: {exc}")
        else:
            age = retractions.cache_age_days() or 0
            n_dois = len(retractions.get_retracted_dois())
            stale = age >= 7
            st.caption(f"{n_dois:,} DOIs indexed | updated {age:.1f} day(s) ago")
            if stale:
                st.warning("List is out of date (>7 days).")
            if st.button("Refresh list", key="btn_refresh_retractions", use_container_width=True):
                with st.spinner("Refreshing..."):
                    try:
                        retractions.download()
                        st.success("List updated.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Failed: {exc}")


def _app() -> None:
    st.set_page_config(page_title="Academic Search", layout="wide")
    st.title("Academic Search")
    _retractions_sidebar()

    # Initialize persistent state (outside tabs, always runs)
    if "query_input" not in st.session_state:
        st.session_state.query_input = ""
    if "results" not in st.session_state:
        st.session_state.results = []
    if "selected" not in st.session_state:
        st.session_state.selected = {}
    if "search_done" not in st.session_state:
        st.session_state.search_done = False
    if "search_error" not in st.session_state:
        st.session_state.search_error = None
    if "total_raw" not in st.session_state:
        st.session_state.total_raw = 0
    if "total_dedup" not in st.session_state:
        st.session_state.total_dedup = 0

    tab_search, tab_export, tab_hidden = st.tabs(["Search", "Export", "Hidden"])

    # ── filter constants ──────────────────────────────────────────────────
    _ALL_SOURCES = ["openalex", "elsevier", "ieee", "semanticscholar"]
    _ALL_TYPES = ["journal", "conference", "repository", "book", "other"]
    _TYPE_LABELS = {
        "journal": "Journal",
        "conference": "Conference",
        "repository": "Repository (preprint)",
        "book": "Book / Chapter",
        "other": "Other",
    }

    # ── Search tab ─────────────────────────────────────────────────────────────
    with tab_search:

        # ── filters (always visible, applied before the search) ─────────────────────────
        with st.expander("Search filters", expanded=False):
            f_col1, f_col2 = st.columns(2)
            with f_col1:
                f_year_from = st.number_input(
                    "Year from", min_value=1900, max_value=2100,
                    value=None, placeholder="any", key="filter_year_from",
                )
                f_year_to = st.number_input(
                    "Year to", min_value=1900, max_value=2100,
                    value=None, placeholder="any", key="filter_year_to",
                )
                f_oa = st.checkbox("Open Access only", key="filter_oa")
                f_cluster = st.checkbox(
                    "Group by topic",
                    key="filter_cluster",
                    help="K-means over the result embeddings; label derived from the top title words.",
                )
            with f_col2:
                f_sources = st.multiselect(
                    "Sources to query",
                    options=_ALL_SOURCES,
                    default=_ALL_SOURCES,
                    key="filter_sources",
                )
                f_types = st.multiselect(
                    "Publication type",
                    options=_ALL_TYPES,
                    default=_ALL_TYPES,
                    format_func=lambda t: _TYPE_LABELS.get(t, t),
                    key="filter_types",
                )

        # Count non-default filters so the badge can be shown
        n_active = sum([
            f_year_from is not None,
            f_year_to is not None,
            f_oa,
            set(f_sources) != set(_ALL_SOURCES),
            set(f_types) != set(_ALL_TYPES),
        ])

        def _set_query_from_recent(value: str) -> None:
            st.session_state.query_input = value

        query = st.text_input(
            "Search term",
            placeholder="e.g. machine learning transformers",
            key="query_input",
        )

        # ── search history (clickable pills) ─────────────────────────────
        recent = list_recent_queries(limit=8)
        if recent:
            st.caption("Recent searches (click to reuse):")
            cols = st.columns(min(len(recent), 4))
            for i, rq in enumerate(recent):
                with cols[i % len(cols)]:
                    st.button(
                        rq[:30] + ("..." if len(rq) > 30 else ""),
                        key=f"recent_{i}",
                        use_container_width=True,
                        on_click=_set_query_from_recent,
                        args=(rq,),
                    )

        col_lim, col_btn, col_refresh = st.columns([3, 1, 1])
        with col_lim:
            limit = st.slider("Result limit", min_value=5, max_value=50, value=10)
        with col_btn:
            st.write("")
            search_clicked = st.button("Search", use_container_width=True)
        with col_refresh:
            st.write("")
            refresh_clicked = st.button(
                "Refresh",
                use_container_width=True,
                help="Invalidate the cache for this query and re-query the APIs.",
            )

        if n_active:
            st.info(f"{n_active} active filter(s) — the search will run with these already applied.")

        if refresh_clicked and query.strip():
            removed = cache_invalidate(query.strip())
            st.toast(f"Cache invalidated ({removed} entry/entries). Searching again...")
            search_clicked = True  # falls through into the search flow below

        if search_clicked:
            if not query.strip():
                st.warning("Enter a search term.")
            else:
                with st.spinner("Searching..."):
                    try:
                        active_sources = set(f_sources) if f_sources else set(_ALL_SOURCES)
                        results, from_cache, failed, errors, total_raw, total_dedup = _fetch(
                            query.strip(), limit, sources=active_sources
                        )
                        hidden = get_hidden_dois()
                        visible = [a for a in results if _hide_key(a) not in hidden]
                        st.session_state.results = visible
                        st.session_state.total_raw = total_raw
                        st.session_state.total_dedup = total_dedup
                        st.session_state.hidden_count = len(results) - len(visible)
                        st.session_state.from_cache = from_cache
                        st.session_state.failed_providers = failed
                        st.session_state.provider_errors = errors
                        st.session_state.search_error = None
                    except httpx.TimeoutException:
                        st.session_state.results = []
                        st.session_state.search_error = "Request timed out. Check your connection and try again."
                    except Exception as exc:
                        st.session_state.results = []
                        st.session_state.search_error = str(exc)
                    finally:
                        st.session_state.search_done = True
                        st.session_state.selected = {}
                        for k in list(st.session_state.keys()):
                            if k.startswith("sel_"):
                                del st.session_state[k]
                # Force a clean script run before rendering the checkboxes.
                # Without it Streamlit may reuse the previous run's internal widget
                # state for the first article (same key as the previous run),
                # desynchronizing only the first item in the list.
                st.rerun()

        if st.session_state.search_done:
            if err := st.session_state.get("search_error"):
                st.warning(err)
            else:
                results: list[Article] = st.session_state.results

                # apply post-fetch filters (year, type, OA) over the full pool
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
                sliced = filtered[:limit]

                # Provider failures surface regardless of result count, so a full
                # outage never disguises itself as "no results".
                failed = st.session_state.get("failed_providers") or []
                if failed:
                    errors = st.session_state.get("provider_errors") or {}
                    lines = [f"**{p}**: {errors.get(p, 'unknown error')}" for p in failed]
                    st.warning(
                        "Providers unavailable for this search (partial results):\n\n"
                        + "\n\n".join(lines)
                    )

                if not sliced:
                    st.warning("No results found with the current filters.")
                else:
                    # ── header: count + export CSV ────────────────────────────────────────
                    header_col, export_col = st.columns([4, 1])
                    with header_col:
                        origin = "cache" if st.session_state.get("from_cache") else "API"
                        total_raw = st.session_state.get("total_raw", 0)
                        total_dedup = st.session_state.get("total_dedup", len(results))
                        n = len(sliced)
                        # The limit caps what is displayed. The detail (candidates
                        # evaluated, duplicates removed) only shows on a fresh search.
                        if total_raw and not st.session_state.get("from_cache"):
                            dupes = max(total_raw - total_dedup, 0)
                            extra = f", {dupes} duplicate(s) removed" if dupes else ""
                            st.caption(f"{n} article(s) found, {total_raw} candidate(s) evaluated{extra}  ({origin})")
                        else:
                            st.caption(f"{n} article(s) found  ({origin})")
                        hidden_count = st.session_state.get("hidden_count", 0)
                        if hidden_count:
                            st.caption(f"{hidden_count} article(s) hidden in this search")
                    with export_col:
                        st.download_button(
                            label="Export CSV",
                            data=_to_csv_bytes(sliced),
                            file_name="results.csv",
                            mime="text/csv",
                            use_container_width=True,
                        )

                    # ── selection bar ─────────────────────────────────────────────────────
                    selected = st.session_state.selected
                    sel_info_col, sel_btn_col = st.columns([4, 1])
                    with sel_info_col:
                        st.caption(f"{len(selected)} article(s) selected")
                    with sel_btn_col:
                        if selected:
                            if st.button("Clear selection", key="btn_clear_selection", use_container_width=True):
                                _clear_selection()
                                st.rerun()

                    # ── article cards (grouped by topic when f_cluster) ─────────────
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
                                    title = a.title or "(untitled)"
                                    link = _article_link(a)
                                    prefix = ":red[**[RETRACTED]**] " if a.retracted else ""
                                    if link:
                                        st.markdown(f"{prefix}**{i}. [{title}]({link})**")
                                    else:
                                        st.markdown(f"{prefix}**{i}. {title}**")
                                with sel_col:
                                    is_checked = st.checkbox("Select", key=cb_key)
                                with hide_col:
                                    if st.button("Hide", key=f"hide_{article_key}"):
                                        hide_article(hide_key, a.title or "")
                                        st.session_state.results = [
                                            r for r in st.session_state.results
                                            if _hide_key(r) != hide_key
                                        ]
                                        st.session_state.selected.pop(article_key, None)
                                        st.session_state.pop(cb_key, None)
                                        st.rerun()

                                # Sync checkbox state -> selected dict
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
                                    st.caption(f"Cited by: {a.citations}  |  [{a.source}]")

                                with st.expander("Details", expanded=False):
                                    if a.venue_type:
                                        st.write(f"Publication type: {a.venue_type}")
                                    if a.publisher:
                                        st.write(f"Publisher: {a.publisher}")
                                    if a.is_oa is not None:
                                        st.write(f"Open access: {'Yes' if a.is_oa else 'No'}")
                                    if a.oa_url:
                                        st.link_button("Open access version", a.oa_url)
                                    if a.concepts:
                                        st.write(f"Topics: {' | '.join(a.concepts)}")
                                    if a.abstract:
                                        st.caption(a.abstract)
                                    else:
                                        st.caption("Abstract not available")

                    if st.session_state.selected:
                        st.divider()
                        st.info(
                            f"{len(st.session_state.selected)} article(s) selected. "
                            "Go to the **Export** tab to download as TSV, xlsx or BibTeX."
                        )

    # ── Export tab ──────────────────────────────────────────────────────────
    with tab_export:
        selected_list = list(st.session_state.selected.values())

        if not selected_list:
            st.warning("No articles selected. Go back to the Search tab and tick some articles.")
        else:
            n = len(selected_list)
            st.write(f"{n} article(s) selected for export")

            hide_after = st.checkbox(
                "Hide selected articles after exporting",
                value=True,
                key="hide_after_export",
            )

            fmt = st.radio(
                "Export format",
                [
                    "TSV for Excel (paste straight into a sheet)",
                    ".xlsx file (direct download)",
                    "BibTeX for Overleaf (.bib)",
                ],
            )

            st.divider()

            def _do_hide_selected() -> None:
                for article in selected_list:
                    hide_article(_hide_key(article), article.title or "")
                st.session_state.selected = {}
                st.info("Exported articles have been marked as hidden.")

            if fmt == "TSV for Excel (paste straight into a sheet)":
                st.write("Copy the text below and paste it into the first empty row of your sheet")
                st.text_area(
                    label="TSV to paste into a spreadsheet",
                    value=_to_ref_tsv(selected_list),
                    height=200,
                    key="tsv_output",
                    label_visibility="collapsed",
                )
                st.caption(
                    "The Content column holds the original abstract. Review and adapt it "
                    "before pasting into your sheet."
                )
                if hide_after:
                    if st.button("Confirm export and hide articles", key="btn_hide_tsv"):
                        _do_hide_selected()
                        st.rerun()

            elif fmt == ".xlsx file (direct download)":
                xlsx_clicked = st.download_button(
                    label="Download .xlsx",
                    data=_to_ref_xlsx(selected_list),
                    file_name="academic_references.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
                if xlsx_clicked and hide_after:
                    _do_hide_selected()
                    st.rerun()

            else:  # BibTeX
                bib = _to_export_bibtex(selected_list)
                st.write("Copy and paste into your .bib file in Overleaf or Zotero")
                st.code(bib, language=None)
                bib_clicked = st.download_button(
                    label="Download references.bib",
                    data=bib.encode("utf-8"),
                    file_name="references.bib",
                    mime="text/plain",
                )
                if bib_clicked and hide_after:
                    _do_hide_selected()
                    st.rerun()

    # ── Hidden tab ───────────────────────────────────────────────────────────
    with tab_hidden:
        hidden_entries = get_hidden_articles()
        total_hidden = len(hidden_entries)

        if not hidden_entries:
            st.info("No hidden articles.")
        else:
            top_col, sweep_col, btn_col = st.columns([3, 1, 1])
            with top_col:
                st.write(f"{total_hidden} hidden article(s)")
                if total_hidden >= 100:
                    st.warning(f"{total_hidden} hidden articles. Use 'Clear old' to remove those older than 90 days.")
            with sweep_col:
                if st.button("Clear old (90d)", key="btn_sweep_hidden", use_container_width=True):
                    removed = sweep_hidden(days=90)
                    st.toast(f"{removed} article(s) removed.")
                    st.rerun()
            with btn_col:
                if st.button("Clear all", key="btn_clear_hidden", use_container_width=True):
                    clear_hidden()
                    st.rerun()

            for entry in hidden_entries:
                title_display = (entry["title"] or entry["doi"])[:80]
                hidden_at = entry.get("hidden_at")
                row_col, restore_col = st.columns([5, 1])
                with row_col:
                    st.write(title_display)
                    if hidden_at:
                        st.caption(f"Hidden on {hidden_at.strftime('%Y-%m-%d')}")
                with restore_col:
                    if st.button("Restore", key=f"restore_{entry['doi']}"):
                        unhide_article(entry["doi"])
                        st.rerun()


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    """Used by the `academic-web` script to start the Streamlit server."""
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", __file__],
        check=True,
    )


# Streamlit re-executes this file on every user interaction.
# The guard ensures _app() only runs inside an active Streamlit session,
# avoiding errors when the module is imported by the `academic-web` script.
# IMPORTANT: only ImportError is caught (expected outside a Streamlit context).
# Errors raised inside _app() must propagate rather than be silenced.
try:
    from streamlit.runtime.scriptrunner import get_script_run_ctx as _get_ctx
except ImportError:
    _get_ctx = None

if _get_ctx is not None and _get_ctx() is not None:
    _app()
