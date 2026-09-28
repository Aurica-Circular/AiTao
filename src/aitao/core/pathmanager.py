# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

import sys
from typing import Dict, List, Any
from pathlib import Path
from .lib.path_manager import GenericPathManager

class AitaoPathManager(GenericPathManager):
    """
    Project-specific PathManager for 'Aitao'.
    Inherits generic capabilities and implements specific business logic.
    """

    def __init__(self):
        # Detect the project root (markers / AITAO_HOME — never the CWD, US-098 A1).
        super().__init__(
            config_filename="config.toml",
            root_markers=["aitao.sh", "requirements.txt"]
        )
        # Paths and settings are resolved LAZILY on first access (US-098 A3/A4):
        #   - no directory creation as an import side-effect;
        #   - sourcing from the shared config loader avoids the
        #     config -> logger -> pathmanager import cycle.
        self._system_paths = None
        self._settings_cache = None

    def _ensure_path(self, relative_path: str) -> Path:
        """Helper to get a path relative to project root."""
        return self.root / relative_path

    def _settings(self) -> Dict[str, Any]:
        """Raw, env-layered config via the single shared loader (US-098 A4).

        PathManager is the *only* component that turns these raw strings into
        filesystem paths; ConfigManager owns the settings, not the resolution.
        The loader is log-free, so this is safe from the import cycle.
        """
        if self._settings_cache is None:
            try:
                from aitao.core.config import load_merged_config
                self._settings_cache = load_merged_config(self.config_path)
            except Exception:
                self._settings_cache = {}
        return self._settings_cache

    @property
    def system_paths(self) -> Dict[str, Any]:
        """Resolved system paths (storage_root, models_dir, logs_dir), lazily built."""
        if self._system_paths is None:
            self._system_paths = self._resolve_system_paths()
        return self._system_paths

    def _resolve_system_paths(self) -> Dict[str, Any]:
        # Fallback defaults (used only if the config provides nothing).
        paths: Dict[str, Any] = {
            "storage_root": self._ensure_path("data"),
            "models_dir": self.root.parent / "AI-models",
            "logs_dir": self._ensure_path("data/logs"),
        }
        pcfg = self._settings().get("paths", {}) or {}

        # --- Storage Root ---
        raw_storage = pcfg.get("storage_root")
        if raw_storage:
            paths["storage_root"] = self.resolve_path(raw_storage)
        else:
            # A2 (US-098): never fall back silently. Make the guessed location
            # visible (stderr — MCP-safe) so data is not written to an unexpected
            # place without the user knowing.
            print(
                f"⚠️  AiTao PathManager: no [paths].storage_root found in "
                f"{self.config_path} — falling back to '{paths['storage_root']}'. "
                f"Set storage_root in config.toml (or the AITAO_HOME env var) to "
                f"control where data lives.",
                file=sys.stderr,
            )

        # --- Models Dir ---
        raw_models = pcfg.get("models_dir")
        if raw_models:
            paths["models_dir"] = self.resolve_path(raw_models)

        # --- Logs Dir (resolves ${storage_root}) ---
        raw_logs = pcfg.get("logs_dir")
        if raw_logs:
            context = {"storage_root": str(paths["storage_root"])}
            p = self.resolve_path(raw_logs, context_vars=context)
            paths["logs_dir"] = p if p.is_absolute() else paths["storage_root"] / p
        else:
            paths["logs_dir"] = paths["storage_root"] / "logs"

        self._create_structure(paths)
        return paths

    def _create_structure(self, paths: Dict[str, Any]) -> None:
        """Creates the required folder hierarchy for AiTao V2 (lazy, on first access)."""
        storage = paths["storage_root"]
        storage.mkdir(parents=True, exist_ok=True)

        # V2 structure
        (storage / "lancedb").mkdir(exist_ok=True)
        (storage / "queue").mkdir(exist_ok=True)
        (storage / "cache").mkdir(exist_ok=True)
        (storage / "cache" / "ocr").mkdir(exist_ok=True)
        (storage / "cache" / "translations").mkdir(exist_ok=True)
        (storage / "corrections").mkdir(exist_ok=True)

        # Legacy (keep for now)
        (storage / "history").mkdir(exist_ok=True)

        paths["logs_dir"].mkdir(parents=True, exist_ok=True)

    # --- Project Specific Accessors ---

    def get_storage_root(self) -> Path:
        return self.system_paths["storage_root"]

    def get_logs_dir(self) -> Path:
        return self.system_paths["logs_dir"]

    def get_vector_db_path(self) -> Path:
        return self.system_paths["storage_root"] / "lancedb"

    def get_sql_db_path(self) -> str:
        db_path = self.system_paths["storage_root"] / "history" / "chat_history.db"
        return str(db_path)

    def get_models_dir(self) -> Path:
        return self.system_paths["models_dir"]

    def get_src_dir(self) -> Path:
        """Directory containing the aitao package (src/), for subprocess cwd bootstrap."""
        return self.root / "src"

    def get_queue_dir(self) -> Path:
        """Get queue directory for task management."""
        return self.system_paths["storage_root"] / "queue"

    def get_queue_file(self) -> Path:
        """Get path to the task queue JSON file."""
        return self.get_queue_dir() / "tasks.json"

    def get_scanner_state_file(self) -> Path:
        """Get path to scanner state file for incremental scans."""
        return self.system_paths["storage_root"] / "scanner_state.json"

    def get_trash_state_file(self) -> Path:
        """Get path to the deleted-files trash registry (US-28a)."""
        return self.system_paths["storage_root"] / "trash_state.json"

    def get_cache_dir(self, cache_type: str = None) -> Path:
        """
        Get cache directory.
        
        Args:
            cache_type: Optional subdirectory ('ocr', 'translations'). 
                       If None, returns base cache dir.
        
        Returns:
            Path to cache directory
        """
        base_cache = self.system_paths["storage_root"] / "cache"
        if cache_type:
            return base_cache / cache_type
        return base_cache

    def get_corrections_dir(self) -> Path:
        """Get corrections directory for user feedback."""
        return self.system_paths["storage_root"] / "corrections"

    def get_chat_history_dir(self) -> Path:
        """Get chat history directory."""
        return self.system_paths["storage_root"] / "history" / "chat"

    def resolve_include_paths(self, raw_paths: List[str], existing_only: bool = True) -> List[str]:
        """Resolve a list of raw include_path strings — PathManager is the sole resolver.

        ConfigManager holds the raw ``${HOME}/...`` strings; here they are expanded.
        The *list* may come from any (possibly injected) config; the *resolution* always
        happens here (US-098 A4). ``existing_only`` drops roots that are not currently
        mounted (scanner); pass False to keep the full configured allow-list.
        """
        resolved = [self.resolve_path(p) for p in (raw_paths or [])]
        if existing_only:
            resolved = [p for p in resolved if p.exists()]
        return [str(p) for p in resolved]

    def get_include_paths(self, existing_only: bool = True) -> List[str]:
        """Resolved include_paths from the global config (US-098 A4)."""
        raw = self._settings().get("indexing", {}).get("include_paths", []) or []
        return self.resolve_include_paths(raw, existing_only=existing_only)

    def get_indexing_config(self) -> Dict[str, List[str]]:
        """Specific logic to parse indexing arrays (paths resolved here, US-098 A4)."""
        idx = self._settings().get("indexing", {})
        return {
            "include_paths": self.get_include_paths(existing_only=True),
            "exclude_dirs": idx.get("exclude_dirs", []),
            "exclude_files": idx.get("exclude_files", []),
            "exclude_extensions": idx.get("exclude_extensions", [])
        }

    def get_ocr_config(self) -> Dict[str, Any]:
        """Return OCR router configuration with defaults."""
        ocr_cfg = self._settings().get("ocr", {})
        qwen_path = ocr_cfg.get("qwen_model_path", "")
        if qwen_path:
            qwen_path = str(self.resolve_path(qwen_path))
        return {
            "engine": ocr_cfg.get("engine", "auto"),
            "table_area_min": float(ocr_cfg.get("table_area_min", 0.15)),
            "min_intersections": int(ocr_cfg.get("min_intersections", 4)),
            "min_line_density": float(ocr_cfg.get("min_line_density", 0.0005)),
            "qwen_model_path": qwen_path,
        }

# Global Instance
path_manager = AitaoPathManager()
