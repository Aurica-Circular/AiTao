# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
CLI commands for document indexing.

Provides commands to index documents into Meilisearch (full-text + semantic
vector, fusion engine — ÉPIC-31, US-113).
"""

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn

from aitao.cli.utils import status_line, spinner
from aitao.core.registry import StatsKeys

console = Console()
app = typer.Typer(
    help=(
        "Document indexing pipeline.\n\n"
        "[bold cyan]Examples[/bold cyan]\n\n"
        "  Index a file manually:\n"
        "    [green]./aitao.sh index file /path/to/document.pdf[/green]\n\n"
        "  Show index status:\n"
        "    [green]./aitao.sh index status[/green]\n\n"
        "  Remove a document from the index:\n"
        "    [green]./aitao.sh index delete <DOC_ID>[/green]\n\n"
        "  Remove documents outside the configured include_paths:\n"
        "    [green]./aitao.sh index prune --dry-run[/green]\n\n"
        "[dim]Note: the worker processes files automatically.\n"
        "Use these commands for one-off operations.[/dim]"
    ),
    rich_markup_mode="rich",
)


@app.command("file")
def index_file(
    file_path: str = typer.Argument(..., help="Path to file to index"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-index even if exists"),
    json_output: bool = typer.Option(False, "--json", "-j", help="Output as JSON"),
):
    """
    Index a single file into Meilisearch (full-text + semantic vector).

    Examples:
        ./aitao.sh index file document.pdf
        ./aitao.sh index file document.pdf --force
        ./aitao.sh index file document.pdf --json
    """
    from aitao.indexation.indexer import DocumentIndexer
    
    path = Path(file_path)
    
    # Resolve relative paths
    if not path.is_absolute():
        import os
        orig_pwd = os.environ.get("AITAO_ORIG_PWD", os.getcwd())
        path = Path(orig_pwd) / path
    
    if not path.exists():
        console.print(f"[red]❌ File not found: {path}[/red]")
        raise typer.Exit(1)
    
    try:
        indexer = DocumentIndexer()
    except Exception as e:
        console.print(f"[red]❌ Failed to initialize indexer: {e}[/red]")
        raise typer.Exit(1)
    
    with spinner(f"Indexing {path.name}..."):
        result = indexer.index_file(path, force=force)
    
    if json_output:
        import json
        output = {
            "path": result.path,
            "doc_id": result.doc_id,
            "success": result.success,
            "meilisearch_indexed": result.meilisearch_indexed,
            "word_count": result.word_count,
            "language": result.language,
            "extraction_time_ms": round(result.extraction_time_ms, 2),
            "indexing_time_ms": round(result.indexing_time_ms, 2),
            "total_time_ms": round(result.total_time_ms, 2),
        }
        if result.error:
            output["error"] = result.error
        console.print_json(json.dumps(output))
        return
    
    # Display result
    console.print()
    
    if result.success:
        if result.error and "Already indexed" in result.error:
            console.print(Panel(f"[yellow]⏭ Skipped: {path.name}[/yellow]\n{result.error}", 
                               title="Already Indexed"))
        else:
            console.print(Panel(f"[green]✓ Indexed: {path.name}[/green]", title="Success"))
            
            table = Table(show_header=False, box=None, padding=(0, 2))
            table.add_column("Key", style="cyan")
            table.add_column("Value")
            
            table.add_row("Document ID", result.doc_id[:16] + "...")
            table.add_row("Meilisearch", "[green]✓[/green]" if result.meilisearch_indexed else "[red]✗[/red]")
            table.add_row("Word count", str(result.word_count))
            table.add_row("Language", result.language or "unknown")
            table.add_row("Extraction time", f"{result.extraction_time_ms:.1f}ms")
            table.add_row("Indexing time", f"{result.indexing_time_ms:.1f}ms")
            
            console.print(table)
    else:
        console.print(Panel(f"[red]✗ Failed: {path.name}[/red]\n{result.error}", title="Error"))
        raise typer.Exit(1)


@app.command("batch")
def index_batch(
    directory: str = typer.Argument(..., help="Directory to scan and index"),
    recursive: bool = typer.Option(True, "--recursive/--no-recursive", "-r/-R", 
                                    help="Scan subdirectories"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-index existing documents"),
    limit: int = typer.Option(0, "--limit", "-l", help="Maximum files to process (0=unlimited)"),
):
    """
    Index all supported files in a directory.
    
    Examples:
        ./aitao.sh index batch ~/Documents
        ./aitao.sh index batch ~/Documents --no-recursive
        ./aitao.sh index batch ~/Documents --force --limit 100
    """
    from aitao.indexation.indexer import DocumentIndexer
    from aitao.indexation.text_extractor import TextExtractor
    
    path = Path(directory)
    
    if not path.is_absolute():
        import os
        orig_pwd = os.environ.get("AITAO_ORIG_PWD", os.getcwd())
        path = Path(orig_pwd) / path
    
    if not path.exists() or not path.is_dir():
        console.print(f"[red]❌ Directory not found: {path}[/red]")
        raise typer.Exit(1)
    
    # Find files first
    extractor = TextExtractor()
    supported = extractor.get_supported_extensions()
    
    console.print(f"[bold]Scanning {path}...[/bold]")
    
    if recursive:
        files = [f for f in path.rglob("*") if f.is_file() and f.suffix.lower() in supported]
    else:
        files = [f for f in path.iterdir() if f.is_file() and f.suffix.lower() in supported]
    
    if limit > 0:
        files = files[:limit]
    
    if not files:
        console.print("[yellow]⚠ No supported files found[/yellow]")
        return
    
    console.print(f"Found [bold]{len(files)}[/bold] files to index\n")
    
    try:
        indexer = DocumentIndexer()
    except Exception as e:
        console.print(f"[red]❌ Failed to initialize indexer: {e}[/red]")
        raise typer.Exit(1)
    
    # Index with progress bar
    successful = 0
    failed = 0
    skipped = 0
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Indexing...", total=len(files))
        
        for file in files:
            result = indexer.index_file(file, force=force)
            
            if result.success:
                if result.error and "Already indexed" in result.error:
                    skipped += 1
                else:
                    successful += 1
            else:
                failed += 1
            
            progress.update(task, advance=1, description=f"Indexing {file.name[:30]}...")
    
    # Summary
    console.print()
    console.print(Panel(
        f"[green]✓ Indexed: {successful}[/green]\n"
        f"[yellow]⏭ Skipped: {skipped}[/yellow]\n"
        f"[red]✗ Failed: {failed}[/red]",
        title="Batch Indexing Complete"
    ))


@app.command("reindex")
def index_reindex(
    skip_confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Re-index every document already in the index (force rebuild).

    Rebuilds stored fields (title, metadata) for documents indexed before a
    fix — e.g. US-20's filename-based title. Targets exactly the paths known
    to the index; files gone from disk are skipped (handled by the trash).
    """
    from aitao.indexation.indexer import DocumentIndexer
    from aitao.storage.repository import make_meilisearch_client

    try:
        meili = make_meilisearch_client()
        paths = sorted(meili.get_all_document_paths())
    except Exception as e:
        console.print(f"[red]❌ Cannot list indexed documents: {e}[/red]")
        raise typer.Exit(1)

    if not paths:
        console.print("[yellow]⚠ No indexed documents to re-index[/yellow]")
        return

    console.print(f"Found [bold]{len(paths)}[/bold] indexed document(s) to rebuild.")
    if not skip_confirm:
        if not typer.confirm("Force re-index all of them?"):
            console.print("Cancelled")
            raise typer.Exit(0)

    try:
        indexer = DocumentIndexer()
    except Exception as e:
        console.print(f"[red]❌ Failed to initialize indexer: {e}[/red]")
        raise typer.Exit(1)

    rebuilt = missing = failed = 0
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Re-indexing...", total=len(paths))
        for p in paths:
            if not Path(p).exists():
                missing += 1
            else:
                result = indexer.index_file(p, force=True)
                if result.success:
                    rebuilt += 1
                else:
                    failed += 1
            progress.update(task, advance=1, description=f"Re-indexing {Path(p).name[:30]}...")

    console.print()
    console.print(Panel(
        f"[green]✓ Rebuilt: {rebuilt}[/green]\n"
        f"[yellow]⏭ Missing on disk (skipped): {missing}[/yellow]\n"
        f"[red]✗ Failed: {failed}[/red]",
        title="Re-index Complete",
    ))


@app.command("status")
def index_status():
    """
    Show indexing statistics from both databases.
    
    Example:
        ./aitao.sh index status
    """
    from aitao.indexation.indexer import DocumentIndexer
    
    console.print(Panel("[bold]Indexing Statistics[/bold]"))
    console.print()
    
    try:
        indexer = DocumentIndexer()
        stats = indexer.get_stats()
    except Exception as e:
        console.print(f"[red]❌ Failed to get stats: {e}[/red]")
        raise typer.Exit(1)
    
    # ÉPIC-31 (US-113): LanceDB is no longer a live store (fusion-only, v4.0)
    # — get_stats()["lancedb"] stays in the contract (Optional) but is always
    # None. See `./aitao.sh db status` for the active (Meilisearch) excerpt
    # index stats.

    # Meilisearch stats
    console.print("[bold cyan]Meilisearch (Full-text Search)[/bold cyan]")
    if stats.get("meilisearch"):
        ms = stats["meilisearch"]
        if "error" in ms:
            status_line("Status", f"Error: {ms['error']}", ok=False)
        else:
            # Use StatsKeys for consistent key access
            doc_count = ms.get(StatsKeys.TOTAL_DOCUMENTS, 0)
            status_line("Documents", str(doc_count))
            status_line("Index", ms.get(StatsKeys.INDEX_NAME, "unknown"))
            server = ms.get(StatsKeys.HOST, "unknown")
            status_line("Server", server)
    else:
        status_line("Status", "Not available", ok=False)


@app.command("delete")
def delete_document(
    file_path: str = typer.Argument(..., help="Path of document to delete from indexes"),
    confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """
    Delete a document from both search indexes.
    
    Example:
        ./aitao.sh index delete ~/Documents/old_file.pdf
        ./aitao.sh index delete ~/Documents/old_file.pdf --yes
    """
    from aitao.indexation.indexer import DocumentIndexer
    
    path = Path(file_path)
    
    if not path.is_absolute():
        import os
        orig_pwd = os.environ.get("AITAO_ORIG_PWD", os.getcwd())
        path = Path(orig_pwd) / path
    
    if not confirm:
        console.print("[yellow]This will delete the document from both search indexes:[/yellow]")
        console.print(f"  {path}")
        console.print()
        if not typer.confirm("Are you sure?"):
            console.print("[dim]Cancelled[/dim]")
            return
    
    try:
        indexer = DocumentIndexer()
        success, message = indexer.delete_document(path)
        
        if success:
            console.print(f"[green]✓ {message}[/green]")
        else:
            console.print(f"[red]✗ Delete failed: {message}[/red]")
            raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]❌ Error: {e}[/red]")
        raise typer.Exit(1)


@app.command("test")
def test_indexing():
    """
    Run a quick test of the indexing pipeline.
    
    Example:
        ./aitao.sh index test
    """
    from aitao.indexation.indexer import DocumentIndexer

    console.print(Panel("[bold]Testing Document Indexing Pipeline[/bold]"))
    console.print()
    
    # Test 1: Check components
    console.print("[bold]1. Component Check[/bold]")
    
    try:
        indexer = DocumentIndexer()
        status_line("DocumentIndexer", "OK")
    except Exception as e:
        status_line("DocumentIndexer", f"FAIL: {e}", ok=False)
        raise typer.Exit(1)
    
    # Check Meilisearch
    try:
        if indexer.meilisearch:
            if indexer.meilisearch.is_healthy():
                stats = indexer.meilisearch.get_stats()
                status_line("Meilisearch", f"OK ({stats.get('document_count', 0)} docs)")
            else:
                status_line("Meilisearch", "Not healthy", ok=False)
        else:
            status_line("Meilisearch", "Not available", ok=False)
    except Exception as e:
        status_line("Meilisearch", f"FAIL: {e}", ok=False)
    
    console.print()
    
    # NOTE (US-088): this command is a component health check only. It deliberately
    # does NOT index a sample file, because writing a temp doc into the LIVE stores
    # (even with cleanup) risks leaving an orphan chunk if cleanup is partial — the
    # exact bug US-088 fixes. End-to-end indexing is covered by the test suite.
    console.print()
    console.print("[bold green]Component check complete.[/bold green]")
    console.print(
        "[dim]End-to-end indexing is validated by the test suite "
        "(uv run pytest), not against the live stores.[/dim]"
    )


@app.command("prune")
def index_prune(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="List out-of-scope documents without deleting anything"
    ),
    skip_confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """
    Remove indexed documents that are no longer under any configured
    `[indexing].include_paths` root (US-126-A).

    Scope is decided ONLY from the configured include_paths list — never
    from disk presence. A root that is still configured but momentarily
    unmounted (e.g. an unplugged external volume) is NOT considered
    out-of-scope; its documents are left untouched.

    Examples:
        ./aitao.sh index prune --dry-run
        ./aitao.sh index prune
        ./aitao.sh index prune --yes
    """
    from aitao.cli.commands._index_prune import delete_orphans, render_orphans_report
    from aitao.core.pathmanager import path_manager
    from aitao.indexation.prune import find_out_of_scope_paths, group_by_root
    from aitao.storage.repository import make_meilisearch_client

    try:
        meili = make_meilisearch_client()
        indexed_paths = meili.get_all_document_paths()
    except Exception as e:
        console.print(f"[red]❌ Cannot list indexed documents: {e}[/red]")
        console.print("[yellow]Aborting — nothing was deleted.[/yellow]")
        raise typer.Exit(1)

    # SAFETY: existing_only=False on purpose — a demounted-but-still-configured
    # root must stay "in scope" (see prune.py module docstring). Never swap
    # this for existing_only=True.
    configured_roots = path_manager.get_include_paths(existing_only=False)

    if not configured_roots:
        console.print(
            "[yellow]⚠ [indexing].include_paths is empty — refusing to prune "
            "(an empty config is not treated as \"everything is orphaned\").[/yellow]"
        )
        raise typer.Exit(0)

    orphans = find_out_of_scope_paths(indexed_paths, configured_roots)

    if not orphans:
        console.print("[green]✓ No out-of-scope documents — index matches include_paths.[/green]")
        return

    groups = group_by_root(orphans, configured_roots)
    render_orphans_report(console, orphans, groups)
    console.print()

    if dry_run:
        console.print("[dim]Dry run — nothing was deleted.[/dim]")
        return

    if not skip_confirm:
        if not typer.confirm(f"Delete these {len(orphans)} document(s) from the index?"):
            console.print("[dim]Cancelled[/dim]")
            raise typer.Exit(0)

    deleted, failed = delete_orphans(console, meili, orphans)

    console.print()
    console.print(
        Panel(
            f"[green]✓ Deleted: {deleted}[/green]\n[red]✗ Failed: {failed}[/red]",
            title="Prune Complete",
        )
    )
    if failed:
        raise typer.Exit(1)


@app.command("help")
def help_cmd(ctx: typer.Context) -> None:
    """Show this help message and exit."""
    typer.echo(ctx.parent.get_help())
