# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# tests/unit/test_cli_smoke.py — Smoke tests for CLI command registration
#
# Ensures every command group and root command is registered, discoverable,
# and responds to --help without crashing. Catches silent regressions when
# someone removes an add_typer() or renames a subcommand.

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

# Ensure src is importable
_src = str(Path(__file__).parent.parent.parent / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

from aitao.cli.main import app  # noqa: E402

runner = CliRunner()


# ============================================================================
# Expected command tree — update when adding / removing commands
# ============================================================================

EXPECTED_ROOT_COMMANDS = [
    "dashboard",
    "docs",
    "init",
    "migrate-v4",  # US-112 (EPIC-31) — LanceDB -> Meilisearch data migration
    "restart",
    "start",
    "status",
    "stop",
    "test",
    "version",
]

EXPECTED_GROUPS = {
    "ms":        ["help", "prune", "rebuild", "restart", "start", "status", "stop", "upgrade"],
    "db":        ["clear", "help", "search", "stats", "status"],
    "config":    ["edit", "help", "show", "validate"],
    "scan":      ["clear", "help", "paths", "reindex", "run", "status"],
    "queue":     ["add", "cancel", "clear", "failures", "help", "info", "list", "retry", "status"],
    "worker":    ["help", "logs", "restart", "run-once", "start", "status", "stop"],
    "extract":   ["batch", "file", "help", "test", "types"],
    "index":     ["batch", "delete", "file", "help", "prune", "reindex", "status", "test"],
    "search":    ["help", "modes", "run", "test"],
    "lifecycle": ["restart", "start", "status", "stop"],
    "models":    ["add", "check", "fix", "help", "pull", "remove", "status", "validate"],
    "api":       ["help", "start", "status", "stop"],
    "license":   ["activate", "deactivate", "help", "status"],
    "mcp":       ["config", "help", "serve", "status", "stop"],
}


# ============================================================================
# Test: all root commands are registered
# ============================================================================

class TestRootCommandRegistration:
    """Verify that all expected root commands exist in the CLI app."""

    def _get_root_command_names(self) -> list[str]:
        names = []
        for cmd_info in app.registered_commands:
            name = cmd_info.name or (
                cmd_info.callback.__name__ if cmd_info.callback else None
            )
            if name:
                names.append(name)
        return sorted(names)

    def test_all_root_commands_registered(self):
        registered = self._get_root_command_names()
        for cmd in EXPECTED_ROOT_COMMANDS:
            assert cmd in registered, (
                f"Root command '{cmd}' missing from CLI app. "
                f"Registered: {registered}"
            )

    def test_no_unexpected_root_commands(self):
        """Catch accidentally added root commands."""
        registered = self._get_root_command_names()
        for cmd in registered:
            assert cmd in EXPECTED_ROOT_COMMANDS, (
                f"Unexpected root command '{cmd}' found. "
                f"Add it to EXPECTED_ROOT_COMMANDS if intentional."
            )


# ============================================================================
# Test: all command groups are registered
# ============================================================================

class TestGroupRegistration:
    """Verify that all expected command groups exist in the CLI app."""

    def _get_group_names(self) -> list[str]:
        return sorted(g.name for g in app.registered_groups if g.name)

    def test_all_groups_registered(self):
        registered = self._get_group_names()
        for group in EXPECTED_GROUPS:
            assert group in registered, (
                f"Command group '{group}' missing from CLI app. "
                f"Registered groups: {registered}"
            )

    def test_no_unexpected_groups(self):
        registered = self._get_group_names()
        for group in registered:
            assert group in EXPECTED_GROUPS, (
                f"Unexpected group '{group}' found. "
                f"Add it to EXPECTED_GROUPS if intentional."
            )


# ============================================================================
# Test: each group has exactly its expected subcommands
# ============================================================================

class TestGroupSubcommands:
    """Verify subcommands within each group match expectations."""

    def _get_subcommands(self, group_name: str) -> list[str]:
        for g in app.registered_groups:
            if g.name == group_name:
                names = []
                for cmd_info in g.typer_instance.registered_commands:
                    name = cmd_info.name or (
                        cmd_info.callback.__name__ if cmd_info.callback else None
                    )
                    if name:
                        names.append(name)
                return sorted(names)
        return []

    @pytest.mark.parametrize("group", sorted(EXPECTED_GROUPS.keys()))
    def test_group_has_expected_subcommands(self, group: str):
        actual = self._get_subcommands(group)
        expected = sorted(EXPECTED_GROUPS[group])
        assert actual == expected, (
            f"Group '{group}' subcommands mismatch.\n"
            f"  Expected: {expected}\n"
            f"  Actual:   {actual}\n"
            f"  Missing:  {sorted(set(expected) - set(actual))}\n"
            f"  Extra:    {sorted(set(actual) - set(expected))}"
        )


# ============================================================================
# Test: --help works for root app and every group
# ============================================================================

class TestHelpSmoke:
    """Verify --help responds without crash for the root app and all groups."""

    def test_root_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "AiTao" in result.stdout

    @pytest.mark.parametrize("group", sorted(EXPECTED_GROUPS.keys()))
    def test_group_help(self, group: str):
        result = runner.invoke(app, [group, "--help"])
        assert result.exit_code == 0, (
            f"'{group} --help' failed with exit_code={result.exit_code}\n"
            f"Output: {result.stdout}"
        )

    @pytest.mark.parametrize("group", sorted(EXPECTED_GROUPS.keys()))
    def test_group_help_subcommand(self, group: str):
        """Test `<group> help` subcommand (where available)."""
        if "help" not in EXPECTED_GROUPS[group]:
            pytest.skip(f"Group '{group}' has no 'help' subcommand")
        result = runner.invoke(app, [group, "help"])
        assert result.exit_code == 0, (
            f"'{group} help' failed with exit_code={result.exit_code}\n"
            f"Output: {result.stdout}"
        )


# ============================================================================
# Test: root commands respond to --help
# ============================================================================

class TestRootCommandHelp:
    """Verify each root command works with --help."""

    @pytest.mark.parametrize("cmd", EXPECTED_ROOT_COMMANDS)
    def test_root_command_help(self, cmd: str):
        result = runner.invoke(app, [cmd, "--help"])
        assert result.exit_code == 0, (
            f"'{cmd} --help' failed with exit_code={result.exit_code}\n"
            f"Output: {result.stdout}"
        )


# ============================================================================
# Test: aitao.sh _GROUPS list is complete
# ============================================================================

class TestAitaoShGroupList:
    """Verify aitao.sh _GROUPS variable includes all CLI groups."""

    def _parse_groups_from_shell(self) -> set[str]:
        """Extract the _GROUPS variable from aitao.sh."""
        script = Path(__file__).parent.parent.parent / "aitao.sh"
        for line in script.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("_GROUPS="):
                # _GROUPS="queue scan worker ..."
                value = stripped.split("=", 1)[1].strip().strip('"').strip("'")
                return set(value.split())
        pytest.fail("Could not find _GROUPS variable in aitao.sh")

    def test_all_groups_in_aitao_sh(self):
        shell_groups = self._parse_groups_from_shell()
        cli_groups = set(EXPECTED_GROUPS.keys())
        # lifecycle is exposed via root start/stop/restart, not as a user group
        check_groups = cli_groups - {"lifecycle"}
        missing = check_groups - shell_groups
        assert not missing, (
            f"aitao.sh _GROUPS is missing groups: {sorted(missing)}. "
            f"Update the _GROUPS variable in aitao.sh."
        )


# ============================================================================
# US-29 — command reference generator + global "help" handling
# ============================================================================

class TestHelpArgvNormalization:
    """A trailing bare 'help'/'-h' becomes '--help' (US-29)."""

    def test_trailing_help_becomes_flag(self):
        from aitao.cli.main import normalize_help_argv
        assert normalize_help_argv(["queue", "add", "help"]) == [
            "queue", "add", "--help"]
        assert normalize_help_argv(["queue", "add", "-h"]) == [
            "queue", "add", "--help"]
        assert normalize_help_argv(["help"]) == ["--help"]

    def test_non_help_argv_unchanged(self):
        from aitao.cli.main import normalize_help_argv
        assert normalize_help_argv(["queue", "add", "file.pdf"]) == [
            "queue", "add", "file.pdf"]
        assert normalize_help_argv(["status"]) == ["status"]
        assert normalize_help_argv([]) == []


class TestCommandReferenceGenerator:
    """The docs generator renders the live command tree (US-29)."""

    def test_markdown_covers_groups_and_options(self):
        from aitao.cli.commands.docs import generate_commands_markdown
        from aitao.cli.main import app

        md = generate_commands_markdown(app)
        assert md.startswith("# AiTao — Command Reference")
        assert "Auto-generated" in md
        # a known group, subcommand and option must appear
        assert "## `queue`" in md
        assert "### `queue add`" in md
        assert "--priority" in md
        # every group is documented
        for group in EXPECTED_GROUPS:
            assert f"## `{group}`" in md

    def test_write_creates_file(self, tmp_path):
        from aitao.cli.commands.docs import write_commands_doc
        from aitao.cli.main import app

        out = tmp_path / "sub" / "COMMANDS.md"
        written = write_commands_doc(app, out)
        assert written.exists()
        assert written.read_text("utf-8").startswith("# AiTao")
