# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# tests/unit/test_cli_license.py — `aitao license status` output per licence
# state (US-140: the "Beta mode active" branch is gone; one clear panel per
# state instead: module not installed, module installed with no key, active,
# expired (warm message), invalid).
#
# LicenseManager.get_info() is monkeypatched directly (typer CliRunner
# pattern, same as tests/test_cli.py / tests/test_lifecycle_commands.py) so
# every state is exercised without needing a real key file or aitao-premium.

from __future__ import annotations

import sys
from pathlib import Path

from typer.testing import CliRunner

_src = str(Path(__file__).parent.parent.parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

from aitao.cli.main import app  # noqa: E402
from aitao.core.license import LicenseManager  # noqa: E402

runner = CliRunner()


def _run_status(monkeypatch, info: dict):
    monkeypatch.setattr(LicenseManager, "get_info", lambda self: info)
    return runner.invoke(app, ["license", "status"])


class TestLicenseStatusStates:
    def test_module_not_installed(self, monkeypatch):
        result = _run_status(
            monkeypatch, {"edition": "Core", "premium_module_installed": False}
        )
        assert result.exit_code == 0
        assert "not installed" in result.stdout
        assert "Beta" not in result.stdout

    def test_module_installed_no_key(self, monkeypatch):
        result = _run_status(monkeypatch, {"status": "none"})
        assert result.exit_code == 0
        assert "No license installed" in result.stdout
        assert "license activate" in result.stdout

    def test_active(self, monkeypatch):
        result = _run_status(
            monkeypatch,
            {
                "status": "active",
                "tier": "premium",
                "exp": "2027-12-31",
                "label": "founder-phil",
                "days_left": 365,
            },
        )
        assert result.exit_code == 0
        assert "Premium" in result.stdout
        assert "founder-phil" in result.stdout
        assert "2027-12-31" in result.stdout
        assert "365" in result.stdout

    def test_expired(self, monkeypatch):
        result = _run_status(
            monkeypatch,
            {
                "status": "expired",
                "tier": "premium",
                "exp": "2020-01-01",
                "label": "beta-tester",
            },
        )
        assert result.exit_code == 0
        assert "2020-01-01" in result.stdout
        assert "beta-tester" in result.stdout
        assert "Thank you" in result.stdout
        assert "auricacircular.com" in result.stdout

    def test_invalid(self, monkeypatch):
        result = _run_status(monkeypatch, {"status": "invalid"})
        assert result.exit_code == 0
        assert "invalid" in result.stdout.lower()

    def test_no_beta_branch_left(self, monkeypatch):
        """The old signal ("Premium (beta)" edition) must no longer be
        special-cased anywhere — "status" alone drives the CLI now."""
        result = _run_status(
            monkeypatch, {"status": "none", "edition": "Premium (beta)"}
        )
        assert result.exit_code == 0
        assert "Beta mode active" not in result.stdout
