# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# tests/unit/test_license_facade.py — LicenseManager.require_premium delegation
# and no-bypass guarantees (US-140: beta mode removed, Premium requires a
# valid licence key).
#
# The facade (aitao.core.license.LicenseManager) holds no key/crypto logic
# itself (see aitao.core.license module docstring): every real decision is
# delegated to the provider registered under ("license", "premium") in
# aitao.core.plugin_registry, populated (if at all) by aitao-premium's entry
# point. These tests swap that registry for a controlled one — reusing the
# "no provider at all" simulation pattern from test_core_without_premium.py —
# to prove two things without needing aitao-premium installed:
#   1. With no provider installed, require_premium() raises a message
#      pointing at the missing Premium MODULE, never "install a license key"
#      (which would be misleading with no module to accept one).
#   2. With a provider installed, require_premium() surfaces THAT provider's
#      own message verbatim — the facade never invents its own text once a
#      provider exists.
#   3. AITAO_BETA (or any other environment trick) unlocks nothing when no
#      provider is installed — there is no beta-mode code left in the core
#      to read it.

from __future__ import annotations

import pytest

from aitao.core.license import LicenseManager, PremiumFeatureError


def _empty_registry():
    from aitao.core import plugin_registry as pr

    return pr.PluginRegistry()


def _no_provider(monkeypatch):
    """Simulate aitao-premium not being installed (or not yet discovered):
    swap the global plugin registry for a fresh, empty one so a lookup of
    ("license", "premium") is guaranteed to miss, and neutralise discovery
    so a real, installed aitao-premium package (this dev environment has
    one) cannot repopulate it mid-test. Mirrors
    test_core_without_premium.py's _no_license_provider."""
    from aitao.core import plugin_registry as pr

    monkeypatch.setattr(pr, "registry", _empty_registry())
    monkeypatch.setattr(pr, "discover_plugins", lambda *a, **kw: None)


class _FakeProvider:
    """A minimal stand-in for aitao_premium.license.PremiumLicenseManager —
    proves the facade DELEGATES rather than deciding anything itself."""

    def is_premium(self) -> bool:
        return False

    def edition(self) -> str:
        return "Core"

    def require_premium(self, feature: str) -> None:
        raise PremiumFeatureError(feature, "fake-provider-specific message")

    def get_info(self) -> dict:
        return {"status": "none"}


def _with_fake_provider(monkeypatch):
    from aitao.core import plugin_registry as pr

    registry = _empty_registry()
    registry.register("license", "premium")(_FakeProvider)
    monkeypatch.setattr(pr, "registry", registry)
    monkeypatch.setattr(pr, "discover_plugins", lambda *a, **kw: None)


class TestRequirePremiumDelegation:
    def test_without_provider_points_at_the_missing_module(self, monkeypatch):
        _no_provider(monkeypatch)

        with pytest.raises(PremiumFeatureError) as exc_info:
            LicenseManager().require_premium("advanced_formats")

        message = str(exc_info.value)
        assert "Premium module" in message
        assert "not installed" in message
        assert "install a license key" not in message.lower()

    def test_with_provider_surfaces_the_providers_own_message(self, monkeypatch):
        _with_fake_provider(monkeypatch)

        with pytest.raises(PremiumFeatureError) as exc_info:
            LicenseManager().require_premium("advanced_formats")

        assert str(exc_info.value) == "fake-provider-specific message"


class TestNoBetaBypassWithoutProvider:
    """US-140: without an installed provider there is no licence decision
    left in the core at all — AITAO_BETA (or any other env var) has nothing
    to act on."""

    def test_aitao_beta_true_unlocks_nothing_without_a_provider(self, monkeypatch):
        monkeypatch.setenv("AITAO_BETA", "true")
        _no_provider(monkeypatch)

        assert LicenseManager().is_premium() is False
        with pytest.raises(PremiumFeatureError):
            LicenseManager().require_premium("advanced_formats")
