# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
CLI commands for Task Queue management.

This module provides commands to manage the document processing queue:
- View queue status and statistics
- List pending/completed/failed tasks
- Add files to queue manually
- Clear completed tasks
- Retry failed tasks
"""

import typer
from pathlib import Path
from typing import Optional
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from aitao.cli.utils import (
    get_config_path,
    success,
    error,
    warning,
    info as show_info,
    spinner,
)
from aitao.indexation.queue import TaskQueue, TaskStatus, TaskPriority, TaskType

console = Console()
app = typer.Typer(
    help=(
        "Document processing queue.\n\n"
        "[bold cyan]Common tasks[/bold cyan]\n\n"
        "  View failures:\n"
        "    [green]./aitao.sh queue list failed[/green]\n"
        "    [green]./aitao.sh queue list failed -n 100[/green]   (100 results)\n\n"
        "  Task details:\n"
        "    [green]./aitao.sh queue info a3f1bc2d[/green]\n\n"
        "  Retry failures:\n"
        "    [green]./aitao.sh queue retry[/green]\n"
        "    [green]./aitao.sh start[/green]               (restart worker)\n\n"
        "  View all tasks:\n"
        "    [green]./aitao.sh queue status[/green]         (global counters)\n"
        "    [green]./aitao.sh queue list pending[/green]   (pending)\n"
        "    [green]./aitao.sh queue list[/green]           (all)"
    ),
    rich_markup_mode="rich",
)


def get_queue() -> TaskQueue:
    """Get configured TaskQueue instance."""
    config_path = get_config_path()
    return TaskQueue(config_path=config_path)


def get_failed_tracker():
    """Get the persistent failed-files tracker (US-086 v4 visibility)."""
    from aitao.core.failed_files_tracker import FailedFilesTracker

    return FailedFilesTracker()


def _failure_summary_lines(tracker) -> list:
    """Persistent-failure lines for the queue panel, or [] if none/unavailable.

    These come from the failed-files tracker, NOT the queue: a file that has
    exhausted its retries is never re-enqueued (US-086 v3), so it would be
    invisible in the queue counters. This surfaces it.
    """
    try:
        stats = tracker.get_stats()
    except Exception:
        return []
    total = stats.get("total_failed", 0)
    if not total:
        return []
    retryable = stats.get("retryable", 0)
    given_up = total - retryable
    lines = [
        "",
        "[bold red]🚫 Persistent failures (index):[/bold red]"
        f" {total}  [dim](retryable {retryable}, given up {given_up})[/dim]",
    ]
    by_reason = stats.get("by_reason", {})
    for reason, count in sorted(by_reason.items(), key=lambda kv: -kv[1]):
        lines.append(f"    {reason}: {count}")
    lines.append("[dim]    → details: ./aitao.sh queue failures[/dim]")
    return lines


def _elapsed(started_at: Optional[str]) -> str:
    """Return a short ' (12s)' / ' (1m05s)' suffix from an ISO timestamp."""
    if not started_at:
        return ""
    try:
        from datetime import datetime
        secs = max(0, int((datetime.now() - datetime.fromisoformat(started_at)).total_seconds()))
        return f" ({secs}s)" if secs < 60 else f" ({secs // 60}m{secs % 60:02d}s)"
    except Exception:
        return ""


def _render_queue_panel(queue) -> Panel:
    """Build the queue status panel, including the file(s) being indexed now."""
    stats = queue.get_stats()
    lines = [
        f"[bold]Total tasks:[/bold] {stats['total']}",
        "",
        f"[yellow]⏳ Pending:[/yellow] {stats['pending']}",
        f"[blue]⚙️  Processing:[/blue] {stats['processing']}",
        f"[green]✅ Completed:[/green] {stats['completed']}",
        f"[red]❌ Failed:[/red] {stats['failed']}",
    ]

    # Live snapshot: which file(s) the worker is indexing right now
    try:
        processing = queue.list_tasks(status=TaskStatus.PROCESSING.value, limit=5)
    except Exception:
        processing = []
    if processing:
        lines.append("")
        lines.append("[bold blue]⚙️  En cours d'indexation:[/bold blue]")
        for task in processing:
            lines.append(f"  • {Path(task.file_path).name}{_elapsed(task.started_at)}")

    if stats['pending'] > 0:
        lines.append("")
        lines.append("[bold]Priority breakdown:[/bold]")
        lines.append(f"  🔴 High: {stats['by_priority'].get('high', 0)}")
        lines.append(f"  🟡 Normal: {stats['by_priority'].get('normal', 0)}")
        lines.append(f"  🟢 Low: {stats['by_priority'].get('low', 0)}")

    if stats['by_type']:
        lines.append("")
        lines.append("[bold]By task type:[/bold]")
        for task_type, count in stats['by_type'].items():
            lines.append(f"  {task_type}: {count}")

    # Persistent failures live in their own store, not the queue (US-086 v4).
    try:
        lines.extend(_failure_summary_lines(get_failed_tracker()))
    except Exception:
        pass

    return Panel("\n".join(lines), title="📋 Task Queue Status", border_style="cyan")


@app.command()
def status(
    watch: bool = typer.Option(
        False, "--watch", "-w", help="Live view — refresh until Ctrl-C"
    ),
):
    """Show queue status, including the file(s) currently being indexed.

    Use --watch for a live view that refreshes as files are processed.
    """
    try:
        queue = get_queue()
        if watch:
            import time as _time
            from rich.live import Live

            with Live(
                _render_queue_panel(queue), console=console, refresh_per_second=4
            ) as live:
                try:
                    while True:
                        _time.sleep(1.0)
                        live.update(_render_queue_panel(queue))
                except KeyboardInterrupt:
                    pass
            return

        console.print(_render_queue_panel(queue))
    except Exception as e:
        error(f"Failed to get queue status: {e}")
        raise typer.Exit(1)


@app.command("list")
def list_tasks(
    status_arg: Optional[str] = typer.Argument(
        None, 
        help="Filter by status: pending, completed, failed, processing, cancelled"
    ),
    pending: bool = typer.Option(False, "--pending", "-p", help="Show only pending tasks"),
    completed: bool = typer.Option(False, "--completed", "-c", help="Show only completed tasks"),
    failed: bool = typer.Option(False, "--failed", "-f", help="Show only failed tasks"),
    limit: int = typer.Option(20, "--limit", "-n", help="Maximum number of tasks to show")
):
    """List tasks in the queue.
    
    Examples:
        ./aitao.sh queue list           # Show all tasks
        ./aitao.sh queue list failed    # Show only failed tasks
        ./aitao.sh queue list pending   # Show only pending tasks
        ./aitao.sh queue list -n 50     # Show up to 50 tasks
    """
    try:
        queue = get_queue()
        
        # Determine status filter (positional arg takes precedence)
        status_filter = None
        
        # Check positional argument first
        if status_arg:
            valid_statuses = ["pending", "completed", "failed", "processing", "cancelled"]
            if status_arg.lower() not in valid_statuses:
                error(f"Invalid status: {status_arg}")
                show_info(f"Valid statuses: {', '.join(valid_statuses)}")
                raise typer.Exit(1)
            status_filter = status_arg.lower()
        # Then check option flags
        elif pending:
            status_filter = TaskStatus.PENDING.value
        elif completed:
            status_filter = TaskStatus.COMPLETED.value
        elif failed:
            status_filter = TaskStatus.FAILED.value
        
        tasks = queue.list_tasks(status=status_filter, limit=limit)
        
        if not tasks:
            show_info("Queue is empty" if not status_filter else f"No {status_filter} tasks")
            return
        
        # Create table
        table = Table(title=f"Tasks ({len(tasks)} shown)")
        table.add_column("ID", style="dim", width=8)
        table.add_column("File", style="cyan", max_width=40)
        table.add_column("Type", width=10)
        table.add_column("Priority", width=8)
        table.add_column("Status", width=12)
        table.add_column("Added", width=16)
        
        status_styles = {
            "pending": "yellow",
            "processing": "blue",
            "completed": "green",
            "failed": "red",
            "cancelled": "dim"
        }
        
        priority_icons = {
            "high": "🔴",
            "normal": "🟡", 
            "low": "🟢"
        }
        
        for task in tasks:
            file_name = Path(task.file_path).name if task.file_path else "-"
            if len(file_name) > 38:
                file_name = file_name[:35] + "..."
            
            status_style = status_styles.get(task.status, "white")
            priority_icon = priority_icons.get(task.priority, "")
            
            # Format date
            added = task.added_at[:16] if task.added_at else "-"
            
            table.add_row(
                task.id,
                file_name,
                task.task_type,
                f"{priority_icon} {task.priority}",
                f"[{status_style}]{task.status}[/{status_style}]",
                added
            )
        
        console.print(table)
        
    except Exception as e:
        error(f"Failed to list tasks: {e}")
        raise typer.Exit(1)


@app.command()
def add(
    file_path: str = typer.Argument(..., help="Path to file to add"),
    task_type: str = typer.Option("index", "--type", "-t", help="Task type (index, ocr)"),
    priority: str = typer.Option("normal", "--priority", "-p", help="Priority (high, normal, low)")
):
    """Add a file to the processing queue."""
    import os
    try:
        # Resolve path from original working directory
        orig_pwd = os.environ.get("AITAO_ORIG_PWD", os.getcwd())
        path = Path(file_path).expanduser()
        if not path.is_absolute():
            path = Path(orig_pwd) / path
        
        if not path.exists():
            error(f"File not found: {file_path}")
            raise typer.Exit(1)
        
        # Validate task type
        valid_types = [t.value for t in TaskType]
        if task_type not in valid_types:
            error(f"Invalid task type. Valid: {', '.join(valid_types)}")
            raise typer.Exit(1)
        
        # Validate priority
        valid_priorities = [p.value for p in TaskPriority]
        if priority not in valid_priorities:
            error(f"Invalid priority. Valid: {', '.join(valid_priorities)}")
            raise typer.Exit(1)
        
        queue = get_queue()
        task = queue.add_task(
            str(path.absolute()),
            task_type=task_type,
            priority=priority
        )
        
        success(f"Task added: {task.id}")
        console.print(f"  File: [cyan]{path.name}[/cyan]")
        console.print(f"  Type: {task_type}, Priority: {priority}")
        
    except typer.Exit:
        raise
    except Exception as e:
        error(f"Failed to add task: {e}")
        raise typer.Exit(1)


@app.command()
def clear(
    all_tasks: bool = typer.Option(False, "--all", "-a", help="Clear all tasks (not just completed)"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation")
):
    """Clear completed tasks from the queue."""
    try:
        queue = get_queue()
        stats = queue.get_stats()
        
        if all_tasks:
            count = stats["total"]
            action = "ALL tasks"
        else:
            count = stats["completed"]
            action = "completed tasks"
        
        if count == 0:
            show_info(f"No {action} to clear")
            return
        
        if not force:
            confirm = typer.confirm(f"Clear {count} {action}?")
            if not confirm:
                show_info("Cancelled")
                return
        
        with spinner("Clearing tasks..."):
            if all_tasks:
                removed = queue.clear_all()
            else:
                removed = queue.clear_completed()
        
        success(f"Cleared {removed} tasks")
        
    except Exception as e:
        error(f"Failed to clear tasks: {e}")
        raise typer.Exit(1)


@app.command()
def retry():
    """Retry failed tasks (up to max retries)."""
    try:
        queue = get_queue()
        stats = queue.get_stats()
        
        if stats["failed"] == 0:
            show_info("No failed tasks to retry")
            return
        
        with spinner("Retrying failed tasks..."):
            count = queue.retry_failed()
        
        if count > 0:
            success(f"Reset {count} tasks to pending")
        else:
            warning("All failed tasks have exceeded max retries")
        
    except Exception as e:
        error(f"Failed to retry tasks: {e}")
        raise typer.Exit(1)


@app.command()
def cancel(
    task_id: str = typer.Argument(..., help="Task ID to cancel")
):
    """Cancel a pending task."""
    try:
        queue = get_queue()
        task = queue.get_task(task_id)
        
        if not task:
            error(f"Task not found: {task_id}")
            raise typer.Exit(1)
        
        if task.status != TaskStatus.PENDING.value:
            warning(f"Task is not pending (status: {task.status})")
            return
        
        if queue.cancel_task(task_id):
            success(f"Task {task_id} cancelled")
        else:
            error("Failed to cancel task")
            raise typer.Exit(1)
        
    except typer.Exit:
        raise
    except Exception as e:
        error(f"Failed to cancel task: {e}")
        raise typer.Exit(1)


@app.command()
def info(
    task_id: str = typer.Argument(..., help="Task ID to inspect")
):
    """Show detailed information about a task."""
    try:
        queue = get_queue()
        task = queue.get_task(task_id)
        
        if not task:
            error(f"Task not found: {task_id}")
            raise typer.Exit(1)
        
        status_colors = {
            "pending": "yellow",
            "processing": "blue", 
            "completed": "green",
            "failed": "red",
            "cancelled": "dim"
        }
        
        color = status_colors.get(task.status, "white")
        
        lines = [
            f"[bold]ID:[/bold] {task.id}",
            f"[bold]File:[/bold] {task.file_path}",
            f"[bold]Type:[/bold] {task.task_type}",
            f"[bold]Priority:[/bold] {task.priority}",
            f"[bold]Status:[/bold] [{color}]{task.status}[/{color}]",
            f"[bold]Added:[/bold] {task.added_at}",
        ]
        
        if task.started_at:
            lines.append(f"[bold]Started:[/bold] {task.started_at}")
        
        if task.completed_at:
            lines.append(f"[bold]Completed:[/bold] {task.completed_at}")
        
        if task.error_message:
            lines.append(f"[bold red]Error:[/bold red] {task.error_message}")
        
        if task.retry_count > 0:
            lines.append(f"[bold]Retries:[/bold] {task.retry_count}")
        
        if task.metadata:
            lines.append(f"[bold]Metadata:[/bold] {task.metadata}")
        
        console.print(Panel(
            "\n".join(lines),
            title=f"Task {task_id}",
            border_style=color
        ))
        
    except typer.Exit:
        raise
    except Exception as e:
        error(f"Failed to get task info: {e}")
        raise typer.Exit(1)


def _build_failures_table(failures: dict, limit: int, exhausted_only: bool, ceiling: int) -> Table:
    """Build the persistent-failures table from tracker entries (pure helper)."""
    table = Table(title=f"Persistent index failures ({len(failures)} total)")
    table.add_column("File", style="cyan", max_width=42)
    table.add_column("Reason", width=14)
    table.add_column("Retries", justify="right", width=8)
    table.add_column("State", width=10)
    table.add_column("Last error", style="dim", max_width=40)

    # Most-retried first — those closest to (or past) the ceiling matter most.
    items = sorted(failures.items(), key=lambda kv: -kv[1].get("retry_count", 0))
    shown = 0
    for path, info in items:
        retries = info.get("retry_count", 0)
        exhausted = retries >= ceiling
        if exhausted_only and not exhausted:
            continue
        name = Path(path).name or path
        if len(name) > 40:
            name = name[:37] + "..."
        err = (info.get("error", "") or "").splitlines()[0][:38]
        state = "[red]given up[/red]" if exhausted else "[yellow]retryable[/yellow]"
        table.add_row(name, info.get("reason", "unknown"), str(retries), state, err)
        shown += 1
        if shown >= limit:
            break
    return table


@app.command()
def failures(
    limit: int = typer.Option(30, "--limit", "-n", help="Maximum rows to show"),
    exhausted_only: bool = typer.Option(
        False, "--given-up", "-g", help="Only files that exhausted their retries"
    ),
):
    """List files that persistently failed to index (with reason and retry count).

    These come from the failed-files tracker, not the queue: a file that has
    exhausted its retries is never re-enqueued, so it is invisible in
    `queue status`/`queue list`. Use this to see what needs manual attention.
    """
    try:
        tracker = get_failed_tracker()
        all_failed = tracker.all_failures()
        if not all_failed:
            success("No persistent index failures 🎉")
            return
        table = _build_failures_table(
            all_failed, limit=limit, exhausted_only=exhausted_only, ceiling=3
        )
        if table.row_count == 0:
            show_info("No files have exhausted their retries")
            return
        console.print(table)
    except Exception as e:
        error(f"Failed to list failures: {e}")
        raise typer.Exit(1)


@app.command("help")
def help_cmd(ctx: typer.Context) -> None:
    """Show this help message and exit."""
    typer.echo(ctx.parent.get_help())
