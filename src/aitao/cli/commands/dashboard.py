# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Dashboard command — AiTao system overview at a glance.

Orchestrates data collection and renders a Rich dashboard by delegating
panel rendering to dashboard_panels.py.  Displays: services, models,
index stats, worker/scan status, MCP server, and recent errors.
"""

import socket
import datetime

from rich.console import Console
from rich.columns import Columns
from rich.panel import Panel

from aitao.cli.utils import get_config_path
from aitao.cli.commands.dashboard_panels import (
    section_services,
    section_models,
    section_index,
    section_worker,
    section_errors,
    section_mcp,
)

console = Console()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """Return True if a TCP port is accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def show_dashboard():
    """Render the full AiTao dashboard."""
    # Load config
    try:
        from aitao.core.config import ConfigManager
        config = ConfigManager(str(get_config_path()))
    except Exception as e:
        console.print(f"[red]Impossible de lire la config: {e}[/red]")
        return

    ollama_url = config.llm.ollama_url
    ms_url     = config.search.meilisearch.url

    # Header
    from aitao.core.version import get_version
    now = datetime.datetime.now().strftime("%d %b %Y  %H:%M")
    console.print()
    console.print(Panel(
        f"[bold white]AiTao Dashboard[/bold white]  [dim]—[/dim]  "
        f"[cyan]v{get_version()}[/cyan]  [dim]—[/dim]  [dim]{now}[/dim]",
        border_style="bright_cyan",
        expand=False,
    ))
    console.print()

    # Services + Models side-by-side
    services_panel = section_services(config, _port_open)
    models_panel   = section_models(ollama_url)
    console.print(Columns([services_panel, models_panel], equal=True, expand=True))
    console.print()

    # Pre-compute worker state once — shared by index, worker, and errors sections
    try:
        from aitao.indexation.worker import BackgroundWorker
        _worker     = BackgroundWorker(config_path=get_config_path())
        _is_running = _worker.is_running()
    except Exception:
        _worker     = None
        _is_running = False

    # Index + Worker side-by-side
    index_panel  = section_index(config, ms_url, is_worker_running=_is_running)
    worker_panel = section_worker(config, worker=_worker)
    console.print(Columns([index_panel, worker_panel], equal=False, expand=True))
    console.print()

    # MCP Server — full-width
    console.print(section_mcp(_port_open))
    console.print()

    # Errors (only if there are any)
    errors_panel = section_errors(config, worker=_worker)
    if errors_panel:
        console.print(errors_panel)
        console.print()

    console.print("[dim]Appuie sur Ctrl+C pour quitter.[/dim]")
    console.print()
