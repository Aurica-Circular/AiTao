# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
AiTao version information.

This module provides version information for the AiTao package.
The single source of truth is pyproject.toml - this file reads from there.

Version format: MAJOR.MINOR.PATCH
  - MAJOR: Version (1=V1 legacy, 2=V2 modular)
  - MINOR: Sprint number (0=Foundation, 1=Indexation, etc.)
  - PATCH: User Story number within sprint

Example: 2.0.5 = V2, Sprint 0, US-005 completed
"""

from importlib.metadata import version as metadata_version, PackageNotFoundError


def _read_version_from_pyproject() -> str:
    """Read version directly from pyproject.toml (single source of truth).

    Delegates to path_manager.root (marker-based repo root resolution) rather
    than counting Path(__file__).parent hops — the previous hand-rolled count
    silently broke when US-114 moved this module from src/core/ to
    src/aitao/core/ (3 hops became 4), causing _get_version() to fall back to
    a possibly-stale installed package version. No import-cycle risk: neither
    pathmanager.py nor its .lib.path_manager base import core.version.
    """
    from aitao.core.pathmanager import path_manager

    pyproject_path = path_manager.root / "pyproject.toml"
    
    if pyproject_path.exists():
        try:
            import tomllib
        except ImportError:
            # Python < 3.11 fallback
            try:
                import tomli as tomllib  # type: ignore
            except ImportError:
                return "0.0.0"
        
        with open(pyproject_path, "rb") as f:
            data = tomllib.load(f)
        return data.get("project", {}).get("version", "0.0.0")
    
    return "0.0.0"


def _get_version() -> str:
    """Get version from pyproject.toml (single source of truth)."""
    # Always read from pyproject.toml first (single source of truth)
    version = _read_version_from_pyproject()
    if version != "0.0.0":
        return version
    
    # Fallback to installed metadata if pyproject.toml not found
    try:
        return metadata_version("aitao")
    except PackageNotFoundError:
        return "0.0.0"


__version__ = _get_version()

# Semantic parts
VERSION_PARTS = __version__.split('.')
MAJOR = int(VERSION_PARTS[0]) if len(VERSION_PARTS) > 0 else 2
MINOR = int(VERSION_PARTS[1]) if len(VERSION_PARTS) > 1 else 0
PATCH = int(VERSION_PARTS[2]) if len(VERSION_PARTS) > 2 else 0


def get_version() -> str:
    """Return the current version string."""
    return __version__


def get_version_info() -> dict:
    """Return detailed version information."""
    import sys
    return {
        "version": __version__,
        "major": MAJOR,
        "sprint": MINOR,
        "user_story": PATCH,
        "codename": "Foundation" if MINOR == 0 else f"Sprint {MINOR}",
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    }
