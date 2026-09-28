# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# cli/commands/mcp.py — CLI commands for the AiTao MCP Server (US-055)
#
# Responsibilities:
#   - Provide `aitao mcp serve` command (stdio / SSE / HTTP transports)
#   - Provide `aitao mcp status` command (is the SSE server running?)
#   - Provide `aitao mcp config` command (show Claude Desktop JSON snippet)
#   - Provide `aitao mcp stop` command (stop daemon SSE/HTTP server)

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import typer

from aitao.cli.utils import console

app = typer.Typer(
    name="mcp",
    help="Model Context Protocol (MCP) server — expose AiTao tools to AI assistants.",
    no_args_is_help=True,
)

_DEFAULT_PORT = 8201
_DEFAULT_HOST = "127.0.0.1"
import tempfile  # noqa: E402

_MCP_PID_FILE = Path(tempfile.gettempdir()) / "aitao_mcp.pid"
# Console capture for the MCP server process — in the configured logs dir
# (storage_root/logs), never the project tree (US-098 A5).
from aitao.core.pathmanager import path_manager  # noqa: E402
_MCP_LOG_FILE = path_manager.get_logs_dir() / "mcp.console.log"


# ---------------------------------------------------------------------------
# mcp serve
# ---------------------------------------------------------------------------

@app.command("serve")
def serve(
    transport: str = typer.Option(
        "stdio",
        "--transport", "-t",
        help="Transport protocol: stdio | sse | http",
    ),
    host: str = typer.Option(
        _DEFAULT_HOST,
        "--host", "-H",
        help="Bind address (SSE / HTTP only).",
    ),
    port: int = typer.Option(
        _DEFAULT_PORT,
        "--port", "-p",
        help="Port to listen on (SSE / HTTP only).",
    ),
    daemon: bool = typer.Option(
        False,
        "--daemon", "-d",
        help="Run in background (SSE / HTTP only). Returns the terminal immediately.",
    ),
) -> None:
    """Start the AiTao MCP server.

    \b
    Transports:
      stdio   — for Claude Desktop, VS Code Copilot, Windsurf, Cursor (foreground)
      sse     — HTTP Server-Sent Events (legacy MCP clients)
      http    — Streamable HTTP (MCP 1.0 standard)

    \b
    Examples:
      aitao mcp serve                           # stdio, foreground
      aitao mcp serve --transport sse           # SSE foreground on 127.0.0.1:8201
      aitao mcp serve --transport sse --daemon  # SSE background
      aitao mcp serve --transport http -d       # HTTP background
    """
    valid = {"stdio", "sse", "http"}
    if transport not in valid:
        console.print(f"[red]Unknown transport '{transport}'. Valid: {', '.join(sorted(valid))}[/red]")
        raise typer.Exit(1)

    if daemon and transport == "stdio":
        console.print("[yellow]--daemon is not supported for stdio transport (stdio must be foreground).[/yellow]")
        raise typer.Exit(1)

    # --- Daemon mode: spawn a detached subprocess and return ---
    if daemon:
        _start_daemon(transport=transport, host=host, port=port, src_path=path_manager.get_src_dir())
        return

    # --- Foreground mode ---
    _run_foreground(transport=transport, host=host, port=port)


def _run_foreground(transport: str, host: str, port: int) -> None:
    """Run the MCP server in the current process (blocking)."""
    try:
        if transport == "stdio":
            print("Starting MCP server (stdio)…", file=sys.stderr)
            from aitao.mcp_server.server import run_stdio  # type: ignore
            run_stdio()
        elif transport == "sse":
            console.print(f"[cyan]Starting MCP server (SSE) on {host}:{port}…[/cyan]")
            from aitao.mcp_server.server import run_sse  # type: ignore
            run_sse(host=host, port=port)
        else:
            console.print(f"[cyan]Starting MCP server (HTTP) on {host}:{port}…[/cyan]")
            from aitao.mcp_server.server import run_http  # type: ignore
            run_http(host=host, port=port)
    except KeyboardInterrupt:
        # After stdio/SSE shutdown, stdout may be closed — always use stderr
        print("\nMCP server stopped.", file=sys.stderr)
    except Exception as exc:
        print(f"MCP server error: {exc}", file=sys.stderr)
        raise typer.Exit(1) from exc


def _start_daemon(transport: str, host: str, port: int, src_path: Path) -> None:
    """Spawn a detached MCP server process and write its PID file."""
    # Build the same command that was invoked, without --daemon
    cmd = [
        sys.executable, "-m", "aitao.cli.main",
        "mcp", "serve",
        "--transport", transport,
        "--host", host,
        "--port", str(port),
    ]

    _MCP_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(_MCP_LOG_FILE, "a")  # noqa: WPS515

    process = subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=log_file,
        cwd=str(src_path),
        start_new_session=True,
        env=os.environ.copy(),
    )
    log_file.close()

    _MCP_PID_FILE.write_text(str(process.pid))
    time.sleep(1)

    if process.poll() is None:
        console.print(
            f"[green]MCP server started[/green] (PID {process.pid}, "
            f"{transport} on {host}:{port})\n"
            f"  Logs : {_MCP_LOG_FILE}\n"
            f"  Stop : [bold]aitao mcp stop[/bold]"
        )
    else:
        _MCP_PID_FILE.unlink(missing_ok=True)
        console.print(f"[red]MCP server failed to start. Check logs: {_MCP_LOG_FILE}[/red]")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# mcp stop
# ---------------------------------------------------------------------------

@app.command("stop")
def stop() -> None:
    """Stop the background MCP server (SSE / HTTP daemon)."""
    if not _MCP_PID_FILE.exists():
        console.print("[yellow]MCP server is not running (no PID file found).[/yellow]")
        return

    try:
        pid = int(_MCP_PID_FILE.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        _MCP_PID_FILE.unlink(missing_ok=True)
        console.print(f"[green]MCP server stopped[/green] (PID {pid})")
    except ProcessLookupError:
        _MCP_PID_FILE.unlink(missing_ok=True)
        console.print("[yellow]MCP server was not running (stale PID file removed).[/yellow]")
    except Exception as exc:
        console.print(f"[red]Error stopping MCP server: {exc}[/red]")


# ---------------------------------------------------------------------------
# mcp status
# ---------------------------------------------------------------------------

@app.command("status")
def status(
    host: str = typer.Option(_DEFAULT_HOST, "--host", "-H", help="SSE server host to probe."),
    port: int = typer.Option(_DEFAULT_PORT, "--port", "-p", help="SSE server port to probe."),
) -> None:
    """Check if the AiTao MCP server (SSE/HTTP) is running."""
    import urllib.request
    import urllib.error

    # PID file check
    pid_info = ""
    if _MCP_PID_FILE.exists():
        try:
            pid = int(_MCP_PID_FILE.read_text().strip())
            os.kill(pid, 0)  # signal 0 = check existence
            pid_info = f" (PID {pid})"
        except (ProcessLookupError, ValueError):
            _MCP_PID_FILE.unlink(missing_ok=True)

    url = f"http://{host}:{port}/"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:  # noqa: S310 — local loopback
            console.print(f"[green]MCP server is UP[/green]{pid_info} — {url} (HTTP {resp.status})")
    except urllib.error.URLError:
        if pid_info:
            console.print(
                f"[cyan]MCP server process running{pid_info}[/cyan] but not responding on {host}:{port}\n"
                "  (May be stdio mode — no HTTP endpoint)"
            )
        else:
            console.print(
                f"[yellow]MCP server is NOT running[/yellow] on {host}:{port}\n"
                "  Start with: [bold]aitao mcp serve --transport sse --daemon[/bold]"
            )
    except Exception as exc:
        console.print(f"[red]Error probing MCP server: {exc}[/red]")


# ---------------------------------------------------------------------------
# mcp config
# ---------------------------------------------------------------------------

@app.command("config")
def config_snippet(
    transport: str = typer.Option("stdio", "--transport", "-t", help="Transport: stdio | sse"),
    port: int = typer.Option(_DEFAULT_PORT, "--port", "-p", help="Port (SSE only)."),
) -> None:
    """Print a Claude Desktop or VS Code MCP configuration snippet."""
    aitao_bin = _find_aitao_bin()

    if transport == "stdio":
        snippet = (
            '{\n'
            '  "mcpServers": {\n'
            '    "aitao": {\n'
            '      "command": "' + aitao_bin + '",\n'
            '      "args": ["mcp", "serve", "--transport", "stdio"]\n'
            '    }\n'
            '  }\n'
            '}'
        )
        console.print("\n[bold]Claude Desktop / VS Code Copilot (stdio):[/bold]")
    else:
        snippet = (
            '{\n'
            '  "mcpServers": {\n'
            '    "aitao": {\n'
            f'      "url": "http://127.0.0.1:{port}/sse"\n'
            '    }\n'
            '  }\n'
            '}'
        )
        console.print(f"\n[bold]SSE client config (port {port}):[/bold]")

    console.print(snippet)
    console.print()


def _find_aitao_bin() -> str:
    """Return the best available aitao executable path."""
    # Prefer the shell wrapper for full env setup
    aitao_sh = path_manager.root / "aitao.sh"
    if aitao_sh.exists():
        return str(aitao_sh)
    # Fallback: installed CLI entry-point
    return "aitao"


@app.command("help")
def help_cmd(ctx: typer.Context) -> None:
    """Show this help message and exit."""
    typer.echo(ctx.parent.get_help())
