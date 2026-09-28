# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Failure-mode regression tests for the PathManager (US-098).

Unlike test_pathmanager.py (which always chdir's into a valid project root and
only characterises the happy path), these tests exercise the *broken* contexts
that let real anomalies live for months:

  - launching AiTao from a foreign working directory (A1) must NOT scatter a
    `data/` skeleton wherever the process happens to run (A3);
  - the project root must be derived from the code location, never from the CWD,
    so it is stable no matter where you start the process (A1);
  - `${HOME}` substitution must work cross-platform, including on Windows where
    `HOME` is usually unset and `USERPROFILE` holds the home directory.

These are written TEST-FIRST: they are expected to be RED against the current
implementation and to turn GREEN once US-098 anchors root resolution on the code
location (+ optional AITAO_HOME override) instead of the CWD.

All assertions use pathlib / monkeypatch only — no Unix-only tooling — so the
suite runs identically on macOS, Linux and Windows.
"""

import pytest

from aitao.core.pathmanager import AitaoPathManager


@pytest.fixture
def foreign_cwd(tmp_path, monkeypatch):
    """A directory with no project markers and no config, used as the CWD."""
    foreign = tmp_path / "some" / "unrelated" / "place"
    foreign.mkdir(parents=True)
    monkeypatch.chdir(foreign)
    return foreign


class TestPathManagerRobustness:
    """Regression guards for deterministic, CWD-independent path resolution."""

    def test_foreign_cwd_creates_no_stray_data_dir(self, foreign_cwd, tmp_path, monkeypatch):
        """Running from an unrelated directory must not create a `data/` skeleton there.

        This is the exact anomaly that produced `docs-aitao/data`: the process
        ran with a CWD that had no markers, so storage_root fell back to
        `<cwd>/data` and a skeleton was silently created in the working dir.
        """
        home = tmp_path / "aitao_home"
        home.mkdir()
        monkeypatch.setenv("AITAO_HOME", str(home))

        AitaoPathManager()

        assert not (foreign_cwd / "data").exists(), (
            "A `data/` skeleton was created in the foreign CWD — path resolution "
            "still depends on os.getcwd() instead of the code location / AITAO_HOME."
        )

    def test_root_is_independent_of_cwd(self, tmp_path, monkeypatch):
        """The detected root must be identical regardless of the launch directory."""
        home = tmp_path / "aitao_home"
        home.mkdir()
        monkeypatch.setenv("AITAO_HOME", str(home))

        place_a = tmp_path / "a"
        place_b = tmp_path / "b"
        place_a.mkdir()
        place_b.mkdir()

        monkeypatch.chdir(place_a)
        root_a = AitaoPathManager().root

        monkeypatch.chdir(place_b)
        root_b = AitaoPathManager().root

        assert root_a == root_b, "Root resolution drifts with the working directory."
        assert root_a not in (place_a, place_b), "Root is the CWD — it must not be."

    def test_home_substitution_is_cross_platform(self, foreign_cwd, tmp_path, monkeypatch):
        """`${HOME}` must resolve even when HOME is unset (Windows uses USERPROFILE)."""
        home = tmp_path / "aitao_home"
        home.mkdir()
        monkeypatch.setenv("AITAO_HOME", str(home))

        # Simulate a Windows-like environment: no HOME, only USERPROFILE.
        monkeypatch.delenv("HOME", raising=False)
        monkeypatch.setenv("USERPROFILE", str(tmp_path / "winhome"))

        pm = AitaoPathManager()
        resolved = pm.resolve_path("${HOME}/sub")

        assert "${HOME}" not in str(resolved), (
            "`${HOME}` was left as a literal — substitution does not fall back to "
            "USERPROFILE / Path.home(), so configured paths break on Windows."
        )

    def test_missing_config_warns_clearly_and_falls_back_to_known_root(
        self, foreign_cwd, tmp_path, monkeypatch, capsys
    ):
        """No config.toml anywhere (A2): warn clearly, never skeleton the CWD silently.

        Acceptance criterion (US-098): "Config introuvable -> message clair
        (pas de squelette muet dans le CWD)". AITAO_HOME has no config/config.toml
        at all — not even a missing key, the file itself is absent.
        """
        home = tmp_path / "aitao_home"
        home.mkdir()
        monkeypatch.setenv("AITAO_HOME", str(home))

        pm = AitaoPathManager()
        assert not pm.config_path.exists(), "Test setup error: config.toml must be absent."

        storage_root = pm.get_storage_root()

        stderr = capsys.readouterr().err
        assert "storage_root" in stderr and str(pm.config_path) in stderr, (
            "Missing config must print a clear, actionable warning naming the "
            "expected config path — got: " + repr(stderr)
        )

        assert storage_root == home / "data", (
            "Fallback storage_root must land in the known AITAO_HOME-derived root, "
            f"not somewhere else — got {storage_root}."
        )
        assert not (foreign_cwd / "data").exists(), (
            "A `data/` skeleton was created in the foreign CWD instead of the "
            "documented fallback — the exact anomaly A2 exists to prevent."
        )
