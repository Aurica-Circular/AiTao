# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
AiTao — src/core/config.py

Layered TOML config loader, aligned with tao-init v1.0.0 python adapter.
Replaces the former YAML-based loader (migrated US-045, 2026-03-10).

Uses tomllib (Python 3.11+ stdlib). No external dependencies required.
For Python 3.10, tomli is used as a fallback (must be installed).

Layer priority (highest wins):
  config/config.toml  →  ~/.config/aitao/user.toml  →  env vars (APP__SECTION__KEY)

Usage:
    from aitao.core.config import ConfigManager

    config = ConfigManager()

    # Typed access (US-22) — preferred:
    config.llm.backend
    config.search.meilisearch.url
    config.indexing.include_paths

    # Legacy access (deprecated, kept for one version):
    config.get("paths.storage_root")
    config.get_section("indexing")
    config.reload()
"""

from __future__ import annotations

import copy
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import tomllib                  # Python 3.11+ stdlib
except ModuleNotFoundError:
    try:
        import tomli as tomllib     # uv pip install tomli (Python 3.10 fallback)
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "tomllib not available. "
            "Python 3.11+ includes it in stdlib. "
            "For Python 3.10, run: uv pip install tomli"
        ) from exc

try:
    from aitao.core.logger import get_logger
except ImportError:
    from aitao.core.logger import get_logger

from aitao.core.config_schema import (  # noqa: E402
    Settings,
    AppConfig,
    PathsConfig,
    IdentityConfig,
    LLMConfig,
    IndexingConfig,
    SearchConfig,
    RAGConfig,
    ChunkingConfig,
    WorkerConfig,
    OCRConfig,
    TranslationConfig,
    CategoriesConfig,
    ResourcesConfig,
    APIConfig,
    LoggerConfig,
)


class ConfigError(Exception):
    """Raised when configuration is invalid or missing."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _deep_merge(base: dict, override: dict) -> dict:
    """
    Recursively merge `override` into `base`.
    Returns the mutated `base`. Does not affect scalar types.
    """
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def load_merged_config(config_path: Path) -> Dict[str, Any]:
    """Single, side-effect-free config loader (US-098 A4).

    Returns defaults + config.toml + ~/.config/aitao/user.toml + ``APP__SECTION__KEY``
    env overrides, deep-merged — with **no ${...} expansion and no logging**.

    This is the one source of truth for *what the config says*, shared by both
    ConfigManager (typed settings) and PathManager (path resolution), so the two
    can never diverge. Resolving raw path strings into filesystem paths is
    PathManager's responsibility, not this loader's. Being log-free, it is safe to
    call from PathManager without the config→logger→pathmanager import cycle.
    """
    merged: Dict[str, Any] = copy.deepcopy(ConfigManager._DEFAULTS)

    with open(config_path, "rb") as f:
        _deep_merge(merged, tomllib.load(f))

    user_toml = Path.home() / ".config" / "aitao" / "user.toml"
    if user_toml.exists():
        with open(user_toml, "rb") as f:
            _deep_merge(merged, tomllib.load(f))

    for envvar, value in os.environ.items():
        if envvar.startswith("APP__"):
            parts = envvar.split("__", 2)
            if len(parts) == 3:
                _, section, key = parts
                merged.setdefault(section.lower(), {})[key.lower()] = value

    return merged


# ---------------------------------------------------------------------------
# ConfigManager
# ---------------------------------------------------------------------------

class ConfigManager:
    """
    Thread-safe layered TOML configuration manager.

    Layers (later overrides earlier):
      1. Built-in defaults (_DEFAULTS)
      2. config/config.toml  (project config, committed)
      3. ~/.config/aitao/user.toml  (user overrides, not committed)
      4. Environment variables:  APP__<SECTION>__<KEY>=value

    Public API:
      get(key, default)     — dot-notation key, e.g. "paths.storage_root"
      get_section(section)  — returns full section dict (copy)
      require(key)          — like get() but raises SystemExit if missing
      set(key, value)       — runtime override (not persisted)
      reload()              — reload from disk
      dump()                — full merged config (for debugging)
    """

    _DEFAULTS: dict[str, Any] = {
        "app": {
            "name": "aitao",
            "mode": "normal",
            # version is intentionally absent — single source of truth is pyproject.toml
        },
        "paths": {
            "storage_root": "${HOME}/.aitao/data",
            "models_dir":   "${HOME}/.aitao/models",
        },
        "indexing": {
            "enabled":          True,
            "interval_minutes": 60,
            "include_paths":    [],
            "exclude_dirs":     [".git", ".DS_Store", "__pycache__"],
            "exclude_files":    [],
            "exclude_extensions": [],
        },
        "ocr": {
            "provider":             "auto",
            "languages":            ["fr", "en"],
            "confidence_threshold": 0.7,
        },
        "translation": {
            "provider":    "mbart50",
            "source_lang": "fr",
            "target_lang": "zh_TW",
        },
        "search": {
            "meilisearch": {
                "url":        "http://localhost:7700",
                "api_key":    "",
                "index_name": "aitao_documents",
            },
            "lancedb": {
                "embedding_model": "BAAI/bge-m3",
                "table_name":      "aitao_embeddings",
                "dimension":       1024,
                "top_k":           20,
                "min_score":       0.45,
            },
        },
        "api": {
            "host": "127.0.0.1",
            "port": 8200,
        },
        "resources": {
            "max_workers": 4,
            "batch_size":  10,
        },
        "logger": {
            "level":          "info",
            "console_pretty": True,
            "file_json":      True,
            "max_file_mb":    100,
            "max_files":      5,
        },
        "rag": {
            "enabled":             True,
            "use_chunks":          True,
            "max_context_chunks":  5,
            "context_max_tokens":  4000,
            "min_relevance_score": 0.3,
            "verify_answer":       "fast",
            "intent_router":       True,
        },
    }

    def __init__(self, config_path: Optional[str] = None, auto_reload: bool = False):
        """
        Initialize ConfigManager.

        Args:
            config_path:  Path to config.toml. If None, searches standard locations.
            auto_reload:  Reserved for future hot-reload support.
        """
        self.logger = get_logger("config")
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {}
        self._settings: Settings = Settings()
        self._last_modified: Optional[float] = None
        self._auto_reload = auto_reload

        # Resolve config file path
        if config_path:
            # Accept both old .yaml and new .toml paths transparently during migration
            resolved = Path(config_path)
            if not resolved.exists() and resolved.suffix in (".yaml", ".yml"):
                toml_equivalent = resolved.with_suffix(".toml")
                if toml_equivalent.exists():
                    resolved = toml_equivalent
            self.config_path = resolved
        else:
            self.config_path = self._find_config()

        self.reload()
        self.logger.info(
            "ConfigManager initialized",
            metadata={"config_path": str(self.config_path), "sections": list(self._data.keys())},
        )

    # ------------------------------------------------------------------
    def _find_project_root(self) -> Optional[Path]:
        """Walk up from this file searching for project markers."""
        current = Path(__file__).resolve().parent
        markers = ["aitao.sh", "pyproject.toml"]
        for _ in range(10):
            for marker in markers:
                if (current / marker).exists():
                    return current
            parent = current.parent
            if parent == current:
                break
            current = parent
        return None

    def _find_config(self) -> Path:
        """Locate config.toml in standard locations."""
        project_root = self._find_project_root()
        candidates: list[Path] = []

        if project_root:
            candidates += [
                project_root / "config" / "config.toml",
                project_root / "config.toml",
            ]
        candidates += [
            Path("config/config.toml"),
            Path("config.toml"),
            Path.home() / ".config" / "aitao" / "user.toml",
        ]

        for path in candidates:
            if path.exists():
                return path

        raise ConfigError(
            f"Configuration file not found. Searched: {[str(p) for p in candidates]}\n"
            "Run: cp config/config.toml.template config/config.toml"
        )

    # ------------------------------------------------------------------
    def reload(self) -> None:
        """
        Reload configuration from disk.
        Thread-safe; can be called manually to pick up file changes.
        """
        with self._lock:
            try:
                if not self.config_path.exists():
                    raise ConfigError(f"Config file not found: {self.config_path}")

                current_mtime = self.config_path.stat().st_mtime
                if self._last_modified and current_mtime == self._last_modified:
                    return

                # Defaults + config.toml + user.toml + APP__ env, via the shared
                # raw loader. NO ${...} expansion: ConfigManager holds raw settings;
                # resolving paths is PathManager's job (US-098 A4).
                self._data = load_merged_config(self.config_path)
                self._last_modified = current_mtime

                # 6. Build typed settings object
                self._settings = Settings.model_validate(self._data)

            except (OSError, ValueError) as e:
                raise ConfigError(f"Invalid TOML syntax: {e}") from e

    # ------------------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        """
        Return a config value by dot-notation key (legacy API — prefer typed properties).

        Prefer: config.paths.storage_root / config.api.port / config.llm.generation.temperature
        """
        with self._lock:
            parts = key.split(".")
            node = self._data
            for part in parts:
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    return default
            return node

    def require(self, key: str) -> Any:
        """Like get() but raises SystemExit with a clear message if the key is missing."""
        _sentinel = object()
        value = self.get(key, _sentinel)
        if value is _sentinel:
            raise SystemExit(f"Required config key missing: {key}")
        return value

    def get_section(self, section: str) -> Dict[str, Any]:
        """Return an entire top-level section as a dict (copy)."""
        with self._lock:
            if section not in self._data:
                raise ConfigError(f"Configuration section not found: '{section}'")
            return dict(self._data[section])

    def set(self, key: str, value: Any) -> None:
        """
        Set a config value at runtime (not persisted to disk).
        Supports dot-notation: config.set("api.port", 9000)
        """
        with self._lock:
            parts = key.split(".")
            node = self._data
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value

    def dump(self) -> dict[str, Any]:
        """Return the full merged config dict (useful for debugging)."""
        with self._lock:
            return copy.deepcopy(self._data)

    # ------------------------------------------------------------------
    # Typed section properties (US-22)
    # ------------------------------------------------------------------

    @property
    def settings(self) -> Settings:
        """Full typed settings object."""
        return self._settings

    @property
    def app(self) -> AppConfig:
        return self._settings.app

    @property
    def paths(self) -> PathsConfig:
        return self._settings.paths

    @property
    def identity(self) -> IdentityConfig:
        return self._settings.identity

    @property
    def llm(self) -> LLMConfig:
        return self._settings.llm

    @property
    def indexing(self) -> IndexingConfig:
        return self._settings.indexing

    @property
    def search(self) -> SearchConfig:
        return self._settings.search

    @property
    def rag(self) -> RAGConfig:
        return self._settings.rag

    @property
    def chunking(self) -> ChunkingConfig:
        return self._settings.chunking

    @property
    def worker(self) -> WorkerConfig:
        return self._settings.worker

    @property
    def ocr(self) -> OCRConfig:
        return self._settings.ocr

    @property
    def translation(self) -> TranslationConfig:
        return self._settings.translation

    @property
    def categories(self) -> CategoriesConfig:
        return self._settings.categories

    @property
    def resources(self) -> ResourcesConfig:
        return self._settings.resources

    @property
    def api(self) -> APIConfig:
        return self._settings.api

    @property
    def log(self) -> LoggerConfig:
        """Logger configuration (alias: 'logger' section in TOML)."""
        return self._settings.log


# ---------------------------------------------------------------------------
# Singleton helper (backward-compatible with existing callers)
# ---------------------------------------------------------------------------

_config_manager: Optional[ConfigManager] = None
_singleton_lock = threading.Lock()


def get_config(config_path: Optional[str] = None) -> ConfigManager:
    """
    Return a process-wide ConfigManager singleton.

    Args:
        config_path: Path to config.toml (only used on first call).

    Returns:
        Shared ConfigManager instance.
    """
    global _config_manager
    with _singleton_lock:
        if _config_manager is None:
            _config_manager = ConfigManager(config_path)
        return _config_manager

