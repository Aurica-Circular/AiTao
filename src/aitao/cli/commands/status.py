# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Status command - Show AiTao system status.

Displays the health of all components:
- Configuration
- Meilisearch server (documents + excerpts/chunks)
- Python environment
- API server
"""

import socket
import tempfile

from aitao.cli.utils import (
    console, print_header, info,
    status_line, get_config_path, spinner
)


def show_status():
    """Display comprehensive system status."""
    print_header("AiTao Status", "System health check")
    console.print()
    
    # Version info
    _show_version_info()
    console.print()
    
    # Configuration status
    _show_config_status()
    console.print()

    # API status
    _show_api_status()
    console.print()
    
    # Meilisearch status
    _show_meilisearch_status()
    console.print()

    # Ollama status
    _show_ollama_status()
    console.print()
    
    # Python environment
    _show_python_info()

    # MCP server
    console.print()
    _show_mcp_status()


def _show_mcp_status():
    """Show MCP server status."""
    console.print("[bold]MCP Server[/bold]")
    import os
    from pathlib import Path

    MCP_PID_FILE = Path(tempfile.gettempdir()) / "aitao_mcp.pid"
    MCP_PORT     = 8201

    # stdio — always available
    status_line("stdio", "Ready (Claude Desktop, VS Code Copilot, Cursor, Windsurf)")

    # SSE/HTTP daemon check
    pid_alive = False
    pid       = None
    if MCP_PID_FILE.exists():
        try:
            pid = int(MCP_PID_FILE.read_text().strip())
            os.kill(pid, 0)
            pid_alive = True
        except (ProcessLookupError, ValueError, OSError):
            MCP_PID_FILE.unlink(missing_ok=True)

    try:
        import socket
        with socket.create_connection(("localhost", MCP_PORT), timeout=1.0):
            sse_alive = True
    except OSError:
        sse_alive = False

    if sse_alive:
        status_line("SSE / HTTP", f"Running on port {MCP_PORT}" + (f" (PID {pid})" if pid_alive else ""))
    else:
        status_line("SSE / HTTP", "Not running", ok=False)
        info("  Start with: ./aitao.sh mcp serve --transport sse --daemon")

    info("  Config:     ./aitao.sh mcp config")
def _show_version_info():
    """Show version information."""
    console.print("[bold]Version[/bold]")
    try:
        from aitao.core.version import get_version, get_version_info
        ver = get_version()
        info_dict = get_version_info()
        status_line("Version", ver)
        status_line("Python", info_dict.get("python_version", "unknown"))
    except Exception as e:
        status_line("Version", f"Error: {e}", ok=False)


def _show_config_status():
    """Show configuration status."""
    console.print("[bold]Configuration[/bold]")
    try:
        from aitao.core.config import ConfigManager
        config_path = get_config_path()
        ConfigManager(str(config_path))  # validates the config parses
        status_line("Config file", str(config_path.relative_to(config_path.parent.parent)))
        
        # Resolved via the single path authority (US-098 A4)
        from aitao.core.pathmanager import path_manager
        storage_path = path_manager.get_storage_root()
        status_line("Storage root", str(storage_path))

        # Quick check if storage exists
        if storage_path.exists():
            status_line("Storage exists", "Yes")
        else:
            status_line("Storage exists", "No (will be created)", ok=False)
            
    except FileNotFoundError:
        status_line("Config file", "Not found", ok=False)
    except Exception as e:
        status_line("Config", f"Error: {e}", ok=False)


def _show_meilisearch_status():
    """Show Meilisearch status.

    ÉPIC-31 (US-113): also shows the excerpt (chunks) index — the real
    chat/RAG retrieval backend since v4.0 (fusion-only, decision D1). Never
    crashes if that index/store is unreachable.
    """
    console.print("[bold]Meilisearch[/bold]")
    try:
        from aitao.storage.repository import make_meilisearch_client

        # Try to connect
        with spinner("Connecting to Meilisearch..."):
            client = make_meilisearch_client()

        if client.is_healthy():
            status_line("Server", "Running")
            status_line("Version", client.get_version())
            status_line("URL", client.host)
            status_line("Index (documents)", client.index_name)
        else:
            status_line("Server", "Not responding", ok=False)

    except Exception as e:
        status_line("Server", f"Not available: {e}", ok=False)
        info("  Start with: brew services start meilisearch")
        return

    try:
        from aitao.core.config import get_config
        cfg = get_config()
        # Name comes straight from config — no store construction (the
        # MeiliChunkStore constructor loads the bge-m3 model, far too heavy
        # for a status line).
        status_line("Index (excerpts)", cfg.search.meilisearch.chunks_index)
    except Exception as e:
        status_line("Index (excerpts)", f"Not available: {e}", ok=False)


def _show_api_status():
    """Show API status."""
    console.print("[bold]API Server[/bold]")
    try:
        import requests
        from aitao.core.config import get_config

        config = get_config()
        port = config.api.port
        base_url = f"http://localhost:{port}"

        def _is_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
            """Check if a local TCP port is open."""
            try:
                with socket.create_connection((host, port), timeout=timeout):
                    return True
            except OSError:
                return False

        if not _is_port_open("localhost", port):
            status_line("Status", "Not running", ok=False)
            status_line("URL", base_url, ok=False)
            info("  Start with: ./aitao.sh api start")
            return

        try:
            response = requests.get(f"{base_url}/api/health", timeout=(1, 5))
            if response.status_code == 200:
                status_line("Status", "Running")
                status_line("URL", base_url)
            else:
                status_line("Status", f"Unhealthy ({response.status_code})", ok=False)
                status_line("URL", base_url, ok=False)
        except requests.ReadTimeout:
            status_line("Status", "Running (slow health check)")
            status_line("URL", base_url)
        except requests.RequestException as e:
            status_line("Status", "Running (health check failed)", ok=False)
            status_line("URL", base_url, ok=False)
            info(f"  Health error: {e}")
    except Exception as e:
        status_line("Status", f"Error: {e}", ok=False)
        info("  Start with: ./aitao.sh api start")


def _show_ollama_status():
    """Show the configured LLM provider — provider-agnostic.

    Reads `[llm] backend` and probes whatever provider is configured (Ollama,
    or an OpenAI-compatible server such as llama.cpp / LM Studio / vLLM). A TCP
    ping alone is misleading, so we run a minimal generation as the real health
    check and surface the provider's own remediation hint when it fails.
    """
    console.print("[bold]LLM Provider[/bold]")
    try:
        from aitao.core.config import get_config
        from aitao.llm.provider import make_llm_client

        config = get_config()
        llm_cfg = config.get_section("llm") or {}
        backend = str(llm_cfg.get("backend") or "ollama").lower()

        if backend in ("ollama", "auto", "mlx"):
            name = "Ollama"
            url = llm_cfg.get("ollama_url", "http://localhost:11434")
        else:
            name = "llama.cpp / OpenAI-compatible"
            url = (llm_cfg.get("openai", {}) or {}).get("base_url", "")

        status_line("Provider", name)
        status_line("URL", url)

        with spinner(f"Checking {name} (inference probe)..."):
            health = make_llm_client(config, None).health()

        if not health.reachable:
            status_line("Status", "Not responding", ok=False)
            if health.hint:
                info(f"  {health.hint}")
        elif health.inference_ok:
            label = "Responding" + (f" (v{health.version})" if health.version else "")
            status_line("Status", label)
        else:
            status_line("Status", "Reachable but inference FAILED", ok=False)
            if health.error:
                info(f"  Reason: {health.error}")
            if health.hint:
                info(f"  → {health.hint}")
    except Exception as e:
        status_line("Status", f"Error: {e}", ok=False)


def _show_python_info():
    """Show Python environment info."""
    console.print("[bold]Python Environment[/bold]")
    import sys
    status_line("Python", sys.version.split()[0])
    status_line("Executable", sys.executable)
    
    # Check key packages
    packages = ["typer", "meilisearch", "lancedb", "sentence_transformers"]
    for pkg in packages:
        try:
            module = __import__(pkg.replace("-", "_"))
            ver = getattr(module, "__version__", "installed")
            status_line(pkg, ver)
        except ImportError:
            status_line(pkg, "Not installed", ok=False)
