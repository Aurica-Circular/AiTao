# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Rendering and execution helpers for `aitao index prune` (US-126-A).

Kept out of index.py to respect the project's per-file line budget — the
`@app.command("prune")` wrapper in index.py stays a thin orchestrator; the
console rendering and the delete loop live here as plain functions so they
can be unit-tested without going through Typer/CliRunner if needed.
"""

from pathlib import Path
from typing import Any, Dict, List, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TaskProgressColumn, TextColumn


def render_orphans_report(console: Console, orphans: List[str], groups: Dict[str, List[str]]) -> None:
    """Print the out-of-scope report grouped by folder, capped at 10 rows per group."""
    console.print(
        Panel(
            f"[yellow]{len(orphans)} out-of-scope document(s) found[/yellow] "
            f"across {len(groups)} folder(s).",
            title="Prune — Out-of-Scope Documents",
        )
    )
    for folder, paths in groups.items():
        console.print(f"\n[cyan]{folder}[/cyan] ({len(paths)})")
        for p in paths[:10]:
            console.print(f"  [dim]{p}[/dim]")
        if len(paths) > 10:
            console.print(f"  [dim]... and {len(paths) - 10} more[/dim]")


def delete_orphans(console: Console, meili: Any, orphans: List[str]) -> Tuple[int, int]:
    """Delete every path in `orphans` via `meili.delete_by_path`, with a progress bar.

    Returns (deleted_count, failed_count). Never raises — a per-path failure
    (exception or falsy return) is counted as `failed` and the loop continues,
    so one bad path cannot abort the rest of the cleanup.
    """
    deleted = failed = 0
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Pruning...", total=len(orphans))
        for p in orphans:
            try:
                if meili.delete_by_path(p):
                    deleted += 1
                else:
                    failed += 1
            except Exception:
                failed += 1
            progress.update(task, advance=1, description=f"Pruning {Path(p).name[:30]}...")

    return deleted, failed
