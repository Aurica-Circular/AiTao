# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
AiTao — src/core/license.py

License facade for the AiTao dual-license model (US-138-1).

This module used to hold the actual RSA signature verification, the embedded
public key, the key-file handling and the beta-mode logic. That code moved
out to the separately distributed "aitao-premium" package: this repository
is published under AGPL v3, and anyone can read, patch and redistribute code
published here, so nothing that lives in this repository can protect
anything. See aitao-premium's src/aitao_premium/license.py for the real
verification logic.

What remains here is a small, non-secret facade with the same public API
callers already use. It holds no public key, imports no crypto library,
knows nothing about beta mode, and never touches a key file directly. Every
method that needs an actual licence decision looks up the provider
registered under kind "license", name "premium" in
aitao.core.plugin_registry (populated, if at all, by aitao-premium's entry
point — see plugin_registry.discover_plugins()). When no such provider is
installed, this facade behaves as a plain Core edition: no Premium feature is
ever unlocked, and callers get a clear message pointing at
https://auricacircular.com instead of a mysterious crash.

Editions (PRD §5 — Premium gates the document *perimeter*, not the engine):
  - Core    (AGPL v3)       : indexing + search + RAG chat over text formats
                              (pdf-with-text, txt, md, log, ini…)
  - Premium (Commercial)    : advanced document formats (docx, odt, xlsx, epub…),
                              scanned-PDF / image OCR

Usage:
    from aitao.core.license import LicenseManager

    LicenseManager().require_premium("advanced_formats")  # raises if not licensed
    if LicenseManager().is_premium():
        ...
    LicenseManager().activate("AITAO-xxx.yyy")     # install a key (Premium only)
    LicenseManager().get_info()                     # {'status': 'active', 'tier': 'premium', 'exp': '...', 'label': '...', 'days_left': ...}
"""

from __future__ import annotations

# The URL shown to a Core-only user who tries a Premium action.
_PREMIUM_INFO_URL = "https://auricacircular.com"


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------

class PremiumFeatureError(RuntimeError):
    """Raised when a Premium feature is accessed without a valid license.

    ``message``, when given, replaces the default text entirely — used by an
    installed Premium provider to surface its own, licence-state-specific
    message (no key / invalid key / expired key) instead of this generic one.
    """

    def __init__(self, feature: str, message: str | None = None) -> None:
        self.feature = feature
        if message is None:
            message = (
                f"'{feature}' is a Premium feature.\n"
                "Install a license key to unlock it:\n"
                "  ./aitao.sh license activate <YOUR-KEY>\n"
                f"Purchase: {_PREMIUM_INFO_URL}"
            )
        super().__init__(message)


# ---------------------------------------------------------------------------
# License Manager (facade)
# ---------------------------------------------------------------------------

class LicenseManager:
    """
    Thin licence facade. Every real decision is delegated to the Premium
    provider registered under ("license", "premium") in
    aitao.core.plugin_registry — when it is installed. With no such
    provider, every method behaves as plain Core: no Premium feature is
    unlocked.
    """

    # Features gated behind Premium
    PREMIUM_FEATURES: tuple[str, ...] = (
        "advanced_formats", # Ingestion of docx/odt/xlsx/epub… (PRD §5 perimeter)
        "ocr_advanced",     # Qwen-VL OCR for scanned documents
        "categorization",   # Automatic document categorization
        "corrections",      # User feedback / learning loop
        "personal_assistant", # Agenda / reminders (roadmap)
    )

    # Advanced document formats reserved to Premium at *ingestion* (PRD §5):
    # Core indexes text formats (pdf-with-text, txt, md, log, ini, json, code…);
    # scanned PDF / images are gated separately at the OCR step.
    PREMIUM_EXTENSIONS: frozenset = frozenset({
        ".docx", ".docm", ".pptx", ".pptm",
        ".xlsx", ".xlsm", ".odt", ".ods", ".odp", ".epub",
    })

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_premium(self) -> bool:
        """Return True if Premium features are unlocked."""
        provider = self._provider()
        if provider is None:
            return False
        return provider.is_premium()

    def require_premium(self, feature: str) -> None:
        """Raise PremiumFeatureError if the feature is not accessible.

        Delegates entirely to the installed provider's own
        ``require_premium()`` so its licence-state-specific message (no key
        installed / an invalid key / an expired key, each with its own
        wording) reaches the caller. Without a provider installed there is
        no licence decision to delegate to: raise directly here, pointing at
        the missing Premium module itself — never "install a license key",
        which would be misleading when there is no module to accept one.
        """
        provider = self._provider()
        if provider is None:
            raise PremiumFeatureError(
                feature,
                "This feature requires the AiTao Premium module, which is "
                f"not installed. See {_PREMIUM_INFO_URL} to obtain it.",
            )
        provider.require_premium(feature)

    @staticmethod
    def is_premium_extension(ext: str) -> bool:
        """True if ``ext`` is an advanced document format reserved to Premium."""
        return ext.lower() in LicenseManager.PREMIUM_EXTENSIONS

    def edition(self) -> str:
        """Return a human-readable edition string for display purposes."""
        provider = self._provider()
        if provider is None:
            return "Core"
        return provider.edition()

    def activate(self, key_str: str) -> bool:
        """Install a license key. Returns True if the key is valid.

        Raises:
            RuntimeError: if the Premium module is not installed — there is
                nothing here that could verify a key.
        """
        provider = self._provider()
        if provider is None:
            raise RuntimeError(
                "The AiTao Premium module is not installed — there is no "
                "licence verification available in the Core edition. "
                f"See {_PREMIUM_INFO_URL} to obtain it."
            )
        return provider.activate(key_str)

    def deactivate(self) -> None:
        """Remove the installed license key, if any. A no-op when the
        Premium module is not installed (there is nothing to remove)."""
        provider = self._provider()
        if provider is None:
            return
        provider.deactivate()

    def get_info(self) -> dict:
        """Return parsed license payload, or a Core-edition summary.

        With the Premium module installed, this is exactly what it returns
        (an empty dict when no key is installed, the parsed payload
        otherwise). Without it, this reports the Core edition explicitly so
        callers can tell "no key yet" apart from "nothing to check licences
        with at all".
        """
        provider = self._provider()
        if provider is None:
            return {"edition": "Core", "premium_module_installed": False}
        return provider.get_info()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _provider():
        """Return the installed Premium license provider instance, or None.

        Imported lazily to avoid a circular import (plugin_registry itself
        imports this module to enforce premium_feature-gated lookups).
        """
        from aitao.core.plugin_registry import (
            PluginNotFoundError,
            discover_plugins,
            registry,
        )

        discover_plugins()
        try:
            provider_cls = registry.get("license", "premium")
        except PluginNotFoundError:
            return None
        return provider_cls()
