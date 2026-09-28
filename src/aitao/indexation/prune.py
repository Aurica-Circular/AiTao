# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Out-of-scope document detection for `aitao index prune` (US-126-A).

When a folder is removed (or commented out) from `[indexing].include_paths`,
its documents stay indexed forever and pollute search results. This module
provides the *pure* rule that decides whether an indexed path is still inside
the configured perimeter — no Meilisearch access, no disk access — so it is
cheap and safe to unit-test exhaustively.

CRITICAL SAFETY RULE (do not weaken): scope membership is decided ONLY from
the configured `include_paths` (``existing_only=False``), NEVER from whether
a root currently exists on disk. A root that is still listed in the config
but momentarily unmounted (e.g. an external volume) must keep its documents
"in scope" — otherwise unplugging a drive would make every document under it
look orphaned and trigger a catastrophic bulk delete on the next prune.
"""

import unicodedata
from pathlib import Path
from typing import Dict, List


def _normalize(path_str: str) -> Path:
    """Normalize a path string to NFC and resolve it to a comparable Path.

    NFC normalization matters because filenames coming from different
    sources (e.g. OCR'd Chinese filenames, macOS filesystem events) can use
    NFC or NFD Unicode forms for the same visible characters; comparing raw
    strings would then wrongly treat identical paths as different.

    ``resolve()`` is used (not just expanduser) so that redundant separators,
    ``..`` segments, and symlink-free normalization line up between indexed
    paths and configured roots. It does NOT require the path to exist.
    """
    nfc = unicodedata.normalize("NFC", path_str)
    return Path(nfc).expanduser().resolve()


def _is_within(path: Path, root: Path) -> bool:
    """Return True if `path` is `root` itself or a descendant of `root`."""
    if hasattr(path, "is_relative_to"):
        return path == root or path.is_relative_to(root)
    # Fallback for Python < 3.11.
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return path == root


def find_out_of_scope_paths(
    indexed_paths: List[str], configured_roots: List[str]
) -> List[str]:
    """Return the subset of `indexed_paths` that fall under NO `configured_roots`.

    Pure function — no I/O. Safety rules:
    - `configured_roots` is expected to be the FULL configured allow-list
      (``existing_only=False``); this function does not know or care whether
      a root currently exists on disk, by design.
    - If `configured_roots` is empty, returns an empty list (no orphans).
      An empty include_paths must never be interpreted as "everything is
      orphaned" — the caller decides what an empty config means, this
      function simply refuses to flag anything as out of scope without at
      least one real root to compare against.

    Args:
        indexed_paths: Paths currently stored in the search index.
        configured_roots: Raw configured include_paths roots (resolved,
            possibly non-existent on disk).

    Returns:
        The indexed paths (original strings, unmodified) that are not under
        any configured root.
    """
    if not configured_roots:
        return []

    normalized_roots = [_normalize(r) for r in configured_roots]

    orphans: List[str] = []
    for raw in indexed_paths:
        candidate = _normalize(raw)
        if not any(_is_within(candidate, root) for root in normalized_roots):
            orphans.append(raw)
    return orphans


def group_by_root(orphan_paths: List[str], configured_roots: List[str]) -> Dict[str, List[str]]:
    """Group out-of-scope paths for display, bucketed by their nearest ancestor.

    Since orphan paths are by definition NOT under any configured root, the
    "nearest ancestor" here is a best-effort display grouping: the parent
    directory of each orphan path. Roots that no orphan matches are omitted.
    Purely cosmetic (console output) — not used for the safety decision.

    Args:
        orphan_paths: Paths already identified as out of scope.
        configured_roots: Unused for grouping logic itself, kept for a
            stable signature / future refinement; grouping is by parent dir.

    Returns:
        Dict mapping a display group key (parent directory string) to the
        list of orphan paths under it, sorted by group key.
    """
    del configured_roots  # not needed for parent-dir grouping, kept for API stability
    groups: Dict[str, List[str]] = {}
    for raw in orphan_paths:
        try:
            parent = str(_normalize(raw).parent)
        except Exception:
            parent = "?"
        groups.setdefault(parent, []).append(raw)

    return dict(sorted(groups.items(), key=lambda kv: kv[0]))
