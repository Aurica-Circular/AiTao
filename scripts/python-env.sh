#!/usr/bin/env bash
# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# scripts/python-env.sh — uv-based Python venv management for AiTao
#
# Responsibilities:
#   - Ensure a valid Python venv exists at ~/.local/share/venvs/aitao (never in cloud)
#   - Create or repair the venv using uv if needed
#   - Warn if an obsolete .venv is found in the project directory
#   - Expose: ensure_python_env(), python_bin()
#
# Usage (source from aitao.sh):
#   source "$SCRIPT_DIR/scripts/python-env.sh"
#   ensure_python_env "$SCRIPT_DIR"
#   PYTHON="$(python_bin "$SCRIPT_DIR")"
#
# Note: Colors (RED/GREEN/YELLOW/NC) are expected to be set by the caller (aitao.sh).
#       This script adds no color definitions of its own.

# Centralised venv base — local disk, never synced to cloud
_AITAO_VENVS_BASE="${HOME}/.local/share/venvs"

# ---------------------------------------------------------------------------
# ensure_python_env <project_dir>
#
#   Creates or validates the venv for the project.
#   Python version read from <project_dir>/.python-version, default 3.14.
#   Exports AITAO_VENV_DIR.
#   Warns if a legacy .venv exists inside the project (cloud waste).
# ---------------------------------------------------------------------------
ensure_python_env() {
    local project_dir="${1:?ensure_python_env: project_dir required}"
    local project_name
    project_name="$(basename "$project_dir")"
    local venv_dir="${_AITAO_VENVS_BASE}/${project_name}"

    # Resolve Python version
    local python_version="3.14"
    if [ -f "${project_dir}/.python-version" ]; then
        python_version="$(tr -d '[:space:]' < "${project_dir}/.python-version")"
    fi

    # uv is mandatory
    if ! command -v uv &>/dev/null; then
        echo -e "${RED:-}❌ uv n'est pas installé.${NC:-}"
        echo "   → curl -LsSf https://astral.sh/uv/install.sh | sh"
        return 1
    fi

    # Create or repair venv if needed
    local needs_setup=false
    if [ ! -f "${venv_dir}/bin/python" ]; then
        needs_setup=true
    elif ! "${venv_dir}/bin/python" --version &>/dev/null 2>&1; then
        echo -e "${YELLOW:-}⚠️  Venv cassé (${project_name}) — recréation...${NC:-}"
        rm -rf "${venv_dir}"
        needs_setup=true
    fi

    if $needs_setup; then
        echo "⚙️  Création de l'environnement Python ${python_version}..."
        mkdir -p "${_AITAO_VENVS_BASE}"
        # --python-preference only-managed: use uv's own Python builds (downloaded
        # if needed) and never inspect interpreters found on PATH. Without it, a
        # pyenv shim that lacks the requested version makes uv abort, and AiTao
        # cannot be installed on a machine that has pyenv (found 2026-09-29 by a
        # clean-install run of the customer procedure).
        uv venv --python "${python_version}" --python-preference only-managed "${venv_dir}" || {
            echo -e "${RED:-}❌ Échec création venv (Python ${python_version} disponible ?)${NC:-}"
            return 1
        }
        echo -e "${GREEN:-}✅ Environnement prêt: ${venv_dir}${NC:-}"
    fi

    # Warn about obsolete in-project venvs (cloud waste)
    for _old_venv in "${project_dir}/venv" "${project_dir}/.venv"; do
        if [ -d "$_old_venv" ]; then
            echo -e "${YELLOW:-}⚠️  Venv obsolète dans le projet: ${_old_venv}${NC:-}" >&2
            echo "   → Supprimez-le: rm -rf \"${_old_venv}\"" >&2
        fi
    done

    export AITAO_VENV_DIR="${venv_dir}"
}

# ---------------------------------------------------------------------------
# python_bin <project_dir>
#
#   Returns the absolute path to the Python binary for this project's venv.
# ---------------------------------------------------------------------------
python_bin() {
    local project_dir="${1:?python_bin: project_dir required}"
    local project_name
    project_name="$(basename "$project_dir")"
    echo "${_AITAO_VENVS_BASE}/${project_name}/bin/python"
}
