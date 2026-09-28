# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# migrate.py — CLI wrapper for the ÉPIC-31 (US-112) v4 migration.
#
# Thin layer over search.migrate_v4.MigrationV4Runner (the tested, store-
# agnostic core): resolves the production defaults from config, prints the
# report, and gates --swap/--rollback behind an interactive confirmation
# (or --yes). No migration LOGIC lives here — see search/migrate_v4.py and
# search/migrate_v4_source.py.
#
# ABSOLUTE SAFETY NOTE for whoever runs this: --dry-run (the default) is the
# ONLY phase with zero effect on the live indices — it builds "<name>_next"
# alongside them and reports, nothing more. --swap performs the actual cutover
# (after confirmation) and enqueues the CJK-affected documents for a full
# reindex. --rollback re-swaps (undoes a previous --swap). ~/.aitao/data/lancedb
# is never deleted by any of this — see docs/MIGRATION-4.0.md.

from __future__ import annotations

import json
from typing import Optional

import typer

from aitao.cli.utils import confirm, console, error, info, print_header, status_line, success, warning

# NOTE: registered as a single TOP-LEVEL command (``aitao migrate-v4``, not a
# sub-app) — see cli/main.py: ``app.command("migrate-v4")(migrate_cmd.migrate_v4)``,
# the same pattern used for ``status``/``dashboard``/``init``.


def _print_report(report) -> None:
    console.print()
    console.print(f"[bold]Mode:[/bold] {report.mode}")
    # ÉPIC-31 (US-113): fusion is the only engine since v4.0 — there is no
    # more "[search] engine" flag to have flipped before/after running this,
    # so `engine_mode_at_run` is always "fusion" (kept on the report for
    # backward-compat visibility only, see search/migrate_v4.py).
    status_line("Engine active at run time", report.engine_mode_at_run, ok=True)
    console.print()
    console.print("[bold]Documents[/bold]")
    status_line("Total (live index)", str(report.docs_total))
    status_line("Copied (vector reused, zero re-embed)", str(report.docs_copied))
    status_line("  ...without a source vector (pre-existing gap)", str(report.docs_without_vector))
    status_line("Needing reindex (CJK gluing changed content)", str(report.docs_needing_reindex))
    status_line("  ...requeued for reindex", str(report.docs_requeued))
    status_line("  ...source file missing (report only)", str(report.docs_source_missing), ok=False)
    console.print()
    console.print("[bold]Excerpts (chunks)[/bold]")
    status_line("Total (source LanceDB)", str(report.chunks_total_source))
    status_line("Copied (vector reused, zero re-embed)", str(report.chunks_copied))
    status_line(
        "Excluded (belong to a reindex-needed document)",
        str(report.chunks_excluded_pending_reindex),
    )
    console.print()
    console.print("[bold]Sample verification[/bold]")
    for c in report.sample_checks:
        ok = c.present and c.has_vector and c.search_found
        status_line(
            f"{c.doc_id[:12]}… ({c.path})",
            f"present={c.present} vector={c.has_vector} search_found={c.search_found}",
            ok=ok,
        )
    if not report.sample_checks:
        info("(no sample — no vector-bearing document in the 'copy' population)")
    console.print()
    status_line("Next index (documents)", report.next_docs_index)
    status_line("Next index (excerpts)", report.next_chunks_index)
    status_line("Swapped onto live names", str(report.swapped))
    status_line("Elapsed", f"{report.elapsed_s}s")
    if report.source_missing_paths:
        console.print()
        warning(f"{len(report.source_missing_paths)} document(s) need reindex but their source file is gone:")
        for p in report.source_missing_paths[:10]:
            console.print(f"  [dim]• {p}[/dim]")


def migrate_v4(
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run", help="Safe default: build '_next', report, never swap."),
    swap: bool = typer.Option(False, "--swap", help="Build '_next' fresh, swap onto the live names, requeue CJK reindex."),
    rollback: bool = typer.Option(False, "--rollback", help="Undo a previous --swap (re-swap, no rebuild)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the interactive confirmation for --swap/--rollback."),
    json_report: Optional[str] = typer.Option(None, "--json", help="Write the report as JSON to this path."),
):
    """Migrate LanceDB vectors + Meilisearch documents into the v4 fusion indices.

    Phases (mutually exclusive; --dry-run is the default and the ONLY one
    with zero effect on the live indices):
      ./aitao.sh migrate-v4                  dry-run (default)
      ./aitao.sh migrate-v4 --swap [--yes]   commit
      ./aitao.sh migrate-v4 --rollback [--yes]  undo a previous --swap
    """
    from aitao.search.migrate_v4 import MigrationConfirmationRequired, MigrationV4Runner

    print_header("AiTao v4 migration (ÉPIC-31, US-112)")

    if rollback and swap:
        error("--swap and --rollback are mutually exclusive.")
        raise typer.Exit(1)

    try:
        runner = MigrationV4Runner()
    except Exception as e:
        error(f"Failed to initialize the migration runner: {e}")
        raise typer.Exit(1)

    if rollback:
        if not yes and not confirm(
            f"Re-swap {runner.docs_index_name} <-> {runner.docs_next_name} and "
            f"{runner.chunks_index_name} <-> {runner.chunks_next_name}? This undoes the last --swap."
        ):
            info("Cancelled.")
            raise typer.Exit(0)
        try:
            report = runner.rollback(confirmed=True)
        except MigrationConfirmationRequired as e:
            error(str(e))
            raise typer.Exit(1)
        success("Rollback complete — the live indices now serve the pre-migration content again.")
        _print_report(report)
    elif swap:
        info("Building '_next' indices (fresh read of the live stores)...")
        preview = runner.dry_run()
        _print_report(preview)
        console.print()
        if not yes and not confirm(
            "Proceed with the swap? The live indices will be replaced immediately."
        ):
            info("Cancelled — nothing was swapped.")
            raise typer.Exit(0)
        try:
            report = runner.swap(confirmed=True)
        except MigrationConfirmationRequired as e:
            error(str(e))
            raise typer.Exit(1)
        success("Swap complete.")
        if report.docs_requeued:
            info(
                f"{report.docs_requeued} document(s) requeued for a full reindex "
                "(CJK gluing changed their content) — the worker will pick them up."
            )
        _print_report(report)
    else:
        report = runner.dry_run()
        info("Dry-run only — no live index was touched.")
        _print_report(report)

    if json_report:
        with open(json_report, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, ensure_ascii=False, indent=2, default=str)
        success(f"Report written to {json_report}")
