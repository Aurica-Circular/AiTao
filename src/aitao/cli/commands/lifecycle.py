# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Service lifecycle CLI commands (start/stop/restart/status).

Typer commands exposing the AiTao service lifecycle to the CLI.
Service management logic lives in _lifecycle_services.py and
infrastructure utilities in _lifecycle_utils.py.

Commands:
  aitao start   - Start ALL services + trigger initial scan
  aitao stop    - Stop ALL services gracefully
  aitao restart - Restart all services
  aitao status  - Show service status
"""

import time

import typer
from rich.live import Live
from rich.spinner import Spinner

from aitao.cli.utils import console, success, error, warning, info, status_line
from aitao.llm.provider import make_llm_client
from aitao.core.config import get_config
from aitao.core.logger import get_logger  # noqa: E402

# ── Internal imports from split modules ─────────────────────────────
from aitao.cli.commands._lifecycle_utils import (  # noqa: E402
    API_PID_FILE,
    WORKER_PID_FILE,
    _get_api_port,
    _is_meilisearch_responding,
    _check_meilisearch_running,
    _is_api_responding,
    _pids_on_port,
    _pid_alive,
    _read_pid_file,
    _run_command,
    _running_api_version,
)
from aitao.core.version import get_version  # noqa: E402
from aitao.cli.commands._lifecycle_services import (  # noqa: E402
    _start_api_server,
    _stop_api_server,
    _start_worker,
    _stop_worker,
    _run_initial_scan,
)

app = typer.Typer(help="Service lifecycle management")


@app.command()
def start(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    skip_scan: bool = typer.Option(False, "--skip-scan", help="Skip initial filesystem scan"),
    show_docs_link: bool = typer.Option(True, "--show-docs-link", hidden=True),
):
    """
    Start all AiTao services.

    Starts: Ollama check → Meilisearch → API server → Worker → initial scan.
    AiTao does not manage Ollama models — use `ollama pull <model>` directly.
    """
    info(f"🚀 Starting all AiTao services (version {get_version()})...")
    console.print()

    all_success = True
    port = _get_api_port()

    # 1. Check the configured LLM provider — PROVIDER-AGNOSTIC and NON-BLOCKING.
    # aitao.sh manages services, not the LLM. It reads `[llm] backend` from
    # config.toml and probes whatever provider is configured (Ollama, llama.cpp…).
    # A provider that is down must NEVER abort startup — the API surfaces LLM
    # errors at request time.
    console.print("[cyan]Step 1: Check LLM provider[/cyan]")
    try:
        cfg = get_config()
        backend = str((cfg.get_section("llm") or {}).get("backend") or "ollama").lower()
        provider = "Ollama" if backend in ("ollama", "auto", "mlx") else "llama.cpp / OpenAI-compatible"
        client = make_llm_client(cfg, get_logger("cli.lifecycle"))
        installed_models = client.list_models()
        available = [m.name for m in installed_models]
        status_line("LLM provider", f"OK — {provider} ({len(available)} model(s))", ok=True)

        # US-097 / US-116 — warn when the model that will ACTUALLY be used for
        # chat is risky for RAG (reasoning model = minutes of hidden thinking,
        # or a non-chat model). Advisory only: never blocks startup.
        #
        # `default_model` empty/not-installed is now a valid "auto" state
        # (US-116): the client resolves it at request time via
        # model_advisor.pick_safe_default. Evaluating the empty string here
        # would say nothing useful, so we resolve it the same way first and
        # advise on the model that will actually be used.
        from aitao.llm.model_advisor import chat_model_advisory, pick_safe_default
        default_model = str((cfg.get_section("llm") or {}).get("default_model") or "")
        if default_model and default_model in available:
            effective_model = default_model
        else:
            effective_model = pick_safe_default(installed_models) or ""
            if default_model:
                warning(
                    f"⚠️  Configured model '{default_model}' is not installed — "
                    f"AiTao will auto-select '{effective_model}' at runtime."
                )
            elif effective_model:
                info(f"🤖 No default model configured — auto-selecting '{effective_model}'.")
        advisory = chat_model_advisory(effective_model)
        if advisory:
            warning(f"⚠️  {advisory}")
    except Exception as e:
        # Non-fatal on purpose: never block startup on the LLM provider.
        warning(f"LLM provider not reachable yet ({type(e).__name__}) — services will start anyway.")
        console.print("  [dim]Check [llm] backend / server in config.toml; the API retries per request.[/dim]")
    
    console.print()
    console.print("[cyan]Step 2: Start services[/cyan]")
    
    # 1. Start Meilisearch — skip if already responding (custom plist or manual start)
    if _is_meilisearch_responding():
        status_line("Meilisearch", "OK (already running)", ok=True)
    elif not _run_command(
        "Meilisearch",
        ["brew", "services", "start", "meilisearch"],
        timeout=30
    ):
        all_success = False
    
    # 2. Start API server
    with Live(
        Spinner("dots", text="[cyan]API Server[/cyan]..."),
        console=console,
        transient=True,
    ):
        api_ok, api_pid = _start_api_server()
    
    if api_ok:
        status_line("API Server", f"OK (port {port}, PID {api_pid})", ok=True)
    else:
        status_line("API Server", "Failed to start", ok=False)
        all_success = False
    
    # 3. Start Worker daemon
    with Live(
        Spinner("dots", text="[cyan]Worker Daemon[/cyan]..."),
        console=console,
        transient=True,
    ):
        worker_ok, worker_pid = _start_worker()
    
    if worker_ok:
        status_line("Worker Daemon", f"OK (PID {worker_pid})", ok=True)
    else:
        status_line("Worker Daemon", "Failed to start", ok=False)
        all_success = False
    
    # 4. Run initial scan (unless skipped)
    if not skip_scan and all_success:
        console.print()
        info("📂 Running initial filesystem scan...")
        
        with Live(
            Spinner("dots", text="[cyan]Scanning documents[/cyan]..."),
            console=console,
            transient=True,
        ):
            new_count, modified_count = _run_initial_scan()
        
        total = new_count + modified_count
        if total > 0:
            status_line("Initial Scan", f"Found {new_count} new, {modified_count} modified files", ok=True)
            info(f"📋 {total} files added to queue for indexing")
        else:
            status_line("Initial Scan", "No new files found", ok=True)
    
    console.print()
    
    if all_success:
        success(f"✅ All services started successfully! (version {get_version()})")
        console.print()
        if show_docs_link:
            info("📖 API docs: http://localhost:{}/docs".format(port))
        info("🔍 Health:   http://localhost:{}/api/health".format(port))
        info("📊 Status:   ./aitao.sh status")
    else:
        warning("⚠️  Some services failed to start. Check above for details.")
        raise typer.Exit(1)


@app.command()
def stop(verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output")):
    """
    Stop all AiTao services.
    
    Gracefully stops:
    1. Background worker (finish current task)
    2. API server
    3. Meilisearch
    
    Services will be gracefully shut down with proper cleanup.
    """
    info("🛑 Stopping all AiTao services...")
    console.print()
    
    all_success = True

    # PIDs are read before stopping so the report can show WHICH process died —
    # afterwards the pid files are gone.
    worker_pid = _read_pid_file(WORKER_PID_FILE)
    api_pid = _read_pid_file(API_PID_FILE)
    worker_label = f"Worker Daemon (PID {worker_pid})" if worker_pid else "Worker Daemon"
    api_label = f"API Server (PID {api_pid})" if api_pid else "API Server"

    # 1. Stop Worker first (let it finish current task)
    with Live(
        Spinner("dots", text="[cyan]Worker Daemon[/cyan]..."),
        console=console,
        transient=True,
    ):
        worker_ok = _stop_worker()

    if worker_ok:
        status_line(worker_label, "Stopped", ok=True)
    else:
        status_line(worker_label, "Failed to stop", ok=False)
        all_success = False

    # 2. Stop API server
    with Live(
        Spinner("dots", text="[cyan]API Server[/cyan]..."),
        console=console,
        transient=True,
    ):
        api_ok = _stop_api_server()

    if api_ok:
        status_line(api_label, "Stopped", ok=True)
    else:
        status_line(api_label, "Failed to stop", ok=False)
        all_success = False
    
    # 3. Stop Meilisearch
    if not _run_command(
        "Meilisearch",
        ["brew", "services", "stop", "meilisearch"],
        timeout=30
    ):
        all_success = False
    
    console.print()
    
    if all_success:
        success("✅ All services stopped")
    else:
        warning("⚠️  Some services failed to stop. Check above for details.")
        raise typer.Exit(1)


@app.command()
def restart(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose output"),
    skip_scan: bool = typer.Option(False, "--skip-scan", help="Skip initial filesystem scan")
):
    """
    Restart all AiTao services.
    
    Equivalent to:
    1. Stop all services
    2. Wait for cleanup
    3. Start all services
    
    Useful after configuration changes.
    """
    info("🔄 Restarting all AiTao services...")

    # Show which version is being stopped (asked from the RUNNING server, not the
    # disk) vs. which will start — so an upgrade is visible in the transcript.
    running = _running_api_version(_get_api_port())
    if running:
        info(f"⏹  Stopping version:  {running}")
    else:
        info("⏹  Stopping version:  none detected (API not responding)")
    info(f"▶️  Starting version:  {get_version()}")
    console.print()

    # Stop services
    try:
        stop(verbose=verbose)
    except typer.Exit:
        pass  # Ignore exit code from stop
    except Exception as e:
        warning(f"Error stopping services: {e}")
    
    # Wait for services to fully stop
    console.print()
    info("⏳ Waiting for services to fully stop...")
    time.sleep(3)
    console.print()
    
    # Start services (no docs link on restart: it's an operational action,
    # the transcript should surface versions/PIDs, not documentation)
    try:
        start(verbose=verbose, skip_scan=skip_scan, show_docs_link=False)
    except Exception as e:
        error(f"Error restarting services: {e}")
        raise typer.Exit(1)


@app.command()
def status():
    """
    Show status of all AiTao services.
    
    Displays running state, PIDs, and ports for all managed services.
    """
    from rich.table import Table
    
    info("📊 AiTao Service Status")
    console.print()
    
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("Service", style="white")
    table.add_column("Status", style="white")
    table.add_column("Details", style="dim")
    
    # Check Meilisearch
    ms_running = _check_meilisearch_running()
    table.add_row(
        "Meilisearch",
        "[green]● Running[/green]" if ms_running else "[red]● Stopped[/red]",
        "brew services" if ms_running else ""
    )
    
    # Check API — port is the source of truth (catches orphans the PID file
    # would miss, and avoids false "Stopped" when the recorded PID is stale).
    port = _get_api_port()
    listening = _pids_on_port(port)
    api_running = bool(listening) or _is_api_responding(port)
    api_pid = listening[0] if listening else _read_pid_file(API_PID_FILE)
    table.add_row(
        "API Server",
        "[green]● Running[/green]" if api_running else "[red]● Stopped[/red]",
        f"PID {api_pid}, port {port}" if api_running else ""
    )

    # Check Worker (PID file now lives alongside the worker daemon's own file)
    worker_pid = _read_pid_file(WORKER_PID_FILE)
    worker_running = _pid_alive(worker_pid)
    
    table.add_row(
        "Worker Daemon",
        "[green]● Running[/green]" if worker_running else "[red]● Stopped[/red]",
        f"PID {worker_pid}" if worker_running else ""
    )
    
    console.print(table)
    console.print()
    
    # Summary
    all_running = ms_running and api_running and worker_running
    if all_running:
        success("✅ All services running")
    elif ms_running or api_running or worker_running:
        warning("⚠️  Some services not running")
    else:
        error("❌ All services stopped")


# Register in main app (will be done in main.py)
