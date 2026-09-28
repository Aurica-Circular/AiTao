# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Boundary tests for the Config/PathManager responsibility split (US-098 A4).

Architecture decision (Phil, 2026-06-30) — the "two butlers":
  - ConfigManager owns SETTINGS: it is the single reader of the config files
    (config.toml + user.toml + APP__ env) and exposes the RAW values. It must not
    resolve filesystem paths and must never be asked for a resolved path.
  - PathManager is the SOLE path authority: it takes the raw path strings FROM
    ConfigManager, resolves them, creates the dirs, and answers "where is X".
    Anything needing a path asks PathManager — never ConfigManager.

These tests are written TEST-FIRST and encode the *boundary*, not "the two agree":
they are expected to be RED against the current code (split-brain) and to turn
GREEN once ConfigManager stops resolving paths and every path reader goes through
PathManager.

All assertions use pathlib / monkeypatch / temp configs — hermetic and portable.
"""

from pathlib import Path

import pytest

from aitao.core.config import ConfigManager
from aitao.core.pathmanager import AitaoPathManager


@pytest.fixture
def temp_project(tmp_path, monkeypatch):
    """A throwaway project root with markers + a config.toml referencing ${HOME}."""
    root = tmp_path / "proj"
    (root / "config").mkdir(parents=True)
    (root / "aitao.sh").touch()
    (root / "requirements.txt").touch()
    (root / "config" / "config.toml").write_text(
        '[paths]\n'
        'storage_root = "${HOME}/aitao_store"\n'
        'logs_dir = "${storage_root}/logs"\n'
        '\n'
        '[indexing]\n'
        'include_paths = []\n'
    )
    # PathManager resolves its root from AITAO_HOME (US-098 A1).
    monkeypatch.setenv("AITAO_HOME", str(root))
    return root


def _config(temp_project: Path) -> ConfigManager:
    return ConfigManager(config_path=str(temp_project / "config" / "config.toml"))


class TestConfigPathBoundary:
    """Encode the ConfigManager (settings) / PathManager (paths) responsibility split."""

    def test_env_override_reaches_the_path_authority(self, temp_project, monkeypatch):
        """An APP__ env override of storage_root must be reflected by PathManager.

        ConfigManager applies env layering; PathManager (the path authority) must
        source from it. Today PathManager reads the raw TOML and ignores the env →
        the override is seen by ConfigManager but not by PathManager = split-brain.
        """
        override = temp_project / "env_store"
        monkeypatch.setenv("APP__PATHS__STORAGE_ROOT", str(override))

        cfg = _config(temp_project)
        # ConfigManager sees the override (sanity — it owns the layering):
        assert str(override) in str(cfg.get("paths.storage_root"))

        # The single path authority must reflect it too:
        pm = AitaoPathManager()
        assert pm.get_storage_root().resolve() == override.resolve(), (
            "PathManager does not see the APP__PATHS__STORAGE_ROOT override — it reads "
            "the raw TOML instead of sourcing from ConfigManager (split-brain)."
        )

    def test_configmanager_does_not_resolve_paths(self, temp_project):
        """ConfigManager must return the RAW path string — resolving is PathManager's job."""
        cfg = _config(temp_project)
        raw = str(cfg.get("paths.storage_root"))
        assert "${HOME}" in raw or "$HOME" in raw, (
            "ConfigManager resolved ${HOME} itself — path resolution belongs to "
            "PathManager. ConfigManager should expose the raw setting value."
        )

    def test_pathmanager_is_the_only_config_path_reader(self):
        """Architecture guard: no module outside PathManager reads paths.* via ConfigManager.

        This makes Phil's rule a permanent, checked invariant: anything needing a
        filesystem path asks PathManager, not ConfigManager. The allowlist holds the
        legitimate exceptions (PathManager itself, ConfigManager internals, the key
        registry). Every other hit is a violator to redirect through PathManager.
        """
        import aitao.core as core

        src_root = Path(core.__file__).resolve().parent.parent  # .../src
        allowlist = {
            "core/pathmanager.py",        # the path authority (sources from ConfigManager)
            "core/lib/path_manager.py",   # generic base
            "core/config.py",             # ConfigManager internals (to be slimmed)
            "core/registry.py",           # defines the ConfigKeys constants
        }
        needles = (
            'get("paths.',
            "get('paths.",
            ".paths.storage_root",
            ".paths.logs_dir",
            ".paths.vector_db_dir",
            "ConfigKeys.STORAGE_ROOT",
            "ConfigKeys.LOGS_DIR",
        )

        violators = {}
        for py in src_root.rglob("*.py"):
            rel = py.relative_to(src_root).as_posix()
            if rel in allowlist:
                continue
            hits = []
            for n, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue
                if any(needle in line for needle in needles):
                    hits.append(f"{rel}:{n}")
            if hits:
                violators[rel] = hits

        assert not violators, (
            "These modules read a path setting via ConfigManager instead of asking "
            "PathManager (US-098 A4 boundary):\n  "
            + "\n  ".join(loc for hits in violators.values() for loc in hits)
        )

    def test_single_resolved_value_no_split(self, temp_project):
        """Without overrides, ConfigManager and PathManager must agree on the path (sanity)."""
        cfg = _config(temp_project)
        pm = AitaoPathManager()
        expected = (Path.home() / "aitao_store").resolve()
        # PathManager resolves the path...
        assert pm.get_storage_root().resolve() == expected
        # ...while ConfigManager still holds the raw, unresolved setting.
        assert cfg.get("paths.storage_root") == "${HOME}/aitao_store"
