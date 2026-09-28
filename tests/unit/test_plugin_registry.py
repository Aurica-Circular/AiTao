# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Unit tests for the plugin registry (US-25).

Covers registration + lookup by name, the explicit "not found" error that lists
available plugins, and Premium gating enforced at lookup time.
"""

from unittest.mock import patch

import pytest

from aitao.core.plugin_registry import (
    PluginNotFoundError,
    PluginRegistry,
    register,
    registry,
)


def test_register_and_get():
    reg = PluginRegistry()

    @reg.register("greeter", "hello")
    class Hello:
        pass

    assert reg.get("greeter", "hello") is Hello
    assert reg.available("greeter") == ["hello"]
    assert reg.is_registered("greeter", "hello")
    assert not reg.is_registered("greeter", "missing")


def test_get_unknown_lists_available():
    reg = PluginRegistry()
    reg.register("ocr", "alpha")(object())
    reg.register("ocr", "beta")(object())

    with pytest.raises(PluginNotFoundError) as exc:
        reg.get("ocr", "missing")

    # The error names what *is* available, sorted.
    assert "alpha, beta" in str(exc.value)


def test_premium_plugin_enforced_on_lookup():
    reg = PluginRegistry()
    reg.register("ocr", "qwen_vl", premium_feature="ocr")(object())

    with patch("aitao.core.license.LicenseManager.require_premium") as req:
        reg.get("ocr", "qwen_vl")
        req.assert_called_once_with("ocr")


def test_non_premium_plugin_not_gated():
    reg = PluginRegistry()
    reg.register("ocr", "tesseract")(object())

    with patch("aitao.core.license.LicenseManager.require_premium") as req:
        reg.get("ocr", "tesseract")
        req.assert_not_called()


def test_module_level_register_uses_global_registry():
    @register("test_kind", "thing")
    def factory():
        return 1

    assert registry.get("test_kind", "thing") is factory
    assert "thing" in registry.available("test_kind")


def test_discover_plugins_registers_dropped_in_module(tmp_path, monkeypatch):
    """Dropping a self-registering module into a plugins package makes it
    available with no core change — the US-25 acceptance criterion."""
    from aitao.core.plugin_registry import discover_plugins, registry as global_registry

    pkg = tmp_path / "demo_plugins"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "fake_thing.py").write_text(
        "from aitao.core.plugin_registry import register\n\n\n"
        "@register('demo_kind', 'fake_demo')\n"
        "class FakeThing:\n"
        "    pass\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    assert not global_registry.is_registered("demo_kind", "fake_demo")
    discover_plugins("demo_plugins")
    assert global_registry.is_registered("demo_kind", "fake_demo")
    assert global_registry.get("demo_kind", "fake_demo").__name__ == "FakeThing"


def test_discover_plugins_skips_broken_module(tmp_path, monkeypatch):
    """A plugin that fails to import is skipped, not fatal — the others load."""
    from aitao.core.plugin_registry import discover_plugins, registry as global_registry

    pkg = tmp_path / "broken_plugins"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "good.py").write_text(
        "from aitao.core.plugin_registry import register\n\n\n"
        "@register('demo_kind', 'good_demo')\n"
        "class Good:\n"
        "    pass\n"
    )
    (pkg / "broken.py").write_text("import a_module_that_does_not_exist_xyz\n")
    monkeypatch.syspath_prepend(str(tmp_path))

    discover_plugins("broken_plugins")  # must not raise
    assert global_registry.is_registered("demo_kind", "good_demo")
