# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
CLI: Model Status and Diagnostic Commands.

Provides diagnostic subcommands for models:
- status: List models available in Ollama + configured default
- check:  Check installed models for template issues (via script)
- fix:    Repair broken model templates (via script)
- validate: Test models with prompts (via script)
"""

import sys
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from aitao.core.logger import get_logger
from aitao.core.config import get_config
from aitao.core.pathmanager import path_manager
# Ollama-specific diagnostic — imports the concrete backend plugin on purpose.
from aitao.plugins.llm.ollama_client import OllamaClient
from aitao.llm.protocols import OllamaConnectionError

logger = get_logger(__name__)
console = Console()
err_console = Console(stderr=True)  # for error output


def status():
    """
    Show models available in Ollama and the configured default.

    AiTao delegates model management to Ollama — install models with:
      ollama pull <model-name>
    """
    console.print()

    try:
        cfg = get_config()
        default_model: str = cfg.get("llm.default_model") or cfg.get("llm", {}).get("default_model", "")
        client = OllamaClient(cfg)  # logger is optional, defaults internally
        models = client.list_models()
    except OllamaConnectionError as e:
        err_console.print(f"[red]ERROR: Cannot connect to Ollama — {e}[/red]")
        raise typer.Exit(code=1)
    except Exception as e:
        err_console.print(f"[red]ERROR: {e}[/red]")
        raise typer.Exit(code=1)

    if not models:
        console.print("[yellow]No models found in Ollama.[/yellow]")
        console.print("Install one with: [cyan]ollama pull llama3.1:8b[/cyan]")
        raise typer.Exit(code=0)

    table = Table(title="[green]Ollama Models[/green]", show_header=True)
    table.add_column("Model", style="cyan")
    table.add_column("Default", style="magenta")
    table.add_column("Size", style="yellow")
    table.add_column("Modified", style="dim")

    for m in sorted(models, key=lambda x: x.name):
        is_default = "✓" if m.name == default_model else ""
        size_gb = f"{m.size / 1e9:.1f} GB" if m.size else "?"
        modified = (m.modified_at or "")[:10]
        table.add_row(m.name, is_default, size_gb, modified)

    console.print(table)
    console.print()

    # US-116: an empty/unmatched default_model is a valid "auto" state, not a
    # misconfiguration — show what AiTao would actually pick at runtime
    # (smallest installed model that is safe for RAG chat, see model_advisor).
    from aitao.llm.model_advisor import pick_safe_default

    if default_model:
        default_present = any(m.name == default_model for m in models)
        if default_present:
            console.print(f"[green]✓ Default model:[/green] [cyan]{default_model}[/cyan]")
        else:
            fallback = pick_safe_default(models)
            console.print(
                f"[red]✗ Default model '{default_model}' not installed.[/red]\n"
                f"  Run: [cyan]ollama pull {default_model}[/cyan]\n"
                f"  [dim]Until then, AiTao auto-selects '{fallback}' at runtime.[/dim]"
            )
    else:
        fallback = pick_safe_default(models)
        console.print(
            f"[cyan]Default model: (auto)[/cyan] → would pick [cyan]'{fallback}'[/cyan]"
        )
        console.print(
            "  [dim]Set [cyan]llm.default_model[/cyan] in config.toml to force a specific model.[/dim]"
        )

    console.print()
    logger.info("Model status checked", metadata={"count": len(models), "default": default_model})
    raise typer.Exit(code=0)


def check():
    """Check all installed models for template issues."""
    import subprocess

    console.print()
    console.print("[cyan]🔍 Checking model templates...[/cyan]")
    console.print()

    script_path = (
        path_manager.root / "scripts" / "fix_ollama_templates.py"
    )
    if not script_path.exists():
        console.print(f"[red]ERROR: Script not found: {script_path}[/red]")
        raise typer.Exit(code=1)

    result = subprocess.run(
        [sys.executable, str(script_path), "--check"],
        capture_output=False,
    )
    raise typer.Exit(code=result.returncode)


def validate(
    model: Optional[str] = typer.Argument(
        None,
        help="Specific model to validate (optional, validates all if not specified)",
    ),
):
    """Validate models by testing them with a prompt."""
    import subprocess

    console.print()
    console.print("[cyan]🧪 Validating models...[/cyan]")
    console.print()

    script_path = (
        path_manager.root / "scripts" / "fix_ollama_templates.py"
    )
    if not script_path.exists():
        console.print(f"[red]ERROR: Script not found: {script_path}[/red]")
        raise typer.Exit(code=1)

    cmd = [sys.executable, str(script_path), "--validate"]
    if model:
        cmd.extend(["--model", model])

    result = subprocess.run(cmd, capture_output=False)
    raise typer.Exit(code=result.returncode)


def fix(
    model: Optional[str] = typer.Argument(
        None,
        help="Specific model to fix (optional, fixes all if not specified)",
    ),
    validate_after: bool = typer.Option(
        True,
        "--validate/--no-validate",
        help="Run validation test after fixing",
    ),
):
    """Fix broken model templates."""
    import subprocess as sp

    console.print()
    console.print("[cyan]🔧 Fixing model templates...[/cyan]")
    console.print()

    script_path = (
        path_manager.root / "scripts" / "fix_ollama_templates.py"
    )
    if not script_path.exists():
        console.print(f"[red]ERROR: Script not found: {script_path}[/red]")
        raise typer.Exit(code=1)

    cmd = [sys.executable, str(script_path), "--fix"]
    if model:
        cmd.extend(["--model", model])

    result = sp.run(cmd, capture_output=False)
    if result.returncode != 0:
        console.print()
        console.print("[red]❌ Some fixes failed[/red]")
        raise typer.Exit(code=1)

    if validate_after:
        console.print()
        console.print("[cyan]🧪 Running validation tests...[/cyan]")
        console.print()
        validate_cmd = [sys.executable, str(script_path), "--validate"]
        if model:
            validate_cmd.extend(["--model", model])
        validate_result = sp.run(validate_cmd, capture_output=False)
        if validate_result.returncode != 0:
            console.print()
            console.print("[yellow]⚠ Validation found issues[/yellow]")
            raise typer.Exit(code=1)

    console.print()
    console.print("[green]✓ Template fix complete![/green]")
    logger.info("Model templates fixed", metadata={"model": model or "all", "validated": validate_after})
