import asyncio

import typer
from rich.console import Console
from rich.table import Table

import src.cache as cache
from src.providers.elsevier import search as elsevier_search
from src.providers.ieee import search as ieee_search
from src.providers.openalex import search as openalex_search
from src.ranking import score_articles

app = typer.Typer()
console = Console()


@app.command()
def main(
    query: str = typer.Argument(..., help="Termo de busca"),
    limit: int = typer.Option(10, "--limit", "-n", help="Número de resultados"),
):
    """Search academic papers across multiple sources."""
    console.print(f"\nBuscando: [bold cyan]{query}[/bold cyan]\n")

    cached = cache.get(query)
    if cached is not None:
        console.print("[dim]cache hit[/dim]")
        ranked = cached
    else:
        articles = asyncio.run(_search_all(query, limit))
        ranked = score_articles(articles, query)
        cache.set(query, ranked)

    _display_results(ranked[:limit])


async def _search_all(query: str, limit: int):
    openalex, elsevier, ieee = await asyncio.gather(
        openalex_search(query, limit),
        elsevier_search(query, limit),
        ieee_search(query, limit),
    )
    return openalex + elsevier + ieee


def _display_results(articles):
    if not articles:
        console.print("[red]Nenhum resultado encontrado.[/red]")
        return

    table = Table(show_header=True, header_style="bold magenta", show_lines=True)
    table.add_column("#", width=3)
    table.add_column("Título", min_width=40, max_width=60)
    table.add_column("Ano", width=6)
    table.add_column("Fonte", width=10)
    table.add_column("Citações", width=9)
    table.add_column("Score", width=7)
    table.add_column("DOI", min_width=20, max_width=35)

    for i, a in enumerate(articles, 1):
        table.add_row(
            str(i),
            a.title[:80] if a.title else "—",
            str(a.year) if a.year else "—",
            a.source,
            str(a.citations),
            f"{a.score:.3f}",
            a.doi or "—",
        )

    console.print(table)
    console.print(f"\n[green]{len(articles)} artigos encontrados.[/green]\n")


if __name__ == "__main__":
    app()
