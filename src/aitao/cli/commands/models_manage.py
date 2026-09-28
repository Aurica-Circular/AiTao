# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
CLI: Model Management Commands (simplified in v3).

AiTao v3 delegates model management entirely to Ollama.
These commands now redirect to the appropriate `ollama` CLI instructions.

Use `ollama` directly:
  ollama pull <model>     — download a model
  ollama list             — list installed models
  ollama rm <model>       — remove a model
"""

import typer
from rich.console import Console
from rich.panel import Panel

console = Console()


def pull():
    """
    Download a model — use `ollama pull` directly.

    AiTao v3 does not manage Ollama models.
    """
    console.print(Panel(
        "[yellow]Model management is handled by Ollama directly.[/yellow]\n\n"
        "To download a model:\n"
        "  [cyan]ollama pull llama3.1:8b[/cyan]\n\n"
        "To list available models:\n"
        "  [cyan]ollama list[/cyan]\n\n"
        "Browse models at: [cyan]https://ollama.com/models[/cyan]",
        title="Use ollama CLI",
        border_style="yellow",
    ))
    raise typer.Exit(code=0)


def add():
    """Add a model — use `ollama pull` directly."""
    pull()


def remove():
    """Remove a model — use `ollama rm` directly."""
    console.print(Panel(
        "[yellow]Model management is handled by Ollama directly.[/yellow]\n\n"
        "To remove a model:\n"
        "  [cyan]ollama rm <model-name>[/cyan]\n\n"
        "To list installed models:\n"
        "  [cyan]ollama list[/cyan]",
        title="Use ollama CLI",
        border_style="yellow",
    ))
    raise typer.Exit(code=0)
