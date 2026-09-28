# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
CLI: Model Management Commands — Hub Module (US-021b).

Registers all model subcommands under `./aitao.sh models`.
Delegates implementation to:
- models_status: status, check, validate (diagnostic / read-only)
- models_manage: pull, add, remove, fix (write / mutate)

This module owns the Typer app instance consumed by cli/main.py.
"""

import sys
from pathlib import Path

import typer


def _find_project_root() -> Path:
    """Walk up from this file searching for project markers (aitao.sh, pyproject.toml).

    Bootstrap before import, duplicated intentionally — see path_manager.py —
    do not add other copies elsewhere. This module can run standalone
    (``python -m aitao.cli.commands.models``) before `aitao` is guaranteed to
    be on sys.path, so it cannot depend on aitao.core.pathmanager here.
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
src_path = _find_project_root() / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

# Import command functions from sub-modules
from aitao.cli.commands.models_status import status, check, validate, fix  # noqa: E402
from aitao.cli.commands.models_manage import pull, add, remove             # noqa: E402

app = typer.Typer(
    help="Model management commands",
    short_help="Manage LLM models",
    no_args_is_help=True,
)

# Register diagnostic commands
app.command()(status)
app.command()(check)
app.command()(validate)

# Register management commands
app.command()(pull)
app.command()(add)
app.command()(remove)
app.command()(fix)


@app.command("help")
def help_cmd(ctx: typer.Context) -> None:
    """Show this help message and exit."""
    typer.echo(ctx.parent.get_help())


if __name__ == "__main__":
    app()
