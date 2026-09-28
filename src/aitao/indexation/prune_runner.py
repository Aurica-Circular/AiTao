# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Headless orchestration for out-of-scope document pruning (US-126-C).

`aitao index prune` (US-126-A) exposed the out-of-scope detection
(`indexation.prune.find_out_of_scope_paths`) as an interactive CLI command
with a `rich` progress bar and a confirmation prompt. This module wraps the
same pure logic in a plain, logger-driven function with no `rich`/console
dependency, so the worker's periodic scan loop (US-126-C) can call it
directly and unattended, behind an opt-in config flag.

CRITICAL SAFETY RULE (inherited from prune.py, do not weaken): scope is
decided ONLY from the caller-supplied `configured_roots` (expected to come
from ``path_manager.get_include_paths(existing_only=False)``), NEVER from
disk presence. A demounted-but-still-configured volume must never be treated
as out of scope. This module does not itself decide the roots — it trusts
the caller — but it inherits the empty-roots guardrail from `prune.py`.
"""

from typing import Any, List, Optional, Tuple

from aitao.indexation.prune import find_out_of_scope_paths


def run_out_of_scope_prune(
    meili: Any,
    configured_roots: List[str],
    logger: Optional[Any] = None,
) -> Tuple[int, int]:
    """Delete indexed documents whose path is no longer under any configured root.

    Never raises: every failure mode (empty config, listing error, per-path
    delete error) degrades to "do nothing" or "skip that one path" rather
    than propagating, because this is meant to run unattended inside the
    worker's periodic scan loop, where an exception here must not stop
    scanning/indexing.

    Args:
        meili: A Meilisearch client exposing `get_all_document_paths()` and
            `delete_by_path(path)` (see `storage.repository.make_meilisearch_client`).
        configured_roots: The full configured include_paths allow-list,
            resolved but NOT filtered by disk existence (``existing_only=False``).
            An empty list means "refuse to prune" (see `find_out_of_scope_paths`).
        logger: Optional logger (``core.logger.get_logger`` style, with
            ``.info``/``.warning``). When None, this function stays silent.

    Returns:
        (deleted_count, failed_count). Both are 0 when there was nothing to
        do, the config was empty, or listing the index failed.
    """
    if not configured_roots:
        if logger:
            logger.info(
                "Auto-prune skipped: [indexing].include_paths is empty "
                "(refusing to treat an empty config as \"everything is orphaned\")."
            )
        return 0, 0

    try:
        indexed_paths = meili.get_all_document_paths()
    except Exception as exc:
        if logger:
            logger.warning(f"Auto-prune skipped: could not list indexed documents: {exc}")
        return 0, 0

    orphans = find_out_of_scope_paths(indexed_paths, configured_roots)
    if not orphans:
        if logger:
            logger.info("Auto-prune: no out-of-scope documents found.")
        return 0, 0

    deleted = failed = 0
    for path in orphans:
        try:
            if meili.delete_by_path(path):
                deleted += 1
            else:
                failed += 1
        except Exception:
            failed += 1

    if logger:
        sample = orphans[:5]
        logger.info(
            "Auto-prune: removed out-of-scope documents",
            metadata={
                "deleted": deleted,
                "failed": failed,
                "total_orphans": len(orphans),
                "sample": sample,
            },
        )

    return deleted, failed
