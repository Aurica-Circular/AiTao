# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Scan commands - Filesystem scanning for document discovery.

Commands:
- aitao scan                   Scan configured paths for new/modified files
- aitao scan paths             Show configured scan paths
- aitao scan status            Show scanner state and stats
- aitao scan clear             Clear scanner state (force full rescan)
- aitao scan reindex           Re-queue docs indexed without a real semantic vector
"""

from pathlib import Path
from typing import Optional, List

import typer

from aitao.cli.utils import (
    console, success, error, warning, info,
    print_header, status_line, confirm, create_table, spinner,
    get_config_path
)


app = typer.Typer(
    help=(
        "Scan configured folders and discover new files.\n\n"
        "[bold cyan]Examples[/bold cyan]\n\n"
        "  Run an immediate scan:\n"
        "    [green]./aitao.sh scan run[/green]\n\n"
        "  Show configured folders:\n"
        "    [green]./aitao.sh scan paths[/green]\n\n"
        "  Last scan state:\n"
        "    [green]./aitao.sh scan status[/green]\n\n"
        "  Clear pending tasks:\n"
        "    [green]./aitao.sh scan clear[/green]"
    ),
    rich_markup_mode="rich",
)


def _enqueue_detected(config_path: str, result) -> int:
    """Queue the scan's new + modified files for indexing. Returns the count.

    The piège this fixes (US-086 v2): the scanner saves a file's 'seen' state as
    soon as it detects it, so a manual ``scan run`` that did NOT enqueue would
    consume the detection and hide the file from the daemon forever.
    """
    to_enqueue = list(result.new_files) + list(result.modified_files)
    if not to_enqueue:
        return 0
    from aitao.indexation.queue import TaskQueue

    queue = TaskQueue(config_path=config_path)
    for fi in to_enqueue:
        queue.add_task(str(fi.path), task_type="index")
    return len(to_enqueue)


@app.command("run")
def scan_run(
    paths: Optional[List[str]] = typer.Argument(
        None, help="Specific paths to scan (default: use config)"
    ),
    no_hash: bool = typer.Option(
        False, "--no-hash", help="Skip SHA256 hash computation (faster)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Scan but don't update state"
    ),
):
    """
    Scan filesystem for new and modified documents, and queue them for indexing.

    Scans the paths configured in config.toml (indexing.include_paths), identifies
    files that need indexing AND enqueues them so the worker picks them up. Without
    this, a manual scan used to save the "seen" state without queuing anything —
    silently hiding those files from the daemon (US-086 v2). Use --dry-run to
    preview detections without changing state or queuing.
    """
    print_header("Filesystem Scan")
    
    try:
        from aitao.indexation.scanner import FilesystemScanner
        
        config_path = str(get_config_path())
        
        with spinner("Initializing scanner..."):
            scanner = FilesystemScanner(config_path=config_path)
        
        # Show what we're scanning
        if paths:
            info(f"Scanning {len(paths)} specified path(s)")
        else:
            info(f"Scanning {len(scanner.include_paths)} configured path(s)")
            for p in scanner.include_paths:
                console.print(f"  [dim]{p}[/dim]")
        
        console.print()
        
        # Run the scan
        with spinner("Scanning filesystem..."):
            result = scanner.scan(
                paths=paths,
                compute_hashes=not no_hash,
                save_state=not dry_run
            )
        
        # Results
        console.print()
        if result.has_changes:
            if result.new_files:
                success(f"New files: {len(result.new_files)}")
                for f in result.new_files[:10]:  # Show first 10
                    console.print(f"  [green]+[/green] {Path(f.path).name}")
                if len(result.new_files) > 10:
                    console.print(f"  [dim]... and {len(result.new_files) - 10} more[/dim]")
            
            if result.modified_files:
                warning(f"Modified files: {len(result.modified_files)}")
                for f in result.modified_files[:10]:
                    console.print(f"  [yellow]~[/yellow] {Path(f.path).name}")
                if len(result.modified_files) > 10:
                    console.print(f"  [dim]... and {len(result.modified_files) - 10} more[/dim]")
            
            if result.deleted_paths:
                error(f"Deleted files: {len(result.deleted_paths)}")
                for p in result.deleted_paths[:10]:
                    console.print(f"  [red]-[/red] {Path(p).name}")
                if len(result.deleted_paths) > 10:
                    console.print(f"  [dim]... and {len(result.deleted_paths) - 10} more[/dim]")
        else:
            success("No changes detected")
        
        console.print()
        
        # Summary
        table = create_table("Scan Summary", [("Metric", "right"), ("Value", "left")])
        table.add_row("Files scanned", str(result.total_scanned))
        table.add_row("Files skipped", str(result.total_skipped))
        table.add_row("Duration", f"{result.scan_duration_seconds:.2f}s")
        if dry_run:
            table.add_row("Mode", "[yellow]Dry run (state not saved)[/yellow]")
        console.print(table)

        # Enqueue detected files so the worker actually indexes them. A dry run
        # previews only — it neither saves state nor queues (US-086 v2).
        if dry_run:
            console.print()
            info("Dry run: nothing queued, state unchanged")
        else:
            queued = _enqueue_detected(config_path, result)
            if queued:
                console.print()
                success(f"Queued {queued} file(s) for indexing")
                info("Start the worker to process them: [bold]./aitao.sh worker start[/bold]")

    except FileNotFoundError as e:
        error(f"Configuration not found: {e}")
        raise typer.Exit(1)
    except Exception as e:
        error(f"Scan failed: {e}")
        raise typer.Exit(1)


@app.command("paths")
def scan_paths():
    """Show configured scan paths."""
    print_header("Scan Paths")
    
    try:
        from aitao.indexation.scanner import FilesystemScanner
        
        config_path = str(get_config_path())
        scanner = FilesystemScanner(config_path=config_path)
        
        console.print("[bold]Include paths:[/bold]")
        if scanner.include_paths:
            for p in scanner.include_paths:
                exists = "✓" if p.exists() else "✗"
                color = "green" if p.exists() else "red"
                console.print(f"  [{color}]{exists}[/{color}] {p}")
        else:
            warning("No include paths configured")
        
        console.print()
        console.print("[bold]Exclude directories:[/bold]")
        console.print(f"  [dim]{', '.join(sorted(scanner.exclude_dirs)[:10])}...[/dim]")
        
        console.print()
        console.print("[bold]Supported extensions:[/bold]")
        exts = sorted(scanner.supported_extensions)
        console.print(f"  [dim]{', '.join(exts[:15])}... ({len(exts)} total)[/dim]")
        
    except Exception as e:
        error(f"Error: {e}")
        raise typer.Exit(1)


@app.command("status")
def scan_status():
    """Show scanner state and statistics."""
    print_header("Scanner Status")
    
    try:
        from aitao.indexation.scanner import FilesystemScanner
        
        config_path = str(get_config_path())
        scanner = FilesystemScanner(config_path=config_path)
        
        stats = scanner.get_stats()
        
        status_line("State file", stats["state_file"])
        status_line("Tracked files", str(stats["tracked_files"]))
        status_line("Include paths", str(len(stats["include_paths"])))
        status_line("Supported extensions", str(stats["supported_extensions"]))
        
    except Exception as e:
        error(f"Error: {e}")
        raise typer.Exit(1)


@app.command("clear")
def scan_clear(
    skip_confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
):
    """Clear scanner state (force full rescan on next run)."""
    try:
        from aitao.indexation.scanner import FilesystemScanner
        
        config_path = str(get_config_path())
        scanner = FilesystemScanner(config_path=config_path)
        
        stats = scanner.get_stats()
        tracked = stats["tracked_files"]
        
        if tracked == 0:
            info("Scanner state is already empty")
            return
        
        warning(f"This will clear state for {tracked} tracked files.")
        
        if not skip_confirm and not confirm("Proceed?"):
            info("Cancelled")
            raise typer.Exit(0)
        
        scanner.clear_state()
        success("Scanner state cleared")
        info("Next scan will treat all files as new")

    except Exception as e:
        error(f"Error: {e}")
        raise typer.Exit(1)


@app.command("reindex")
def scan_reindex(
    missing_vectors: bool = typer.Option(
        True,
        "--missing-vectors",
        help="Re-queue documents indexed without a semantic vector (empty extraction/OCR)",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n",
        help="Show what would be re-queued without submitting tasks",
    ),
):
    """Re-index documents indexed in Meilisearch without a real semantic vector.

    ÉPIC-31 (US-113): v4.0 is fusion-only — a document's semantic vector lives
    on the Meilisearch document itself (``_vectors.default``), not in a
    separate LanceDB table. ``has_content=false`` (see
    ``MeilisearchClient.add_document``) flags documents whose
    extraction/OCR yielded nothing, hence no real vector — these are the
    ones worth re-queuing.
    """
    try:
        from aitao.search.meilisearch_client import (
            MeilisearchConnectionError,
            MeilisearchError,
        )
        from aitao.storage.repository import make_meilisearch_client
        from aitao.indexation.queue import TaskQueue, TaskType

        # Retrieve all paths known to Meilisearch
        info("Fetching Meilisearch document list...")
        try:
            meili = make_meilisearch_client()
            meili_paths: set = set(meili.get_all_document_paths())
        except MeilisearchConnectionError as exc:
            error(f"Cannot connect to Meilisearch: {exc}")
            raise typer.Exit(1)
        except MeilisearchError as exc:
            error(f"Meilisearch error: {exc}")
            raise typer.Exit(1)

        info("Checking for documents without a semantic vector...")
        missing: set = set(meili.get_incomplete_document_paths())

        console.print()
        console.print(f"[bold]Meilisearch:[/bold]  {len(meili_paths)} document(s) indexed")
        console.print(f"[bold]Sans vecteur:[/bold] {len(missing)} document(s)")
        console.print(f"[bold]Missing:[/bold]      {len(missing)} document(s) need re-indexing")
        console.print()

        if not missing:
            success("All documents already have vectors. Nothing to do.")
            return

        if dry_run:
            info("[dry-run] Files that would be re-queued:")
            for p in sorted(missing):
                console.print(f"  {p}")
            return

        # Submit missing files to the indexation queue. metadata={"force": True}
        # is required — without it the worker's dedup/mtime check
        # (DocumentIndexer._is_already_indexed) sees an unchanged file and
        # skips it as "already indexed", silently defeating the whole point
        # of this command (found live: 254 vector-less documents requeued
        # without this flag were all short-circuited, none re-embedded).
        queue = TaskQueue()
        tasks = queue.add_tasks_batch(
            list(missing),
            task_type=TaskType.REINDEX.value,
            priority="normal",
            metadata={"force": True, "reason": "missing_vector"},
        )

        success(f"{len(tasks)} file(s) submitted to the queue for re-indexing")
        info("Run [bold]aitao worker start[/bold] to process the queue")

    except typer.Exit:
        raise
    except Exception as e:
        error(f"Error: {e}")
        raise typer.Exit(1)


@app.command("help")
def help_cmd(ctx: typer.Context) -> None:
    """Show this help message and exit."""
    typer.echo(ctx.parent.get_help())
