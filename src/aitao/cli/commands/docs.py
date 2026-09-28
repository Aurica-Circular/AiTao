# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Command-reference generator (US-29).

Walks the live Typer/Click command tree and renders a Markdown reference, so
`docs/COMMANDS.md` can never drift from the actual CLI. Output is English
(project documentation convention) and is regenerated with `./aitao.sh docs`.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import click
import typer


def _first_line(text: Optional[str]) -> str:
    """First non-empty line of a help string (rich markup stripped lightly)."""
    if not text:
        return ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _is_argument(param) -> bool:
    # Use the version-stable Click attribute, not isinstance: Typer wraps
    # params in TyperArgument/TyperOption whose subclassing of click.Argument/
    # click.Option varies across Typer/Click versions (US-29 CI fix).
    return getattr(param, "param_type_name", "") == "argument"


def _is_option(param) -> bool:
    return (
        getattr(param, "param_type_name", "") == "option"
        and not getattr(param, "hidden", False)
        and param.name != "help"
    )


def _usage(invocation: str, cmd: click.Command) -> str:
    """A one-line usage string: `./aitao.sh <path> <args> [options]`."""
    parts = [f"./aitao.sh {invocation}"]
    for param in cmd.params:
        if _is_argument(param):
            parts.append(f"<{param.name}>" if param.required else f"[{param.name}]")
    if any(_is_option(p) for p in cmd.params):
        parts.append("[options]")
    return " ".join(parts)


def _param_lines(cmd: click.Command) -> List[str]:
    """Bullet lines documenting a command's arguments and options."""
    lines: List[str] = []
    for param in cmd.params:
        if _is_argument(param):
            suffix = "" if param.required else " *(optional)*"
            lines.append(f"- `{param.name}` — argument{suffix}")
    for param in cmd.params:
        if _is_option(param):
            flags = ", ".join(f"`{opt}`" for opt in param.opts)
            help_txt = _first_line(param.help)
            lines.append(f"- {flags}{' — ' + help_txt if help_txt else ''}")
    return lines


def _command_section(invocation: str, cmd: click.Command, heading: str) -> List[str]:
    out = [f"{heading} `{invocation}`", ""]
    desc = _first_line(cmd.help)
    if desc:
        out += [desc, ""]
    out += ["```", _usage(invocation, cmd), "```", ""]
    params = _param_lines(cmd)
    if params:
        out += params + [""]
    return out


def generate_commands_markdown(app: typer.Typer) -> str:
    """Render the full command reference as Markdown from a Typer app."""
    cli = typer.main.get_command(app)
    commands = getattr(cli, "commands", {})
    groups = {n: c for n, c in commands.items() if getattr(c, "commands", None)}
    leaves = {n: c for n, c in commands.items() if not getattr(c, "commands", None)}

    lines: List[str] = [
        "# AiTao — Command Reference",
        "",
        "> Auto-generated from the CLI by `./aitao.sh docs`. Do not edit by hand.",
        "",
        "All commands are invoked through `./aitao.sh <command>`. "
        "Append `--help` (or `help`) to any command for its built-in help.",
        "",
        "## Contents",
        "",
    ]
    if leaves:
        lines.append("- [Top-level commands](#top-level-commands)")
    for name in sorted(groups):
        desc = _first_line(groups[name].help)
        lines.append(f"- [`{name}`](#{name})" + (f" — {desc}" if desc else ""))
    lines.append("")

    if leaves:
        lines += ["## Top-level commands", ""]
        for name in sorted(leaves):
            lines += _command_section(name, leaves[name], "###")

    for name in sorted(groups):
        group = groups[name]
        lines += [f"## `{name}`", ""]
        desc = _first_line(group.help)
        if desc:
            lines += [desc, ""]
        for sub in sorted(group.commands):
            lines += _command_section(f"{name} {sub}", group.commands[sub], "###")

    return "\n".join(lines).rstrip() + "\n"


def write_commands_doc(app: typer.Typer, output: Path) -> Path:
    """Generate and write the command reference to ``output``."""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(generate_commands_markdown(app), encoding="utf-8")
    return output
