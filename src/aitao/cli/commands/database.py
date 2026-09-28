# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Semantic-search store management commands.

Commands:
- aitao db status   Show the active excerpt/chunk index status
- aitao db stats     Legacy LanceDB inspection (removed — informational message)
- aitao db clear     Legacy LanceDB inspection (removed — informational message)
- aitao db search    Legacy LanceDB inspection (removed — informational message)

ÉPIC-31 (US-113): fusion is the only search engine since v4.0 (decision D1).
The semantic backend is the Meilisearch excerpt/chunk index — LanceDB is no
longer a live store; ``search.lancedb_client.LanceDBClient`` was removed
along with the "[search] engine" flag. ``~/.aitao/data/lancedb`` is kept
read-only for reference and can be deleted once you no longer need to roll
back to a 3.x installation — see docs/MIGRATION-4.0.md.
"""

import typer

from aitao.cli.utils import error, warning, info, print_header, status_line

_LEGACY_MESSAGE = (
    "LanceDB n'est plus le moteur actif depuis la v4.0 (fusion). Le dossier "
    "~/.aitao/data/lancedb est conservé en lecture seule pour référence et "
    "peut être supprimé si vous n'avez plus besoin de revenir à une version "
    "3.x (voir docs/MIGRATION-4.0.md). Utilisez `./aitao.sh db status`, "
    "`./aitao.sh dashboard` ou l'API /api/stats pour les statistiques actives."
)


app = typer.Typer(
    help=(
        "Semantic-search store status (excerpt/chunk index).\n\n"
        "[bold cyan]Examples[/bold cyan]\n\n"
        "  Show excerpt index status:\n"
        "    [green]./aitao.sh db status[/green]\n\n"
        "[dim]v4.0 ships fusion-only: the Meilisearch excerpt/chunk index carries\n"
        "the semantic vectors. LanceDB is a legacy, read-only store.[/dim]"
    ),
    rich_markup_mode="rich",
)


@app.command("status")
def db_status():
    """Show the active excerpt/chunk index status (Meilisearch, fusion engine)."""
    print_header("Excerpt index status")
    try:
        # RAW meilisearch.Client on purpose: MeiliChunkStore's constructor
        # loads the bge-m3 model — far too heavy for a status read.
        import meilisearch
        from aitao.core.config import get_config

        cfg = get_config()
        ms = cfg.search.meilisearch
        raw = meilisearch.Client(ms.url, ms.api_key or None)
        stat = raw.index(ms.chunks_index).get_stats()
        count = getattr(stat, "number_of_documents", None)
        if count is None and isinstance(stat, dict):
            count = stat.get("numberOfDocuments", 0)

        status_line("Index", ms.chunks_index)
        status_line("Host", ms.url)
        status_line("Documents (excerpts)", str(int(count or 0)))
        status_line("Embedding dimension", str(cfg.search.embedding.dimension))
    except Exception as e:
        error(f"Error: {e}")
        raise typer.Exit(1)


@app.command("stats")
def db_stats():
    """Legacy LanceDB statistics — removed, see the informational message."""
    print_header("LanceDB (legacy)")
    warning(_LEGACY_MESSAGE)


@app.command("clear")
def db_clear(
    skip_confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Legacy LanceDB clear — removed, see the informational message."""
    print_header("LanceDB (legacy)")
    warning(_LEGACY_MESSAGE)


@app.command("search")
def db_search(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(5, "--limit", "-n", help="Number of results"),
):
    """Legacy LanceDB search — removed, see the informational message."""
    print_header("LanceDB (legacy)")
    warning(_LEGACY_MESSAGE)
    info("Use `./aitao.sh search <query>` for the active (fusion) search.")


@app.command("help")
def help_cmd(ctx: typer.Context) -> None:
    """Show this help message and exit."""
    typer.echo(ctx.parent.get_help())
