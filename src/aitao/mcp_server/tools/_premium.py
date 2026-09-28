# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# mcp_server/tools/_premium.py — Premium license guard for MCP tools (US-055)
#
# Responsibilities:
#   - Centralise premium feature gating for MCP tools
#   - Convert PremiumFeatureError into PermissionError (FastMCP wraps it as MCP error)

from __future__ import annotations


def require_premium(feature: str) -> None:
    """Raise PermissionError if the current license does not include the feature.

    Args:
        feature: Internal feature name shown in error messages.

    Raises:
        PermissionError: When license is insufficient.
    """
    try:
        from aitao.core.license import LicenseManager  # type: ignore
        LicenseManager().require_premium(feature)
    except ImportError:
        # License module not available — allow (development mode)
        pass
    except Exception as exc:
        # Covers PremiumFeatureError or any other license error
        raise PermissionError(
            f"Feature '{feature}' requires an AiTao Premium license. {exc}"
        ) from exc
