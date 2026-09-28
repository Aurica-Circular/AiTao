# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
AiTao — src/cli/commands/license.py

CLI commands for AiTao license management (end-user side).

Commands:
    ./aitao.sh license activate <KEY|FILE>  Install a license key or file
    ./aitao.sh license status               Show current license info
    ./aitao.sh license deactivate           Remove the installed key
"""

import typer
from pathlib import Path
from rich.panel import Panel
from rich.table import Table
from rich import box

from aitao.cli.utils import console

app = typer.Typer(
    name="license",
    help="Manage your AiTao Premium license.",
    no_args_is_help=True,
)


@app.command()
def activate(
    key_or_file: str = typer.Argument(..., help="License key (AITAO-xxx.yyy) or path to a .key file"),
):
    """Activate a Premium license.

    Examples:
        ./aitao.sh license activate AITAO-eyJ...xxx.sig
        ./aitao.sh license activate ~/Downloads/aitao-license.key
    """
    from aitao.core.license import LicenseManager

    # If it looks like a file path, read the key from it
    candidate = Path(key_or_file).expanduser()
    if candidate.exists() and candidate.is_file():
        key = candidate.read_text().strip()
        if not key:
            console.print("[red]\u2717 File is empty.[/red]")
            raise typer.Exit(1)
    else:
        key = key_or_file

    lm = LicenseManager()
    if not key.startswith("AITAO-"):
        console.print("[red]\u2717 Invalid format.[/red] The key must start with [bold]AITAO-[/bold]")
        raise typer.Exit(1)

    try:
        ok = lm.activate(key)
    except RuntimeError as exc:
        console.print(Panel(
            f"[bold red]\u2717 {exc}[/bold red]",
            title="AiTao Premium",
            border_style="red",
        ))
        raise typer.Exit(1)

    if ok:
        info = lm.get_info()
        console.print(Panel(
            f"[bold green]\u2713 Premium license activated[/bold green]\n\n"
            f"  Tier       : [cyan]{info.get('tier', '?')}[/cyan]\n"
            f"  Valid until: [cyan]{info.get('exp', '?')}[/cyan]\n"
            f"  Label      : {info.get('label', '?')}",
            title="AiTao Premium",
            border_style="green",
        ))
    else:
        console.print(Panel(
            "[bold red]\u2717 Invalid or expired key.[/bold red]\n\n"
            "Check that the key is complete and not expired.\n"
            "Contact [cyan]support@auricacircular.com[/cyan] for a new key.",
            title="Activation error",
            border_style="red",
        ))
        raise typer.Exit(1)


@app.command()
def status():
    """Show the status of the installed license.

    One clear panel per state: the Premium module not installed at all, the
    module installed but no key, an active key (label, end date, days left),
    an expired key (a warm, non-guilt-inducing message), a revoked key
    (US-141 — calm, no accusation), or an invalid key (bad signature or
    malformed).
    """
    from aitao.core.license import LicenseManager

    lm = LicenseManager()
    info = lm.get_info()

    if not info.get("premium_module_installed", True):
        console.print(Panel(
            "[yellow]The AiTao Premium module is not installed.[/yellow]\n\n"
            "Edition: [bold]Core[/bold] (free edition)\n\n"
            "Premium adds advanced document formats (Office, e-books) and OCR of scans and images.\n"
            "See [cyan]https://auricacircular.com[/cyan] to obtain it.",
            title="AiTao — License",
            border_style="yellow",
        ))
        return

    state = info.get("status")

    if state == "active":
        table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
        table.add_column("Field", style="bold")
        table.add_column("Value")
        table.add_row("Edition", "[green]Premium[/green]")
        table.add_row("Label", info.get("label", "?"))
        table.add_row("Valid until", info.get("exp", "?"))
        table.add_row("Days left", str(info.get("days_left", "?")))
        console.print(Panel(table, title="AiTao — License", border_style="green"))
        return

    if state == "expired":
        console.print(Panel(
            f"[bold yellow]Your AiTao Premium key ({info.get('label', '?')}) ended on "
            f"{info.get('exp', '?')}.[/bold yellow]\n\n"
            "Thank you for testing AiTao! Your documents and your index are untouched\n"
            "and all Core features keep working.\n\n"
            "To continue with Premium: [cyan]https://auricacircular.com[/cyan] — "
            "[cyan]support@auricacircular.com[/cyan]",
            title="AiTao — License",
            border_style="yellow",
        ))
        return

    if state == "revoked":
        console.print(Panel(
            "[bold red]This AiTao Premium key has been deactivated.[/bold red]\n\n"
            "If you think this is a mistake, please contact "
            "[cyan]support@auricacircular.com[/cyan] — your documents and all "
            "Core features are unaffected.\n\n"
            "Edition: [bold]Core[/bold] (free edition)",
            title="AiTao — License",
            border_style="red",
        ))
        return

    if state == "invalid":
        console.print(Panel(
            "[red]The installed license key is invalid[/red] (bad signature or malformed).\n\n"
            "Edition: [bold]Core[/bold] (free edition)\n\n"
            "Activate a valid key:\n"
            "  [green]aitao license activate <key-file>[/green]\n\n"
            "See [cyan]https://auricacircular.com[/cyan] for a license.",
            title="AiTao — License",
            border_style="red",
        ))
        return

    # state == "none"
    console.print(Panel(
        "[yellow]No license installed.[/yellow]\n\n"
        "Edition: [bold]Core[/bold] (free edition)\n\n"
        "To activate Premium:\n"
        "  [green]aitao license activate <key-file>[/green]",
        title="AiTao — License",
        border_style="yellow",
    ))


@app.command()
def deactivate():
    """Remove the installed license (reverts to Core edition)."""
    from aitao.core.license import LicenseManager

    lm = LicenseManager()
    if lm.get_info().get("status", "none") == "none":
        console.print("[yellow]No license to remove.[/yellow]")
        return

    confirm = typer.confirm("Remove the Premium license? (reverts to Core edition)")
    if confirm:
        lm.deactivate()
        console.print("[green]\u2713 License removed.[/green] Core edition is now active.")
    else:
        console.print("Cancelled.")


@app.command("help")
def help_cmd(ctx: typer.Context) -> None:
    """Show this help message and exit."""
    typer.echo(ctx.parent.get_help())
