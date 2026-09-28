# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Configuration management commands.

Commands:
- aitao config show     Show current configuration
- aitao config validate Validate configuration file
- aitao config edit     Open config in editor
"""

import typer
from rich.syntax import Syntax

from aitao.cli.utils import console, success, error, warning, info, print_header
from aitao.core.pathmanager import path_manager

_PROJECT_ROOT = path_manager.root
_CONFIG_PATH = _PROJECT_ROOT / "config" / "config.toml"
_CONFIG_TEMPLATE = _PROJECT_ROOT / "config" / "config.toml.template"


app = typer.Typer(
    help=(
        "AiTao configuration management.\n\n"
        "[bold cyan]Examples[/bold cyan]\n\n"
        "  Show active (resolved) configuration:\n"
        "    [green]./aitao.sh config show[/green]\n\n"
        "  Validate configuration:\n"
        "    [green]./aitao.sh config validate[/green]\n\n"
        "  Show config file path:\n"
        "    [green]./aitao.sh config path[/green]\n\n"
        "[dim]Config file: config/config.toml[/dim]"
    ),
    rich_markup_mode="rich",
    no_args_is_help=True,
)


@app.command("show")
def show_config(
    section: str = typer.Argument(None, help="Config section to show (e.g., 'search')"),
):
    """Show current configuration."""
    try:
        from aitao.core.config import ConfigManager
        config = ConfigManager(str(_CONFIG_PATH))
        
        if section:
            data = config.get_section(section)
            if data:
                import json
                output = json.dumps(data, indent=2, ensure_ascii=False, default=str)
                syntax = Syntax(output, "json", theme="monokai")
                console.print(syntax)
            else:
                error(f"Section '{section}' not found")
        else:
            # Show full config
            if _CONFIG_PATH.exists():
                syntax = Syntax(_CONFIG_PATH.read_text(), "toml", theme="monokai")
                console.print(syntax)
                
    except FileNotFoundError:
        error("Config file not found: config/config.toml")
        info("Run: cp config/config.toml.template config/config.toml")
    except Exception as e:
        error(f"Error reading config: {e}")


@app.command("validate")
def validate_config():
    """Validate configuration file."""
    print_header("Configuration Validation")
    
    errors = []
    warnings = []
    
    try:
        from aitao.core.config import ConfigManager
        config = ConfigManager(str(_CONFIG_PATH))
        success("Config file parsed successfully")
        
        # Check required sections
        required_sections = ["paths", "search", "indexing"]
        for section in required_sections:
            if config.get_section(section):
                success(f"Section '{section}' present")
            else:
                errors.append(f"Missing required section: {section}")
        
        # Check paths — resolved via the single path authority (US-098 A4)
        from aitao.core.pathmanager import path_manager
        path = path_manager.get_storage_root()
        if path.exists():
            success(f"Storage root exists: {path}")
        else:
            warnings.append(f"Storage root does not exist: {path}")

        # Check Meilisearch URL
        ms_url = config.search.meilisearch.url
        if ms_url:
            success(f"Meilisearch URL: {ms_url}")
        else:
            warnings.append("search.meilisearch.url not configured")
        
        console.print()
        
        if errors:
            for err in errors:
                error(err)
        if warnings:
            for warn in warnings:
                warning(warn)
        
        if not errors:
            success("Configuration is valid!")
            raise typer.Exit(0)
        else:
            error(f"Found {len(errors)} error(s)")
            raise typer.Exit(1)
            
    except FileNotFoundError:
        error(f"Config file not found: {_CONFIG_PATH}")
        raise typer.Exit(1)


@app.command("edit")
def edit_config():
    """Open config file in default editor."""
    import subprocess
    import os
    
    if not _CONFIG_PATH.exists():
        error("Config file not found")
        if typer.confirm("Create from template?"):
            if _CONFIG_TEMPLATE.exists():
                import shutil
                shutil.copy(_CONFIG_TEMPLATE, _CONFIG_PATH)
                success(f"Created {_CONFIG_PATH} from template")
            else:
                error("Template not found")
                raise typer.Exit(1)
        else:
            raise typer.Exit(1)
    
    editor = os.environ.get("EDITOR", "nano")
    subprocess.run([editor, str(_CONFIG_PATH)])


@app.command("help")
def help_cmd(ctx: typer.Context) -> None:
    """Show this help message and exit."""
    typer.echo(ctx.parent.get_help())
