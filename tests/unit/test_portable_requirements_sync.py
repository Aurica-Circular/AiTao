# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Test: portable requirements files are in sync with pyproject.toml core dependencies.

Rationale: pyproject.toml is the source of truth for dependencies.
The portable requirements files (arm64 / amd64) are hand-maintained copies
of those same dependencies for environments that do not use 'uv pip install -e'.
A mismatch between the two causes ModuleNotFoundError at runtime in the field
(see bug: 'cryptography' missing, v2.7.48).
"""

from pathlib import Path
import re
import tomllib
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parents[2]
PYPROJECT = PROJECT_ROOT / "pyproject.toml"
PORTABLE_FILES = [
    PROJECT_ROOT / "portable" / "arm64" / "requirements-portable.txt",
    PROJECT_ROOT / "portable" / "amd64" / "requirements-portable.txt",
]

# Packages that are intentionally excluded from portable requirements.
# Add here only packages whose omission is a deliberate design choice
# (e.g. llama-cpp-python which requires a C++ compiler).
INTENTIONAL_EXCLUSIONS = {
    "llama-cpp-python",
}


def _parse_pyproject_core_deps(path: Path) -> dict[str, str]:
    """Return {package_name: version_spec} from pyproject.toml [project.dependencies]."""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    deps = data.get("project", {}).get("dependencies", [])
    result: dict[str, str] = {}
    for dep in deps:
        # Strip inline comments and whitespace
        dep = dep.split("#")[0].strip()
        if not dep:
            continue
        # Split on first operator: >=, <=, ==, ~=, !=
        name = re.split(r"[><=!~]", dep)[0].strip().lower()
        result[name] = dep
    return result


def _parse_requirements_names(path: Path) -> set[str]:
    """Return lowercase package names found in a requirements.txt file."""
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        name = re.split(r"[><=!~]", line)[0].strip().lower()
        if name:
            names.add(name)
    return names


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("portable_file", PORTABLE_FILES, ids=lambda p: p.parent.name)
def test_portable_contains_all_pyproject_core_deps(portable_file: Path) -> None:
    """Every core dependency in pyproject.toml must appear in the portable requirements file,
    unless it is listed in INTENTIONAL_EXCLUSIONS."""
    pyproject_deps = _parse_pyproject_core_deps(PYPROJECT)
    portable_names = _parse_requirements_names(portable_file)

    missing = {
        name
        for name in pyproject_deps
        if name not in portable_names and name not in INTENTIONAL_EXCLUSIONS
    }

    assert not missing, (
        f"\n{portable_file.relative_to(PROJECT_ROOT)}\n"
        f"Missing dependencies (present in pyproject.toml but absent from portable requirements):\n"
        + "\n".join(f"  - {pyproject_deps[name]}" for name in sorted(missing))
        + "\n\nFix: add the missing line(s) to the portable requirements file,"
        " or add to INTENTIONAL_EXCLUSIONS if the omission is deliberate."    )
