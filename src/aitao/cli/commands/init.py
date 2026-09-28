# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
CLI: First-time configuration wizard (US-054).

Implements `./aitao.sh init` — an interactive 3-step wizard that:
1. Asks which folders to index (`include_paths`)
2. Asks which AI model to use (`default_model`) — auto-discovers from Ollama
3. Asks for user identity (`who_are_you`)

The wizard writes a minimal config/config.toml (based on config.toml.starter).
Existing config is preserved unless the user explicitly confirms overwrite.

Design principles:
- AC-003: No print() in production — Rich console used exclusively
- Less than 350 lines (file size limit)
- Graceful degradation: Ollama offline → manual model name entry
"""

import subprocess
from pathlib import Path
from typing import List

import typer
from rich.console import Console
from rich.panel import Panel

from aitao.core.pathmanager import path_manager

console = Console()

# Path constants (resolved relative to the aitao project root)
_PROJECT_ROOT = path_manager.root
_CONFIG_DIR = _PROJECT_ROOT / "config"
_CONFIG_PATH = _CONFIG_DIR / "config.toml"
_STARTER_PATH = _CONFIG_DIR / "config.toml.starter"


# ──────────────────────────────────────────────────────────────────────────────

def _discover_ollama_models() -> List[dict]:
    """
    Run `ollama list` and parse the output.

    Returns a list of dicts: [{"name": "llama3.1:8b", "size": "4.7 GB"}, ...]
    Returns an empty list if Ollama is unreachable or has no models.
    """
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True, text=True, timeout=8
        )
        if result.returncode != 0:
            return []

        models = []
        lines = result.stdout.strip().splitlines()
        for line in lines[1:]:  # skip header
            parts = line.split()
            if not parts:
                continue
            name = parts[0]
            # Size is typically the 4th column (e.g. "4.7 GB")
            size = f"{parts[3]} {parts[4]}" if len(parts) >= 5 else ""
            models.append({"name": name, "size": size})
        return models

    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []


def _ask_folders() -> List[str]:
    """Interactively collect folder paths from the user."""
    console.print(
        "\n[bold]Step 1/3 — Your folders[/bold]",
    )
    console.rule(style="dim")
    console.print(
        "Which folder(s) do you want AiTao to search through your files?\n"
        "[dim](Press Enter to skip, type a path, or 'done' when finished)[/dim]"
    )

    folders: List[str] = []
    while True:
        try:
            value = console.input("  [cyan]>[/cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if value.lower() in ("done", ""):
            break

        path = Path(value).expanduser()
        if path.exists() and path.is_dir():
            folders.append(str(path))
            console.print(f"  [green]✓[/green] Added: {path}")
        else:
            console.print(
                f"  [yellow]![/yellow] Path not found: {value} "
                "(it will be saved anyway — you can fix it later)"
            )
            folders.append(str(Path(value).expanduser()))

    if folders:
        console.print(f"  [green]✓[/green] {len(folders)} folder(s) added.")
    else:
        console.print(
            "  [yellow]![/yellow] No folders added — "
            "edit [bold]config/config.toml[/bold] later to add paths."
        )
    return folders


def _ask_model(ollama_models: List[dict]) -> str:
    """Interactively pick or type a model name."""
    console.print(
        "\n[bold]Step 2/3 — Your AI model[/bold]",
    )
    console.rule(style="dim")

    if ollama_models:
        console.print("Models found in Ollama:\n")
        for i, m in enumerate(ollama_models, 1):
            size_str = f"[dim]({m['size']})[/dim]" if m["size"] else ""
            console.print(f"  [{i}] [cyan]{m['name']}[/cyan] {size_str}")
        console.print(
            "\nEnter a number, or type a model name directly "
            "[dim](press Enter to skip)[/dim]:"
        )
    else:
        console.print(
            "[yellow]Ollama is not running or has no models installed.[/yellow]\n"
            "Download a model first: [bold]ollama pull llama3.1:8b[/bold]\n"
            "Or type a model name to save it for later:"
        )

    try:
        value = console.input("  [cyan]>[/cyan] ").strip()
    except (EOFError, KeyboardInterrupt):
        value = ""

    if not value:
        console.print(
            "  [yellow]![/yellow] No model selected — "
            "edit [bold]config/config.toml[/bold] later."
        )
        return ""

    # If user typed a number, resolve it to a model name
    if value.isdigit() and ollama_models:
        idx = int(value) - 1
        if 0 <= idx < len(ollama_models):
            model_name = ollama_models[idx]["name"]
            console.print(f"  [green]✓[/green] Default model: [cyan]{model_name}[/cyan]")
            return model_name
        else:
            console.print("  [yellow]![/yellow] Invalid number — using input as model name.")

    console.print(f"  [green]✓[/green] Default model: [cyan]{value}[/cyan]")
    return value


def _ask_identity() -> str:
    """Interactively ask for the user's identity description."""
    console.print(
        "\n[bold]Step 3/3 — About you (optional)[/bold]",
    )
    console.rule(style="dim")
    console.print(
        "AiTao personalizes responses when it knows a little about you.\n"
        '[dim]Example: "Marie, French developer, 35 years old, lives in Lyon"[/dim]\n'
        "Press Enter to skip."
    )

    try:
        value = console.input("  [cyan]>[/cyan] ").strip()
    except (EOFError, KeyboardInterrupt):
        value = ""

    if value:
        console.print("  [green]✓[/green] Identity saved.")
    else:
        console.print(
            "  [dim]Skipped — edit [bold]config/config.toml[/bold] "
            "if you change your mind.[/dim]"
        )
    return value


def _write_config(folders: List[str], model: str, identity: str) -> Path:
    """
    Write a minimal config.toml from the wizard answers.

    Merges answers into the starter template structure.
    Returns the path of the written file.
    """
    _CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    # Build paths block
    if folders:
        paths_lines = "\n".join(f'    "{f}",' for f in folders)
        include_block = f"include_paths = [\n{paths_lines}\n]"
    else:
        include_block = (
            "include_paths = [\n"
            '    # "/Users/yourname/Documents",   # Add your folders here\n'
            "]"
        )

    # US-116: no fallback model name is written when the user skips this
    # step — a hardcoded name is not guaranteed installed (2026-07-20
    # incident). Leaving it commented triggers AiTao's auto-selection of the
    # smallest installed model that is safe for RAG chat.
    model_line = f'default_model = "{model}"' if model else (
        '# default_model = "your-model-name"   # Leave commented to auto-select a safe installed model'
    )

    identity_line = (
        f'who_are_you = "{identity}"'
        if identity
        else '# who_are_you = "Your name, country, age"'
    )

    content = f"""\
# ==============================================================================
# AiTao — Configuration (generated by ./aitao.sh init)
# Edit any value below, then run: ./aitao.sh start
#
# Need help?    docs/GETTING_STARTED.md
# Full options: config/config.toml.full
# ==============================================================================

# --- WHO ARE YOU? -------------------------------------------------------------
[identity]
{identity_line}


# --- WHICH FOLDERS TO INDEX? --------------------------------------------------
[indexing]
{include_block}


# --- WHICH AI MODEL TO USE? ---------------------------------------------------
# Run 'ollama list' to see available models. Use the exact NAME shown.
[llm]
{model_line}
"""

    _CONFIG_PATH.write_text(content, encoding="utf-8")
    return _CONFIG_PATH


# ──────────────────────────────────────────────────────────────────────────────

def run_wizard():
    """
    Entry point for the `./aitao.sh init` wizard.

    Called by cli.main.init() Typer command.
    """
    console.print(
        Panel(
            "[bold cyan]Welcome to AiTao![/bold cyan]\n\n"
            "Let's get you set up in 3 steps.\n"
            "[dim](You can skip any step and edit config/config.toml later)[/dim]",
            expand=False,
        )
    )

    # Check for existing config
    if _CONFIG_PATH.exists():
        console.print(
            f"\n[yellow]⚠[/yellow]  A configuration file already exists: "
            f"[bold]{_CONFIG_PATH}[/bold]"
        )
        try:
            overwrite = console.input("  Overwrite existing config? [y/[bold]N[/bold]] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            overwrite = "n"

        if overwrite != "y":
            console.print("[green]✓[/green] Existing config kept. No changes made.")
            raise typer.Exit(0)

    # Discover Ollama models upfront (used in step 2)
    ollama_models = _discover_ollama_models()

    # Run 3-step wizard
    folders = _ask_folders()
    model = _ask_model(ollama_models)
    identity = _ask_identity()

    # Write config
    config_path = _write_config(folders, model, identity)

    # Final summary
    console.print()
    console.rule(style="green")
    console.print(
        f"\n[green]✓[/green]  Configuration saved to [bold]{config_path}[/bold]\n\n"
        "Run [bold cyan]./aitao.sh start[/bold cyan] to launch AiTao!\n"
    )
    console.rule(style="green")
