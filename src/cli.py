import asyncio
import csv
import re
from enum import Enum
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

import src.cache as cache
from src.models import Article
from src.providers.elsevier import search as elsevier_search
from src.providers.ieee import search as ieee_search
from src.providers.openalex import search as openalex_search
from src.ranking import score_articles
from src.utils import dedup_articles
from src import retractions

app = typer.Typer()
console = Console()


class Format(str, Enum):
    csv = "csv"
    bibtex = "bibtex"


@app.command()
def main(
    query: str = typer.Argument(..., help="Termo de busca"),
    limit: int = typer.Option(10, "--limit", "-n", help="Numero de resultados"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Arquivo de saida"),
    fmt: Format = typer.Option(Format.csv, "--format", "-f", help="Formato de saida: csv, bibtex"),
    year_from: Optional[int] = typer.Option(None, "--year-from", help="Ano minimo de publicacao"),
    year_to: Optional[int] = typer.Option(None, "--year-to", help="Ano maximo de publicacao"),
    source: Optional[list[str]] = typer.Option(None, "--source", "-s", help="Filtrar por fonte: openalex, elsevier, ieee (repetivel)"),
    oa_only: bool = typer.Option(False, "--oa-only", help="Somente artigos Open Access"),
):
    """Search academic papers across multiple sources."""
    console.print(f"\nBuscando: [bold cyan]{query}[/bold cyan]\n")

    active_sources = set(source) if source else None

    cached = cache.get(query, min_size=limit)
    if cached is not None:
        console.print("[dim]cache hit[/dim]")
        ranked = cached
    else:
        articles = asyncio.run(_search_all(query, limit, sources=active_sources))
        ranked = score_articles(articles, query)
        cache.store(query, ranked)

    n_retracted = retractions.mark_retracted(ranked)
    if n_retracted:
        console.print(f"[red]Atencao: {n_retracted} artigo(s) retratado(s) marcado(s).[/red]")

    # Source filter applied post-fetch as well, so cached entries from a
    # different source set are filtered correctly on subsequent calls.
    results = _apply_filters(ranked, year_from=year_from, year_to=year_to, sources=source, oa_only=oa_only)
    results = results[:limit]

    if output:
        if fmt == Format.bibtex:
            _write_bibtex(results, output)
        else:
            _write_csv(results, output)
        console.print(f"[green]Salvo em {output}[/green]")
    else:
        _display_results(results)


def _apply_filters(
    articles: list[Article],
    year_from: Optional[int],
    year_to: Optional[int],
    sources: Optional[list[str]],
    oa_only: bool,
) -> list[Article]:
    out = []
    for a in articles:
        if year_from is not None and (a.year is None or a.year < year_from):
            continue
        if year_to is not None and (a.year is None or a.year > year_to):
            continue
        if sources and a.source not in sources:
            continue
        if oa_only and not a.is_oa:
            continue
        out.append(a)
    return out


_PROVIDER_NAMES = ("openalex", "elsevier", "ieee")


async def _search_all(
    query: str,
    limit: int,
    sources: Optional[set[str]] = None,
) -> list[Article]:
    """Call enabled providers concurrently. If `sources` is given, only those
    providers are queried (pre-fetch filter — saves API quota)."""
    fns = [openalex_search, elsevier_search, ieee_search]
    tasks, names = [], []
    for name, fn in zip(_PROVIDER_NAMES, fns):
        if sources is None or name in sources:
            tasks.append(fn(query, limit))
            names.append(name)

    raw = await asyncio.gather(*tasks, return_exceptions=True)
    articles: list[Article] = []
    for name, result in zip(names, raw):
        if isinstance(result, BaseException):
            console.print(f"[yellow]Aviso: provedor {name} falhou ({result}). Continuando sem ele.[/yellow]")
            continue
        articles.extend(result)
    return dedup_articles(articles)


def _display_results(articles: list[Article]):
    if not articles:
        console.print("[red]Nenhum resultado encontrado.[/red]")
        return

    table = Table(show_header=True, header_style="bold magenta", show_lines=True)
    table.add_column("#", width=3)
    table.add_column("Titulo", min_width=40, max_width=60)
    table.add_column("Ano", width=6)
    table.add_column("Fonte", width=10)
    table.add_column("Citacoes", width=9)
    table.add_column("Score", width=7)
    table.add_column("DOI", min_width=20, max_width=35)

    for i, a in enumerate(articles, 1):
        title = a.title[:80] if a.title else "-"
        if a.retracted:
            title = f"[red][RETRATADO][/red] {title}"
        table.add_row(
            str(i),
            title,
            str(a.year) if a.year else "-",
            a.source,
            str(a.citations),
            f"{a.score:.3f}",
            a.doi or "-",
        )

    console.print(table)
    console.print(f"\n[green]{len(articles)} artigos encontrados.[/green]\n")


def _write_csv(articles: list[Article], path: Path):
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
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


def _write_bibtex(articles: list[Article], path: Path):
    with path.open("w", encoding="utf-8") as f:
        for a in articles:
            key = _bibtex_key(a)
            authors = " and ".join(a.authors) if a.authors else "Unknown"
            f.write(f"@article{{{key},\n")
            f.write(f"  title   = {{{a.title}}},\n")
            f.write(f"  author  = {{{authors}}},\n")
            if a.year:
                f.write(f"  year    = {{{a.year}}},\n")
            if a.doi:
                f.write(f"  doi     = {{{a.doi}}},\n")
            if a.venue:
                f.write(f"  journal = {{{a.venue}}},\n")
            f.write("}\n\n")


def _bibtex_key(article: Article) -> str:
    first_author = article.authors[0].split()[-1] if article.authors else "unknown"
    first_word = re.sub(r"[^a-zA-Z]", "", (article.title or "").split()[0]) if article.title else "untitled"
    year = article.year or "0000"
    return f"{first_author.lower()}{year}{first_word.lower()}"


@app.command("clear-cache")
def clear_cache():
    """Remove all entries from the local search cache."""
    removed = cache.clear()
    console.print(f"[green]Cache limpo: {removed} entrada(s) removida(s).[/green]")


@app.command("refresh-retractions")
def refresh_retractions():
    """Download or refresh the Retraction Watch dataset (~64 MB)."""
    console.print("Baixando lista de retratacoes (pode demorar ate 1 min)...")
    try:
        size = retractions.download()
        n = len(retractions.get_retracted_dois())
        console.print(f"[green]OK. {n:,} DOIs ({size // 1024 // 1024} MB).[/green]")
    except Exception as exc:
        console.print(f"[red]Falha: {exc}[/red]")


if __name__ == "__main__":
    app()
