# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
AiTao CLI main entry point.

This module defines the root Typer application and registers
all command groups. Run with:

    python -m aitao.cli --help
    python -m aitao.cli status
    python -m aitao.cli ms upgrade
"""

import sys
from pathlib import Path
from typing import List

import typer


def _find_project_root() -> Path:
    """Walk up from this file searching for project markers (aitao.sh, pyproject.toml).

    Bootstrap before import, duplicated intentionally — see path_manager.py —
    do not add other copies elsewhere. This module can run standalone
    (``python -m aitao.cli``) before `aitao` is guaranteed to be on
    sys.path, so it cannot depend on aitao.core.pathmanager here.
    """
    current = Path(__file__).resolve().parent
    for _ in range(10):
        if (current / "aitao.sh").exists() or (current / "pyproject.toml").exists():
            return current
        parent = current.parent
        if parent == current:
            return current
        current = parent
    return current


# Ensure src is in path
_PROJECT_ROOT = _find_project_root()
src_path = _PROJECT_ROOT / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from aitao.cli.utils import console  # noqa: E402
from aitao.cli.commands import status as status_cmd  # noqa: E402
from aitao.cli.commands import dashboard as dashboard_cmd  # noqa: E402
from aitao.cli.commands import meilisearch as ms_cmd  # noqa: E402
from aitao.cli.commands import database as db_cmd  # noqa: E402
from aitao.cli.commands import config as config_cmd  # noqa: E402
from aitao.cli.commands import scan as scan_cmd  # noqa: E402
from aitao.cli.commands import queue as queue_cmd  # noqa: E402
from aitao.cli.commands import worker as worker_cmd  # noqa: E402
from aitao.cli.commands import extract as extract_cmd  # noqa: E402
from aitao.cli.commands import index as index_cmd  # noqa: E402
from aitao.cli.commands import search as search_cmd  # noqa: E402
from aitao.cli.commands import lifecycle as lifecycle_cmd  # noqa: E402
from aitao.cli.commands import models as models_cmd  # noqa: E402
from aitao.cli.commands import api as api_cmd  # noqa: E402
from aitao.cli.commands import license as license_cmd  # noqa: E402
from aitao.cli.commands import init as init_cmd  # noqa: E402
from aitao.cli.commands import mcp as mcp_cmd  # noqa: E402
from aitao.cli.commands import migrate as migrate_cmd  # noqa: E402

# Import version
try:
    from aitao.core.version import get_version
except ImportError:
    def get_version() -> str:
        return "unknown"


# Create main app
app = typer.Typer(
    name="aitao",
    help=(
        "AiTao — Local document search and indexing engine.\n\n"
        "[bold cyan]Quick start[/bold cyan]\n\n"
        "  [green]./aitao.sh start[/green]              Start core services\n"
        "  [green]./aitao.sh dashboard[/green]           Full status at a glance\n"
        "  [green]./aitao.sh stop[/green]               Stop core services\n\n"
        "[bold cyan]Failed files?[/bold cyan]\n\n"
        "  [green]./aitao.sh queue list failed[/green]   List failed files\n"
        "  [green]./aitao.sh queue retry[/green]         Requeue failed tasks\n"
        "  [green]./aitao.sh start[/green]               Restart processing\n\n"
        "[bold cyan]Incomplete vectorization (LanceDB < Meilisearch)?[/bold cyan]\n\n"
        "  [green]./aitao.sh scan run[/green]            Re-scan configured folders\n"
        "  [green]./aitao.sh start[/green]               Complete vectorization\n\n"
        "[bold cyan]Task details?[/bold cyan]\n\n"
        "  [green]./aitao.sh queue list failed -n 100[/green]  List up to 100 failures\n"
        "  [green]./aitao.sh queue info <TASK_ID>[/green]      Full task details"
    ),
    no_args_is_help=True,
    rich_markup_mode="rich",
    add_completion=True,
)

# Register command groups — help text is defined in each sub-module's typer.Typer()
app.add_typer(ms_cmd.app, name="ms")
app.add_typer(db_cmd.app, name="db")
app.add_typer(config_cmd.app, name="config")
app.add_typer(scan_cmd.app, name="scan")
app.add_typer(queue_cmd.app, name="queue")
app.add_typer(worker_cmd.app, name="worker")
app.add_typer(extract_cmd.app, name="extract")
app.add_typer(index_cmd.app, name="index")
app.add_typer(search_cmd.app, name="search")
app.add_typer(lifecycle_cmd.app, name="lifecycle")
app.add_typer(models_cmd.app, name="models")
app.add_typer(api_cmd.app, name="api")
app.add_typer(license_cmd.app, name="license")
app.add_typer(mcp_cmd.app, name="mcp")

app.command("migrate-v4")(migrate_cmd.migrate_v4)


@app.command()
def init():
    """Configure AiTao for the first time — interactive wizard."""
    init_cmd.run_wizard()


@app.command()
def status():
    """Show AiTao system status."""
    status_cmd.show_status()


@app.command()
def dashboard():
    """Show AiTao dashboard — all services, models, index and errors at a glance."""
    dashboard_cmd.show_dashboard()


@app.command()
def version():
    """Show AiTao version."""
    ver = get_version()
    console.print(f"[bold cyan]aitao[/bold cyan] {ver}")


@app.command()
def docs(
    output: str = typer.Option(
        "docs/COMMANDS.md", "--output", "-o", help="Output Markdown path"
    ),
):
    """Generate the CLI command reference (docs/COMMANDS.md) from the live CLI."""
    from pathlib import Path

    from aitao.cli.commands import docs as docs_mod

    path = docs_mod.write_commands_doc(app, Path(output))
    console.print(f"[green]✓[/green] Command reference written to [cyan]{path}[/cyan]")


@app.command()
def test(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
):
    """Run unit tests."""
    import subprocess
    
    cmd = ["pytest", "tests/unit/", "-v" if verbose else "-q"]
    result = subprocess.run(cmd, cwd=src_path.parent)
    raise typer.Exit(result.returncode)


@app.command()
def start(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
):
    """Start core AiTao services (Meilisearch, Worker, API).

    The MCP server is not included — start it separately with:
      ./aitao.sh mcp serve --transport sse --daemon

    Shortcut for: ./aitao.sh lifecycle start
    """
    lifecycle_cmd.start(verbose=verbose)


@app.command()
def stop(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
):
    """Stop core AiTao services (Worker, Meilisearch, API).

    To also stop the MCP daemon: ./aitao.sh mcp stop

    Shortcut for: ./aitao.sh lifecycle stop
    """
    lifecycle_cmd.stop(verbose=verbose)


@app.command()
def restart(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
):
    """Restart core AiTao services (Meilisearch, Worker, API).

    Shortcut for: ./aitao.sh lifecycle restart
    """
    lifecycle_cmd.restart(verbose=verbose)


@app.callback()
def main(
    ctx: typer.Context,
    debug: bool = typer.Option(False, "--debug", "-d", help="Enable debug output"),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Suppress log output"),
):
    import os
    
    # Set quiet mode by default in CLI (cleaner output with spinners)
    if not debug:
        os.environ["AITAO_QUIET"] = "1"
    
    if quiet:
        os.environ["AITAO_QUIET"] = "1"
    
    if debug:
        import logging
        os.environ.pop("AITAO_QUIET", None)  # Unset quiet in debug mode
        os.environ["AITAO_LOG_LEVEL"] = "DEBUG"
        logging.basicConfig(level=logging.DEBUG)
        ctx.obj = {"debug": True}


def normalize_help_argv(argv: List[str]) -> List[str]:
    """Treat a trailing bare ``help`` / ``-h`` as ``--help`` (US-29).

    Typer leaf commands take positional arguments, so ``queue add help`` would
    otherwise treat "help" as a filename. Converting a trailing ``help``/``-h``
    to ``--help`` makes help work after any command, consistent with the
    groups' ``help`` subcommand. Only the last token is touched, so an explicit
    ``--help`` or a real argument elsewhere is left alone.
    """
    if len(argv) >= 1 and argv[-1].lower() in ("help", "-h"):
        return argv[:-1] + ["--help"]
    return argv


def run():
    """Entry point for console script."""
    import sys

    sys.argv = sys.argv[:1] + normalize_help_argv(sys.argv[1:])
    app()


if __name__ == "__main__":
    run()
