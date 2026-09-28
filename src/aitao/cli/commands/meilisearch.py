# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Meilisearch management commands.

Commands:
- aitao ms status   Show Meilisearch status
- aitao ms start    Start Meilisearch server
- aitao ms stop     Stop Meilisearch server
- aitao ms upgrade  Upgrade Meilisearch (brew)
- aitao ms rebuild  Rebuild the search index
"""

import subprocess
import shutil

import typer

from aitao.cli.utils import (
    console, success, error, warning, info,
    print_header, status_line, confirm, create_progress
)
from aitao.core.registry import StatsKeys


app = typer.Typer(
    help=(
        "Meilisearch management (full-text search engine).\n\n"
        "[bold cyan]Examples[/bold cyan]\n\n"
        "  Check Meilisearch is running:\n"
        "    [green]./aitao.sh ms status[/green]\n\n"
        "  Show index statistics:\n"
        "    [green]./aitao.sh ms stats[/green]\n\n"
        "  Show installed version:\n"
        "    [green]./aitao.sh ms version[/green]\n\n"
        "  Upgrade Meilisearch:\n"
        "    [green]./aitao.sh ms upgrade[/green]"
    ),
    rich_markup_mode="rich",
)


@app.command("status")
def ms_status():
    """Show Meilisearch server status."""
    print_header("Meilisearch Status")
    
    try:
        from aitao.storage.repository import make_meilisearch_client
        client = make_meilisearch_client()
        
        if client.is_healthy():
            status_line("Server", "Running")
            status_line("Version", client.get_version())
            status_line("URL", client.host)
            
            stats = client.get_stats()
            status_line("Index", client.index_name)
            status_line("Documents", str(stats.get(StatsKeys.TOTAL_DOCUMENTS, 0)))
            
            if stats.get(StatsKeys.IS_INDEXING):
                warning("Server is currently indexing...")
        else:
            status_line("Server", "Not responding", ok=False)
            _show_start_help()
            
    except Exception as e:
        status_line("Server", f"Error: {e}", ok=False)
        _show_start_help()


def _show_start_help():
    """Show help for starting Meilisearch."""
    console.print()
    info("To start Meilisearch:")
    console.print("  [dim]brew services start meilisearch[/dim]")
    console.print("  [dim]or: meilisearch --http-addr localhost:7700[/dim]")


@app.command("start")
def ms_start():
    """Start Meilisearch server via brew services."""
    info("Starting Meilisearch...")
    
    if not shutil.which("brew"):
        error("Homebrew not found. Please start Meilisearch manually.")
        raise typer.Exit(1)
    
    result = subprocess.run(
        ["brew", "services", "start", "meilisearch"],
        capture_output=True, text=True
    )
    
    if result.returncode == 0:
        success("Meilisearch started")
        console.print(f"  {result.stdout.strip()}")
    else:
        error(f"Failed to start: {result.stderr}")
        raise typer.Exit(1)


@app.command("stop")
def ms_stop():
    """Stop Meilisearch server via brew services."""
    info("Stopping Meilisearch...")
    
    if not shutil.which("brew"):
        error("Homebrew not found. Please stop Meilisearch manually.")
        raise typer.Exit(1)
    
    result = subprocess.run(
        ["brew", "services", "stop", "meilisearch"],
        capture_output=True, text=True
    )
    
    if result.returncode == 0:
        success("Meilisearch stopped")
    else:
        error(f"Failed to stop: {result.stderr}")
        raise typer.Exit(1)


@app.command("restart")
def ms_restart():
    """Restart Meilisearch server."""
    info("Restarting Meilisearch...")
    
    if not shutil.which("brew"):
        error("Homebrew not found.")
        raise typer.Exit(1)
    
    result = subprocess.run(
        ["brew", "services", "restart", "meilisearch"],
        capture_output=True, text=True
    )
    
    if result.returncode == 0:
        success("Meilisearch restarted")
    else:
        error(f"Failed to restart: {result.stderr}")
        raise typer.Exit(1)


@app.command("upgrade")
def ms_upgrade(
    skip_confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """
    Upgrade Meilisearch to latest version.
    
    This will:
    1. Stop the service
    2. Export current data (optional)
    3. Upgrade via brew
    4. Start the service
    5. Verify health
    """
    print_header("Meilisearch Upgrade")
    
    if not shutil.which("brew"):
        error("Homebrew not found. Please upgrade manually.")
        raise typer.Exit(1)
    
    # Show current version
    try:
        from aitao.storage.repository import make_meilisearch_client
        client = make_meilisearch_client()
        if client.is_healthy():
            current_version = client.get_version()
            info(f"Current version: {current_version}")
        else:
            warning("Meilisearch not running")
            current_version = "unknown"
    except Exception:
        current_version = "unknown"
    
    # Check for updates
    info("Checking for updates...")
    result = subprocess.run(
        ["brew", "outdated", "meilisearch"],
        capture_output=True, text=True
    )
    
    if "meilisearch" not in result.stdout:
        success("Meilisearch is already up to date!")
        raise typer.Exit(0)
    
    console.print(f"  Available: {result.stdout.strip()}")
    
    if not skip_confirm:
        console.print()
        warning("⚠️  Upgrading may require reindexing if there's a major version change.")
        if not confirm("Proceed with upgrade?"):
            info("Upgrade cancelled")
            raise typer.Exit(0)
    
    # Perform upgrade
    with create_progress() as progress:
        task = progress.add_task("Upgrading Meilisearch...", total=4)
        
        # Step 1: Stop service
        progress.update(task, description="Stopping service...")
        subprocess.run(["brew", "services", "stop", "meilisearch"], 
                      capture_output=True)
        progress.update(task, advance=1)
        
        # Step 2: Upgrade
        progress.update(task, description="Upgrading via brew...")
        result = subprocess.run(
            ["brew", "upgrade", "meilisearch"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            error(f"Upgrade failed: {result.stderr}")
            raise typer.Exit(1)
        progress.update(task, advance=1)
        
        # Step 3: Start service
        progress.update(task, description="Starting service...")
        subprocess.run(["brew", "services", "start", "meilisearch"],
                      capture_output=True)
        progress.update(task, advance=1)
        
        # Step 4: Wait and verify
        progress.update(task, description="Verifying health...")
        import time
        time.sleep(2)  # Give it time to start
        progress.update(task, advance=1)
    
    # Final check
    console.print()
    try:
        client = make_meilisearch_client()
        if client.is_healthy():
            new_version = client.get_version()
            success(f"Upgrade complete! Version: {new_version}")
        else:
            warning("Server started but not responding yet. Check: brew services list")
    except Exception as e:
        warning(f"Could not verify: {e}")


@app.command("rebuild")
def ms_rebuild(
    skip_confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Rebuild the search index from scratch."""
    print_header("Rebuild Meilisearch Index")
    
    try:
        from aitao.storage.repository import make_meilisearch_client
        client = make_meilisearch_client()
        
        if not client.is_healthy():
            error("Meilisearch is not running")
            raise typer.Exit(1)
        
        stats = client.get_stats()
        doc_count = stats.get(StatsKeys.TOTAL_DOCUMENTS, 0)
        
        if doc_count > 0:
            warning(f"This will delete {doc_count} documents from the index.")
            if not skip_confirm and not confirm("Proceed?"):
                info("Cancelled")
                raise typer.Exit(0)
        
        # Clear the index
        info("Clearing index...")
        client.clear_index()
        success("Index cleared. Reindex with: aitao index <path>")
        
    except Exception as e:
        error(f"Failed: {e}")
        raise typer.Exit(1)


@app.command("prune")
def ms_prune(
    skip_confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Delete every Meilisearch index except the canonical one (US-19).

    Removes legacy ghosts (documents, indexao_*) and leftover test_* indexes.
    Idempotent: a second run finds nothing to delete. The canonical index
    (config [meilisearch] index_name, default aitao_documents) is never touched.
    """
    from rich.table import Table

    print_header("Prune Meilisearch Indexes")

    try:
        from aitao.search.meilisearch_client import MeilisearchClient

        client = MeilisearchClient()
        if not client.is_healthy():
            error("Meilisearch is not running")
            raise typer.Exit(1)

        canonical = client.index_name
        raw = client.client.get_indexes()
        indexes = raw.get("results", raw) if isinstance(raw, dict) else raw

        # Step 1 — delete every non-canonical index (legacy ghosts, test_*)
        candidates = [idx for idx in indexes if idx.uid != canonical]
        if candidates:
            table = Table(title="Indexes to delete")
            table.add_column("Index", style="yellow")
            table.add_column("Documents", justify="right")
            for idx in candidates:
                try:
                    count = client.client.index(idx.uid).get_stats().number_of_documents
                except Exception:
                    count = "?"
                table.add_row(idx.uid, str(count))
            console.print(table)
            info(f"Canonical index kept: [green]{canonical}[/green]")
            if skip_confirm or confirm(f"Delete {len(candidates)} index(es)?"):
                deleted = 0
                for idx in candidates:
                    try:
                        client.client.delete_index(idx.uid)
                        deleted += 1
                    except Exception as e:
                        warning(f"Could not delete '{idx.uid}': {e}")
                success(f"Pruned {deleted}/{len(candidates)} index(es). Kept '{canonical}'.")
            else:
                info("Index deletion cancelled")
        else:
            info(f"No ghost/test index — only the canonical '{canonical}' exists.")

        # Step 2 — remove build-artifact documents already in the canonical
        # index (the exclusion rule only prevents FUTURE indexing — US-19).
        artifact_ids = _find_artifact_doc_ids(client)
        if artifact_ids:
            info(f"Found {len(artifact_ids)} build-artifact document(s) in '{canonical}'.")
            if skip_confirm or confirm("Delete them from the index too?"):
                purged = sum(1 for did in artifact_ids if client.delete(did))
                success(f"Removed {purged} build-artifact document(s).")
        elif not candidates:
            success("Index is clean — nothing to prune.")

    except typer.Exit:
        raise
    except Exception as e:
        error(f"Failed: {e}")
        raise typer.Exit(1)


# Path markers of build/packaging artifacts that must never stay indexed (US-19)
_ARTIFACT_MARKERS = (".egg-info", ".dist-info", "__pycache__",
                     ".mypy_cache", ".pytest_cache", ".ruff_cache", "/node_modules/")


def _find_artifact_doc_ids(client) -> list:
    """Return ids of canonical-index docs whose path is a build artifact."""
    ids: list = []
    offset, batch = 0, 1000
    while True:
        try:
            page = client.index.get_documents({
                "offset": offset, "limit": batch,
                "fields": ["id", "path"],
            })
        except Exception:
            break
        results = getattr(page, "results", None) or []
        if not results:
            break
        for doc in results:
            data = doc if isinstance(doc, dict) else getattr(doc, "__dict__", {})
            path = str(data.get("path", ""))
            if any(marker in path for marker in _ARTIFACT_MARKERS):
                ids.append(data.get("id"))
        if len(results) < batch:
            break
        offset += batch
    return [i for i in ids if i]


@app.command("help")
def help_cmd(ctx: typer.Context) -> None:
    """Show this help message and exit."""
    typer.echo(ctx.parent.get_help())
