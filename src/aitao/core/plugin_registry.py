# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Plugin registry for AiTao (US-25).

A small, explicit registry that lets interchangeable components — OCR providers,
LLM backends, file extractors — register themselves under a ``(kind, name)`` key.
The core then looks a component up by name instead of hard-coding ``if/elif`` or
an import table.

A component self-registers with the ``@register(kind, name)`` decorator; adding a
new one is therefore a single new file (auto-discovered from ``src/plugins/``)
with no change to the core. Premium components carry a feature name and are gated
through ``LicenseManager`` at lookup time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, TypeVar

T = TypeVar("T")


class PluginNotFoundError(KeyError):
    """Raised when a requested plugin name is not registered for its kind."""


@dataclass(frozen=True)
class _Entry:
    obj: Any
    premium_feature: Optional[str]


class PluginRegistry:
    """Maps ``(kind, name)`` to a registered object (a class or a factory)."""

    def __init__(self) -> None:
        self._by_kind: Dict[str, Dict[str, _Entry]] = {}

    def register(
        self, kind: str, name: str, *, premium_feature: Optional[str] = None
    ) -> Callable[[T], T]:
        """Decorator that registers the decorated object under ``(kind, name)``.

        ``premium_feature``: when set, looking the plugin up enforces that Premium
        feature through ``LicenseManager`` (see :meth:`get`).
        """

        def decorator(obj: T) -> T:
            self._by_kind.setdefault(kind, {})[name] = _Entry(obj, premium_feature)
            return obj

        return decorator

    def get(self, kind: str, name: str) -> Any:
        """Return the object registered under ``(kind, name)``.

        Raises:
            PluginNotFoundError: if no plugin of that name exists — the message
                lists the names that *are* available for the kind.
            PremiumFeatureError: if the plugin is Premium-gated and the running
                edition is Core (raised by ``LicenseManager.require_premium``).
        """
        entries = self._by_kind.get(kind, {})
        entry = entries.get(name)
        if entry is None:
            available = ", ".join(self.available(kind)) or "(none)"
            raise PluginNotFoundError(
                f"No '{kind}' plugin named '{name}'. Available: {available}"
            )
        if entry.premium_feature:
            from aitao.core.license import LicenseManager

            LicenseManager().require_premium(entry.premium_feature)
        return entry.obj

    def available(self, kind: str) -> List[str]:
        """Return the sorted names registered for ``kind``."""
        return sorted(self._by_kind.get(kind, {}))

    def is_registered(self, kind: str, name: str) -> bool:
        """Whether ``name`` is registered for ``kind``."""
        return name in self._by_kind.get(kind, {})


# Global registry shared across the app, plus a module-level decorator shortcut.
registry = PluginRegistry()


def register(
    kind: str, name: str, *, premium_feature: Optional[str] = None
) -> Callable[[T], T]:
    """Module-level shortcut for ``registry.register(...)`` (see PluginRegistry)."""
    return registry.register(kind, name, premium_feature=premium_feature)


_discovered: set = set()

# Entry-point group under which an externally installed package (e.g. AiTao
# Premium, distributed separately from this repository) can advertise plugin
# modules for the core to import. The core never names that package: it only
# knows the group, so Premium can register itself without the core knowing it
# exists (US — plugin discovery via entry points).
_ENTRY_POINT_GROUP = "aitao.plugins"
_entry_points_discovered = False


def discover_plugins(package: str = "aitao.plugins") -> None:
    """Import every module under ``package`` so its @register decorators run,
    then do the same for every ``aitao.plugins`` entry point.

    This is what makes "drop a file in src/plugins/… and it self-registers"
    work, with no change to the core — and, via entry points, what lets a
    separately installed package (Premium or any third party) register its
    own plugins without the core importing or even knowing its name. Idempotent
    (built-in package scanned at most once per package name, entry points
    scanned at most once overall — calling this more than once is safe and a
    no-op the second time). A plugin module or entry point that fails to
    import is logged with the core logger and skipped — never fatal.
    """
    _discover_builtin_package(package)
    _discover_entry_points()


def _discover_builtin_package(package: str) -> None:
    """Import every module under the built-in ``package`` (see discover_plugins)."""
    if package in _discovered:
        return
    _discovered.add(package)

    import importlib
    import pkgutil

    from aitao.core.logger import get_logger

    logger = get_logger("core.plugin_registry")

    try:
        pkg = importlib.import_module(package)
    except ModuleNotFoundError:
        return  # no such package — nothing to discover
    for info in pkgutil.walk_packages(pkg.__path__, prefix=f"{pkg.__name__}."):
        try:
            importlib.import_module(info.name)
        except Exception as exc:
            logger.warning(
                f"Failed to load plugin module {info.name}: {exc}",
                metadata={"module": info.name, "error": str(exc)},
            )


def _discover_entry_points() -> None:
    """Import the target module of every ``aitao.plugins`` entry point.

    Importing is enough — a well-behaved plugin module self-registers with
    ``@register`` at import time, exactly like a built-in plugin module does.
    A third-party entry point that fails to resolve or import is logged and
    skipped; it never crashes startup, since a broken external package must
    not take down the free edition.
    """
    global _entry_points_discovered
    if _entry_points_discovered:
        return
    _entry_points_discovered = True

    import importlib.metadata

    from aitao.core.logger import get_logger

    logger = get_logger("core.plugin_registry")

    try:
        entry_points = importlib.metadata.entry_points(group=_ENTRY_POINT_GROUP)
    except Exception as exc:
        logger.warning(
            f"Failed to enumerate '{_ENTRY_POINT_GROUP}' entry points: {exc}",
            metadata={"group": _ENTRY_POINT_GROUP, "error": str(exc)},
        )
        return

    for ep in entry_points:
        try:
            ep.load()
        except Exception as exc:
            logger.warning(
                f"Failed to load plugin entry point {ep.name}: {exc}",
                metadata={"entry_point": ep.name, "error": str(exc)},
            )
