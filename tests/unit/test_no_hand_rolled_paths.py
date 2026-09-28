# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Anti-regression guard (US-115): no hand-rolled Path(__file__).parent chains.

US-114 (packaging: src/X -> src/aitao/X) silently broke ~25 hand-counted
``Path(__file__).parent.parent...`` chains scattered across the codebase —
every one of them assumed a fixed folder depth, and the move shifted that
depth by one everywhere at once. AitaoPathManager (src/aitao/core/pathmanager.py,
exported as the ``path_manager`` singleton) already resolves the project root
robustly (AITAO_HOME env var, then marker-file search — never a fixed hop
count). This test makes "use path_manager.root instead of recomputing it"
a permanent, checked invariant so a future file move breaks at most the
small allowlist below, not two dozen unrelated modules.
"""

import re
from pathlib import Path

# Files allowed to compute their own project root from Path(__file__):
#   - core/lib/path_manager.py and core/config.py: the reference implementations
#     PathManager itself (and ConfigManager, for its own documented import-cycle
#     reasons) are built on.
#   - cli/main.py, cli/commands/models.py: legitimate standalone entry points
#     (``python -m aitao.cli`` / ``python -m aitao.cli.commands.models``,
#     each has its own ``if __name__ == "__main__":``) that can run BEFORE the
#     `aitao` package is guaranteed to be on sys.path, so they cannot import
#     aitao.core.pathmanager. They duplicate a small marker-based walk-up
#     instead of hand-counting hops — see the docstring in each file.
ALLOWLIST = {
    "core/lib/path_manager.py",
    "core/config.py",
    "cli/main.py",
    "cli/commands/models.py",
}

# Path(__file__) optionally .resolve()'d, followed by 2+ chained .parent hops.
# A single .parent (e.g. "Path(__file__).parent" for "this file's directory")
# is fine; it's the *chain* of 2+ that hand-counts folder depth and breaks on
# every file move.
_HAND_ROLLED_PATTERN = re.compile(r"Path\(__file__\)(\.resolve\(\))?(\.parent){2,}")


def _iter_src_files(src_root: Path):
    for py_file in sorted(src_root.rglob("*.py")):
        rel = py_file.relative_to(src_root).as_posix()
        if rel.startswith("tests/") or "/tests/" in f"/{rel}":
            continue
        yield py_file, rel


def test_no_hand_rolled_parent_chains_outside_allowlist():
    """Fail if a Path(__file__).parent.parent... chain appears outside the allowlist.

    Fix: import ``path_manager`` from ``aitao.core.pathmanager`` and use
    ``path_manager.root`` (or ``path_manager.get_src_dir()`` for the src/
    directory specifically) instead of recomputing the project root by
    counting folder hops.
    """
    import aitao

    src_root = Path(aitao.__file__).resolve().parent  # .../src/aitao

    violators = {}
    for py_file, rel in _iter_src_files(src_root):
        if rel in ALLOWLIST:
            continue
        text = py_file.read_text(encoding="utf-8")
        hits = []
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _HAND_ROLLED_PATTERN.search(line):
                hits.append(f"{rel}:{lineno}: {line.strip()}")
        if hits:
            violators[rel] = hits

    assert not violators, (
        "Hand-rolled Path(__file__).parent chains found outside the allowlist "
        "(US-115 — these silently broke on the last packaging move, see "
        "tests/unit/test_no_hand_rolled_paths.py docstring). Use "
        "`from aitao.core.pathmanager import path_manager` and "
        "`path_manager.root` (or `path_manager.get_src_dir()`) instead:\n  "
        + "\n  ".join(loc for hits in violators.values() for loc in hits)
    )


def test_allowlist_entries_still_exist():
    """Keep the allowlist honest: every entry must point at a real file.

    Prevents the allowlist from silently going stale (e.g. after a rename)
    and masking a file that should actually be checked.
    """
    import aitao

    src_root = Path(aitao.__file__).resolve().parent
    missing = [rel for rel in ALLOWLIST if not (src_root / rel).is_file()]
    assert not missing, f"Allowlist entries no longer exist on disk: {missing}"
