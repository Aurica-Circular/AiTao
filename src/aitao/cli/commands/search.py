# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Search commands for AiTao CLI.

Commands:
- aitao search "query"                    Hybrid search (default)
- aitao search "query" --mode semantic    Semantic-only search
- aitao search "query" --mode fulltext    Full-text only search
- aitao search "query" --category X       Filter by category
- aitao search "query" --language X       Filter by language
- aitao search "query" --path X           Filter by path substring
"""

from typing import Optional

import typer
from rich.panel import Panel

from aitao.cli.utils import console, success, error, warning, info, print_header, create_table

app = typer.Typer(
    help=(
        "Hybrid search across your documents (text + semantic).\n\n"
        "[bold cyan]Examples[/bold cyan]\n\n"
        "  Search for a term:\n"
        '    [green]./aitao.sh search "rental contract"[/green]\n\n'
        "  Semantic search only:\n"
        '    [green]./aitao.sh search "supplier invoice" --mode semantic[/green]\n\n'
        "  Keyword search only:\n"
        '    [green]./aitao.sh search "Taiwan" --mode fulltext[/green]\n\n'
        "[dim]Hybrid search combines both modes for better results.[/dim]"
    ),
    rich_markup_mode="rich",
    # Allow options to follow the positional query, e.g.
    # `search "my query" --mode semantic` (not just options-first).
    context_settings={"allow_interspersed_args": True},
)


def _format_score(score: float) -> str:
    """Format score with color based on value."""
    if score >= 0.8:
        return f"[green]{score:.2f}[/green]"
    elif score >= 0.5:
        return f"[yellow]{score:.2f}[/yellow]"
    else:
        return f"[dim]{score:.2f}[/dim]"


def _truncate(text: str, max_len: int = 80) -> str:
    """Truncate text with ellipsis."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


@app.command("run")
def search_run(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(10, "--limit", "-l", help="Maximum results"),
    mode: str = typer.Option("hybrid", "--mode", "-m", help="Search mode: hybrid, semantic, fulltext"),
    category: Optional[str] = typer.Option(None, "--category", "-c", help="Filter by category"),
    language: Optional[str] = typer.Option(None, "--language", "-L", help="Filter by language (en, fr, zh, etc.)"),
    path_contains: Optional[str] = typer.Option(None, "--path", "-p", help="Filter by path substring"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output with scores"),
):
    """
    Perform hybrid search across indexed documents.

    Examples:
        aitao search run "facture voyage"
        aitao search run "invoice" --mode semantic --limit 5
        aitao search run "2025" --category finance --language fr
    """
    print_header(f"Search: '{query}'")

    try:
        from aitao.search.hybrid_engine import HybridSearchEngine, SearchFilter

        # Build filters
        filters = SearchFilter(
            path_contains=path_contains,
            category=category,
            language=language,
        )

        # Execute search
        engine = HybridSearchEngine()
        response = engine.search_sync(
            query=query,
            limit=limit,
            filters=filters,
            mode=mode,
        )

        # Show timing info
        info(
            f"Found {response.total} results in {response.search_time_ms:.0f}ms "
            f"(Meilisearch: {response.meilisearch_count})"
        )
        console.print()

        if not response.results:
            warning("No results found")
            return

        # Create results table
        if verbose:
            table = create_table("Search Results")
            table.add_column("#", style="dim", width=3)
            table.add_column("Score", width=10)
            table.add_column("Title", style="cyan", max_width=40)
            table.add_column("Path", max_width=50)
            table.add_column("Lang", width=4)
            table.add_column("Category", width=12)

            for i, r in enumerate(response.results, 1):
                score_str = _format_score(r.score)
                if r.semantic_score > 0 or r.fulltext_score > 0:
                    score_str += f"\n[dim]S:{r.semantic_score:.2f} F:{r.fulltext_score:.2f}[/dim]"

                table.add_row(
                    str(i),
                    score_str,
                    _truncate(r.title, 40),
                    _truncate(r.path, 50),
                    r.language or "-",
                    r.category or "-",
                )
        else:
            table = create_table("Search Results")
            table.add_column("#", style="dim", width=3)
            table.add_column("Score", width=6)
            table.add_column("Title", style="cyan", max_width=50)
            table.add_column("Path", max_width=60)

            for i, r in enumerate(response.results, 1):
                table.add_row(
                    str(i),
                    _format_score(r.score),
                    _truncate(r.title, 50),
                    _truncate(r.path, 60),
                )

        console.print(table)

        # Show first result content preview
        if verbose and response.results:
            first = response.results[0]
            if first.content:
                console.print()
                console.print(
                    Panel(
                        _truncate(first.content, 500),
                        title=f"[cyan]{first.title}[/cyan]",
                        subtitle="Top Result Preview",
                        border_style="dim",
                    )
                )

    except Exception as e:
        error(f"Search failed: {e}")
        raise typer.Exit(1)


@app.command("test")
def search_test():
    """Run a quick search test with sample queries."""
    print_header("Search Test")

    test_queries = [
        "test document",
        "facture",
        "important",
    ]

    try:
        from aitao.search.hybrid_engine import HybridSearchEngine

        engine = HybridSearchEngine()

        table = create_table("Test Results")
        table.add_column("Query", style="cyan")
        table.add_column("Mode")
        table.add_column("Results")
        table.add_column("Time (ms)")
        table.add_column("Status")

        for query in test_queries:
            for mode in ["hybrid", "semantic", "fulltext"]:
                try:
                    response = engine.search_sync(query=query, limit=5, mode=mode)
                    status = "[green]✓[/green]"
                    results = str(response.total)
                    time_ms = f"{response.search_time_ms:.0f}"
                except Exception:
                    status = "[red]✗[/red]"
                    results = "-"
                    time_ms = "-"

                table.add_row(query, mode, results, time_ms, status)

        console.print(table)
        success("Search test completed")

    except Exception as e:
        error(f"Test failed: {e}")
        raise typer.Exit(1)


@app.command("modes")
def search_modes():
    """Explain available search modes."""
    print_header("Search Modes")

    console.print(
        Panel(
            "[bold cyan]hybrid[/bold cyan] (default)\n"
            "  ONE native Meilisearch hybrid call, combining semantic and\n"
            "  full-text in a single round-trip (configurable semanticRatio,\n"
            "  default 0.5 — see [search] semantic_ratio_documents).\n"
            "  Best for: General document search.\n\n"
            "[bold cyan]semantic[/bold cyan]\n"
            "  Uses Meilisearch's userProvided vector embeddings (bge-m3).\n"
            "  Finds conceptually similar content.\n"
            "  Best for: Finding related documents, concepts.\n\n"
            "[bold cyan]fulltext[/bold cyan]\n"
            "  Uses Meilisearch keyword matching.\n"
            "  Exact word matches with typo tolerance.\n"
            "  Best for: Specific terms, names, codes.",
            title="Available Modes",
            border_style="blue",
        )
    )


# Main search command (shortcut).
#
# The group carries an optional [QUERY] positional so `aitao search "text"`
# works without a subcommand. That positional shadows the `run`/`test`/`modes`
# subcommands (Click consumes the token as QUERY before resolving a command),
# so the shortcut below must expose the same options as `run` — otherwise
# semantic/filtered searches would be unreachable from the CLI.
@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    query: Optional[str] = typer.Argument(None, help="Quick search query"),
    mode: str = typer.Option(
        "hybrid",
        "--mode",
        "-m",
        help="Search mode: hybrid, semantic, fulltext",
    ),
    limit: int = typer.Option(10, "--limit", "-l", help="Maximum results"),
    category: Optional[str] = typer.Option(None, "--category", "-c", help="Filter by category"),
    language: Optional[str] = typer.Option(None, "--language", "-L", help="Filter by language (en, fr, zh, ...)"),
    path_contains: Optional[str] = typer.Option(None, "--path", "-p", help="Filter by path substring"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed output with scores"),
):
    """
    Search indexed documents.

    Quick usage:
        aitao search "your query"
        aitao search "your query" --mode semantic --path "Règlement intérieur"
    """
    if ctx.invoked_subcommand is None:
        # A bare trailing "help" must show help, never run a search: the
        # group's quick-search positional consumes the token BEFORE Click's
        # subcommand resolution, so the registered `help` subcommand is
        # unreachable through this path (caught by the CI help-smoke test:
        # a "help" search built a real engine — model download + prod-index
        # client — during `--help` rendering).
        if query and query.strip().lower() in ("help", "-h", "--help"):
            console.print(ctx.get_help())
            return
        if query:
            # Direct search — forward the shortcut options to the runner.
            search_run(
                query=query,
                limit=limit,
                mode=mode,
                category=category,
                language=language,
                path_contains=path_contains,
                verbose=verbose,
            )
        else:
            # Show help
            console.print(ctx.get_help())


@app.command("help")
def help_cmd(ctx: typer.Context) -> None:
    """Show this help message and exit."""
    typer.echo(ctx.parent.get_help())
