# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Dashboard panel renderers — Rich panels for the AiTao dashboard.

Each _section_*() function builds a Rich Panel for one dashboard area:
services, models, index, worker/scan, errors, and MCP server status.
Extracted from dashboard.py to keep both files under 400 lines.
"""

import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Optional

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from aitao.cli.commands.dashboard_eta import build_eta_snapshot


def _http_get(url: str, timeout: float = 3.0):
    """Minimal HTTP GET — returns (status_code, json_body) or raises."""
    import requests
    resp = requests.get(url, timeout=timeout)
    try:
        return resp.status_code, resp.json()
    except Exception:
        return resp.status_code, {}


def _fmt_count(n: int | None, singular: str = "doc", plural: str | None = None) -> str:
    """Format a count with its unit, e.g. '1 482 docs'."""
    if n is None:
        return "[dim]–[/dim]"
    unit = (plural or (singular + "s")) if n != 1 else singular
    return f"[bold]{n:,}[/bold] {unit}"


def _service_row(name: str, url: str, alive: bool, extra: str = "") -> Text:
    """Build a single colored service line."""
    icon = "[bold green]✓[/bold green]" if alive else "[bold red]✗[/bold red]"
    color = "green" if alive else "red"
    line = Text.assemble(
        Text.from_markup(icon + " "),
        Text(f"{name:<14}", style="bold"),
        Text(f"{url:<32}", style=color),
    )
    if extra:
        line.append(f"  {extra}", style="dim")
    return line


def section_services(config, port_checker) -> Panel:
    """Panel: Services status (core + optional interfaces)."""
    api_port   = config.api.port
    ms_url     = config.search.meilisearch.url
    ollama_url = config.llm.ollama_url
    ms_port    = int(ms_url.split(":")[-1]) if ":" in ms_url else 7700
    ol_port    = int(ollama_url.split(":")[-1]) if ":" in ollama_url else 11434

    core = [
        ("AiTao API",   f"http://localhost:{api_port}", port_checker("localhost", api_port)),
        ("Meilisearch", ms_url,                          port_checker("localhost", ms_port)),
        ("Ollama",      ollama_url,                      port_checker("localhost", ol_port)),
    ]

    optional_ports = [
        ("OpenWebUI",  3000, "Interface de chat"),
        ("OnlyOffice", 8080, "Interface de chat"),
    ]
    optional_active = [
        (name, f"http://localhost:{port}", True, note)
        for name, port, note in optional_ports
        if port_checker("localhost", port)
    ]

    table = Table(box=None, show_header=False, padding=(0, 0))
    for name, url, alive in core:
        table.add_row(_service_row(name, url, alive))
    if optional_active:
        table.add_row(Text("  Interfaces optionnelles", style="dim italic"))
        for name, url, alive, note in optional_active:
            table.add_row(_service_row(name, url, alive, extra=f"({note})"))

    return Panel(table, title="[bold cyan]■ Services[/bold cyan]", border_style="cyan", expand=True)


def section_models(ollama_url: str) -> Panel:
    """Panel: AI models installed in Ollama + in-memory indicator."""
    rows = []
    try:
        status_tags, data_tags = _http_get(f"{ollama_url}/api/tags", timeout=2.0)
        installed = data_tags.get("models", []) if status_tags == 200 else []

        status_ps, data_ps = _http_get(f"{ollama_url}/api/ps", timeout=2.0)
        loaded_names = {
            m.get("name", "") for m in (data_ps.get("models", []) if status_ps == 200 else [])
        }

        if installed:
            for m in installed:
                name    = m.get("name", "?")
                size_mb = round(m.get("size", 0) / 1024 / 1024)
                if name in loaded_names:
                    indicator = Text.from_markup("  [bold green]● active[/bold green] ")
                else:
                    indicator = Text.from_markup("  [dim]○ ready  [/dim]")
                rows.append(
                    Text.assemble(
                        indicator,
                        Text(f"{name:<32}", style="cyan"),
                        Text(f"{size_mb:>6} MB", style="dim"),
                    )
                )
        else:
            rows.append(Text("  Ollama not responding or no model installed", style="dim"))
    except Exception:
        rows.append(Text("  Ollama not reachable", style="red dim"))

    table = Table(box=None, show_header=False, padding=(0, 0))
    for r in rows:
        table.add_row(r)

    return Panel(table, title="[bold cyan]\u25a0 Ollama Models[/bold cyan]", border_style="cyan", expand=True)


def section_index(config, ms_url: str, is_worker_running: bool = False) -> Panel:
    """Panel: Index statistics (Meilisearch documents + excerpts) and configured sources.

    \u00c9PIC-31 (US-113): fusion is the only search engine since v4.0 (decision
    D1) \u2014 the second row is always the Meilisearch excerpt/chunk index
    (MeiliChunkStore's backing index); LanceDB is removed from the live path
    entirely.
    """
    ms_docs    = None
    ms_version = "?"

    # --- Meilisearch stats ---
    try:
        from aitao.storage.repository import make_meilisearch_client
        from aitao.core.registry import StatsKeys
        client = make_meilisearch_client()
        if client.is_healthy():
            ms_version = client.get_version()
            stats      = client.get_stats()
            ms_docs    = stats.get(StatsKeys.TOTAL_DOCUMENTS, 0)
    except Exception:
        pass

    second_row_label = "Meilisearch (extraits)"
    second_row_count_kwargs = {"singular": "extrait", "plural": "extraits"}

    # --- Meilisearch excerpt/chunk index stats (the fusion retrieval backend).
    # RAW meilisearch.Client on purpose: MeiliChunkStore's constructor loads
    # the bge-m3 model — far too heavy for a dashboard counter.
    second_docs = None
    try:
        import meilisearch
        ms_cfg = config.search.meilisearch
        raw = meilisearch.Client(ms_cfg.url, ms_cfg.api_key or None)
        stat = raw.index(ms_cfg.chunks_index).get_stats()
        second_docs = getattr(stat, "number_of_documents", None)
        if second_docs is None and isinstance(stat, dict):
            second_docs = stat.get("numberOfDocuments", 0)
        second_docs = int(second_docs or 0)
    except Exception:
        pass

    from aitao.core.pathmanager import path_manager  # resolver (US-098 A4); list from config
    paths = path_manager.resolve_include_paths(config.indexing.include_paths or [], existing_only=False)

    table = Table(box=None, show_header=False, padding=(0, 0))
    table.add_row(
        Text("Meilisearch", style="bold"),
        Text.from_markup(f"  {_fmt_count(ms_docs)}  [dim]({ms_version})[/dim]"),
        Text("  = raw text indexed, keyword search", style="dim"),
    )
    table.add_row(
        Text(second_row_label, style="bold"),
        Text.from_markup(f"  {_fmt_count(second_docs, **second_row_count_kwargs)}"),
        Text("  = semantic representation, understands meaning", style="dim"),
    )

    # ETA estimation from source inventory + recent worker throughput.
    try:
        eta = build_eta_snapshot(ms_client=client if ms_docs is not None else None)

        table.add_row(Text(""), Text(""), Text(""))
        table.add_row(
            Text("ETA", style="bold"),
            Text.from_markup(
                f"  [bold]{eta['eta_human']}[/bold]"
                f"  [dim]({eta['confidence']} confidence)[/dim]"
            ),
            Text(
                "  = estimate from source files + observed worker speed",
                style="dim",
            ),
        )
        table.add_row(
            Text(""),
            Text.from_markup(
                f"  [cyan]{eta['indexed_in_source']:,}[/cyan]/"
                f"[bold]{eta['source_eligible']:,}[/bold] eligible indexed"
                f"  [dim]({eta['remaining_in_source']:,} remaining)[/dim]"
            ),
            Text(""),
        )
        table.add_row(
            Text(""),
            Text.from_markup(
                f"  [yellow]{eta['source_unsupported']:,}[/yellow] unsupported format(s) excluded"
            ),
            Text(""),
        )
        table.add_row(
            Text(""),
            Text.from_markup(
                f"  Throughput: [green]{eta['rate_docs_per_min']:.2f}[/green] doc/min"
                f"  [dim]({eta['recent_completed']} completed in {eta['window_minutes']} min)[/dim]"
            ),
            Text(""),
        )
    except Exception:
        pass

    if paths:
        table.add_row(Text(""), Text(""), Text(""))
        table.add_row(
            Text("Sources", style="bold"),
            Text(f"  {len(paths)} configured folder(s)", style="cyan"),
            Text(""),
        )
        for p in paths[:6]:
            table.add_row(Text(""), Text(f"  \u2022 {p}", style="dim"), Text(""))
        if len(paths) > 6:
            table.add_row(Text(""), Text(f"  \u2026 +{len(paths)-6} more", style="dim italic"), Text(""))

    return Panel(table, title="[bold cyan]■ Index[/bold cyan]", border_style="cyan", expand=True)


def section_worker(config, worker=None) -> Panel:
    """Panel: Worker status and queue breakdown."""
    try:
        if worker is None:
            from aitao.indexation.worker import BackgroundWorker
            from aitao.cli.utils import get_config_path
            worker = BackgroundWorker(config_path=get_config_path())
        is_running  = worker.is_running()
        pid         = worker.get_pid()
        queue_stats = worker.queue.get_stats()

        pending    = queue_stats.get("pending", 0)
        processing = queue_stats.get("processing", 0)
        completed  = queue_stats.get("completed", 0)
        failed     = queue_stats.get("failed", 0)

        if is_running and processing > 0:
            w_text = Text.assemble(
                Text("● Worker ", style="bold"),
                Text("processing", style="bold green"),
                Text(f"  (PID {pid})", style="dim"),
            )
        elif is_running:
            w_text = Text.assemble(
                Text("\u25cf Worker ", style="bold"),
                Text("idle", style="bold yellow"),
                Text(f"  (PID {pid}, waiting for new files)", style="dim"),
            )
        else:
            w_text = Text.assemble(
                Text("\u25cf Worker ", style="bold"),
                Text("stopped", style="bold red"),
                Text("  \u2192 ./aitao.sh start", style="dim"),
            )

        table = Table(box=None, show_header=False, padding=(0, 0))
        table.add_row(w_text)
        table.add_row(Text(""))
        table.add_row(Text.from_markup(f"  Pending           [yellow]{pending:>6,}[/yellow]"))
        table.add_row(Text.from_markup(f"  Processing        [cyan]{processing:>6,}[/cyan]"))
        table.add_row(Text.from_markup(
            f"  Completed         [green]{completed:>6,}[/green]"
            f"  [dim](\u2260 indexed docs \u2014 some files may have no content)[/dim]"
        ))
        table.add_row(Text.from_markup(f"  Failed            [red]{failed:>6,}[/red]"))
        if failed > 0:
            table.add_row(Text(
                "  \u2192 ./aitao.sh queue retry  then  ./aitao.sh start",
                style="dim italic"
            ))

    except Exception as e:
        table = Table(box=None, show_header=False, padding=(0, 0))
        table.add_row(Text(f"Error: {e}", style="red dim"))

    return Panel(table, title="[bold cyan]■ Worker / Scan[/bold cyan]", border_style="cyan", expand=True)


def section_errors(config, worker=None) -> Optional[Panel]:
    """Panel: Recent failed tasks split by error type."""
    try:
        if worker is None:
            from aitao.indexation.worker import BackgroundWorker
            from aitao.cli.utils import get_config_path
            worker = BackgroundWorker(config_path=get_config_path())
        tasks = worker.queue.list_tasks(status="failed", limit=100)

        if not tasks:
            return None

        # Build the authoritative set of supported extensions dynamically so the
        # dashboard reflects reality (e.g. images/PDFs supported via OCR pipeline).
        try:
            from aitao.indexation.text_extractor import TextExtractor
            _supported_exts = TextExtractor().get_supported_extensions()
        except Exception:
            _supported_exts = set()

        # Extensions that are truly not indexable by any code path.
        _truly_unsupported = {".epub", ".mobi", ".azw", ".cbz", ".cbr"}

        format_counts: Counter = Counter()   # truly unsupported formats
        process_errors: dict   = {}          # supported format that failed to process
        content_errors: list   = []          # corrupted / password-protected / unreadable

        for task in tasks:
            err = (task.error_message or "").lower()
            ext = Path(task.file_path).suffix.lower()

            if ext in _truly_unsupported:
                # Not handled by any extractor — skip silently.
                format_counts[ext or "(no extension)"] += 1
            elif ext not in _supported_exts:
                # Extension not recognised by TextExtractor at all.
                format_counts[ext or "(no extension)"] += 1
            else:
                # Supported format that failed during processing — distinguish
                # "extraction/OCR failure" from generic content errors.
                if any(kw in err for kw in ("unsupported file type", "no extractor", "no handler")):
                    # Extractor explicitly rejected this file (e.g. old heuristic leftover).
                    format_counts[ext or "(no extension)"] += 1
                else:
                    # The format is supported but the file could not be processed
                    # (OCR unavailable, corrupted content, password-protected, etc.)
                    process_errors.setdefault(ext, []).append(task.file_path)

        table = Table(box=None, show_header=False, padding=(0, 0))

        if format_counts:
            total_fmt = sum(format_counts.values())
            table.add_row(Text.from_markup(
                f"[yellow]■ Unsupported format[/yellow]  [bold]{total_fmt}[/bold] file(s)"
            ))
            for ext, cnt in format_counts.most_common(8):
                table.add_row(Text(f"    {ext}  ({cnt})", style="dim"))
            table.add_row(Text(
                "  → No action required. These formats are not handled by any extractor.",
                style="dim italic"
            ))

        if process_errors:
            if format_counts:
                table.add_row(Text(""))
            total_proc = sum(len(v) for v in process_errors.values())
            ext_summary = ", ".join(
                f"{ext} ({len(v)})" for ext, v in
                sorted(process_errors.items(), key=lambda x: -len(x[1]))[:4]
            )
            table.add_row(Text.from_markup(
                f"[orange3]■ Processing error[/orange3]  [bold]{total_proc}[/bold] file(s)"
                f"  [dim]({ext_summary})[/dim]"
            ))
            all_proc_files = [fp for files in process_errors.values() for fp in files]
            for fp in all_proc_files[:5]:
                p = Path(fp)
                display = str(p) if len(str(p)) < 60 else f"…/{p.parent.name}/{p.name}"
                table.add_row(Text(f"    {display}", style="dim"))
            if len(all_proc_files) > 5:
                table.add_row(Text(f"    … and {len(all_proc_files)-5} more", style="dim italic"))
            table.add_row(Text(
                "  → Possible causes: OCR not available (Premium), password-protected, or corrupted.",
                style="dim italic"
            ))

        if content_errors:
            if format_counts or process_errors:
                table.add_row(Text(""))
            table.add_row(Text.from_markup(
                f"[red]■ Unreadable file[/red]  [bold]{len(content_errors)}[/bold] file(s)"
                f"  [dim](corrupted, password-protected, or empty)[/dim]"
            ))
            for fp in content_errors[:5]:
                p = Path(fp)
                display = str(p) if len(str(p)) < 60 else f"…/{p.parent.name}/{p.name}"
                table.add_row(Text(f"    {display}", style="dim"))
            if len(content_errors) > 5:
                table.add_row(Text(f"    … and {len(content_errors)-5} more", style="dim italic"))
            table.add_row(Text(
                "  → Open these files to check if they are corrupted or password-protected.",
                style="dim italic"
            ))

        return Panel(table, title="[bold red]■ Recent errors[/bold red]", border_style="red", expand=True)

    except Exception:
        return None


def section_mcp(port_checker) -> Panel:
    """Panel: MCP Server status (stdio always ready, SSE/HTTP when daemon running)."""
    MCP_PID_FILE = Path(tempfile.gettempdir()) / "aitao_mcp.pid"
    MCP_PORT     = 8201

    pid       = None
    pid_alive = False
    if MCP_PID_FILE.exists():
        try:
            pid = int(MCP_PID_FILE.read_text().strip())
            os.kill(pid, 0)
            pid_alive = True
        except (ProcessLookupError, ValueError, OSError):
            MCP_PID_FILE.unlink(missing_ok=True)

    sse_alive = port_checker("localhost", MCP_PORT, timeout=0.8)

    table = Table(box=None, show_header=False, padding=(0, 0))

    table.add_row(_service_row(
        "stdio",
        "Claude Desktop / VS Code Copilot",
        alive=True,
        extra="always ready",
    ))

    if sse_alive:
        pid_txt = f"PID {pid}" if pid_alive else "running"
        table.add_row(_service_row(
            "SSE / HTTP",
            f"http://localhost:{MCP_PORT}",
            alive=True,
            extra=pid_txt,
        ))
    else:
        table.add_row(Text.from_markup(
            "[dim]  SSE / HTTP    not running   "
            "(./aitao.sh mcp serve --transport sse --daemon)[/dim]"
        ))

    table.add_row(Text(""))
    free_tools    = "aitao_search · aitao_ingest · aitao_stats"
    premium_tools = "aitao_ocr · aitao_extract"
    table.add_row(Text.from_markup(
        f"  [green]Free[/green]     {free_tools}"
    ))
    table.add_row(Text.from_markup(
        f"  [yellow]Premium[/yellow]  {premium_tools}"
    ))

    table.add_row(Text(""))
    table.add_row(Text.from_markup(
        "  Config: [bold]./aitao.sh mcp config[/bold]  "
        "[dim]— prints Claude Desktop / VS Code JSON snippet[/dim]"
    ))

    return Panel(
        table,
        title="[bold cyan]■ MCP Server[/bold cyan]",
        border_style="cyan",
        expand=True,
    )
