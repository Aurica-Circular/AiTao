# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

import os
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

# TOML import handling: priority order (stdlib > tomli > external toml)
tomllib = None
try:
    import tomllib  # Python 3.11+
except ImportError:
    try:
        import tomli as tomllib
    except ImportError:
        try:
            import toml as tomllib  # Fallback to the classic 'toml' lib, often present
        except ImportError:
            tomllib = None

# Optional YAML support (config is TOML in V2; this keeps the YAML branch defined
# so it degrades gracefully instead of raising NameError when PyYAML is absent).
try:
    import yaml
except ImportError:
    yaml = None

class GenericPathManager:
    """
    A generic, reusable configuration and path manager.
    
    Features:
    - Root project detection based on marker files.
    - TOML configuration loading.
    - Variable substitution (e.g., $storage_root, $HOME or ~/).
    - API Route construction (protocol, host, port).
    """

    def __init__(self, 
                 config_filename: str = "config.toml", 
                 root_markers: List[str] = None,
                 base_dir: str = None):
        """
        Initialize the PathManager.
        
        :param config_filename: Name of the config file to load.
        :param root_markers: List of files/dirs to look for to identify project root (e.g. ['.git', 'pyproject.toml']).
        :param base_dir: Optional explicit root path. If None, auto-detection starts from this file's location.
        """
        self.config_filename = config_filename
        self.root_markers = root_markers or [".git", "pyproject.toml", "requirements.txt"]
        
        if base_dir:
            self.root = Path(base_dir).resolve()
        else:
            self.root = self._detect_project_root()

        self.config_path = self.root / "config" / config_filename
        self.config: Dict[str, Any] = {}
        # Stores resolved absolute paths
        self.paths: Dict[str, Path] = {} 
        
        self.load_config()

    def _walk_up_for_markers(self, start: Path) -> Optional[Path]:
        """Walk up from ``start`` looking for a project marker. Returns None if none found."""
        current = start.resolve()
        for _ in range(10):  # Max depth search (10 levels up)
            for marker in self.root_markers:
                if (current / marker).exists():
                    return current
            # Also accept a 'config' folder holding our config file
            if (current / "config" / self.config_filename).exists():
                return current
            if current.parent == current:  # Reached filesystem root
                break
            current = current.parent
        return None

    def _detect_project_root(self) -> Path:
        """Resolve the project root deterministically, independent of the CWD (US-098).

        Precedence:
          1. ``AITAO_HOME`` env var — explicit override (packaged installs, tests).
          2. Markers found by walking up from the **current working directory**
             (running from inside a checkout).
          3. Markers found by walking up from the **code location** (``__file__``)
             — CWD-independent; works when launched from anywhere / site-packages.
          4. Safe neutral fallback ``~/.aitao`` — **never the bare CWD**, so a run
             from an unrelated directory cannot scatter a ``data/`` skeleton there.
        """
        # 1. Explicit override (highest precedence)
        env_home = os.environ.get("AITAO_HOME")
        if env_home:
            return Path(env_home).expanduser().resolve()

        # 2. From the working directory (running from within the project)
        from_cwd = self._walk_up_for_markers(Path(os.getcwd()))
        if from_cwd is not None:
            return from_cwd

        # 3. From the code location (stable regardless of where we are launched)
        from_code = self._walk_up_for_markers(Path(__file__).resolve().parent)
        if from_code is not None:
            return from_code

        # 4. Neutral fallback — never the CWD (would create stray dirs in the working dir)
        return Path.home() / ".aitao"

    def load_config(self):
        """Loads and parses the configuration (YAML or TOML)."""
        if not self.config_path.exists():
            # Silently skip if not found - ConfigManager is the primary config source in V2
            return

        # Determine file type
        suffix = self.config_path.suffix.lower()
        
        if suffix in (".yaml", ".yml"):
            if not yaml:
                return  # Silently skip if yaml not available
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self.config = yaml.safe_load(f) or {}
            except Exception as e:
                print(f"❌ PathManager: Error loading YAML config: {e}")
        elif suffix == ".toml":
            if not tomllib:
                print("❌ PathManager Error: No TOML parser found (tomllib, tomli, or toml required).")
                return
            try:
                with open(self.config_path, "r" if hasattr(tomllib, "load") and tomllib.__name__ == "toml" else "rb") as f:
                    self.config = tomllib.load(f)
            except Exception as e:
                print(f"❌ PathManager: Error loading config: {e}")

    def resolve_path(self, path_str: str, context_vars: Dict[str, str] = None) -> Path:
        """
        Resolves a path string into an absolute Path object.
        Supports:
        - Environment variable substitution ($VAR or ${VAR})
        - Tilde expansion (~/...)
        - Custom variable substitution via context_vars
        """
        if not path_str:
            return Path(".")

        expanded_str = path_str
        
        # 1. Substitute environment variables first ($HOME, $USER, etc.)
        # Pattern: $VAR or ${VAR}
        env_pattern = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)')
        def replace_env(match):
            var_name = match.group(1) or match.group(2)
            val = os.environ.get(var_name)
            # Cross-platform home: on Windows HOME is usually unset (USERPROFILE holds
            # the home dir). Fall back to the OS-resolved home so ${HOME}/${USERPROFILE}
            # never leak as a literal into a path (US-098).
            if val is None and var_name in ("HOME", "USERPROFILE"):
                val = str(Path.home())
            return val if val is not None else match.group(0)  # Keep original if unknown
        expanded_str = env_pattern.sub(replace_env, expanded_str)
        
        # 2. Substitute custom variables if context provided (takes precedence over env)
        if context_vars:
            for key, val in context_vars.items():
                if val:
                    pattern = re.compile(re.escape(f"${key}") + r"|" + re.escape(f"${{{key}}}") )
                    expanded_str = pattern.sub(str(val), expanded_str)

        # 3. Expand User (~)
        expanded_str = os.path.expanduser(expanded_str)
        
        # 4. Resolve absolute
        return Path(expanded_str).resolve()

    def get_config_value(self, section: str, key: str, default: Any = None) -> Any:
        """Safely retrieves a value from the config dict."""
        return self.config.get(section, {}).get(key, default)

    # --- API / Network Utilities ---

    def get_api_route(self, 
                      host_key: str, 
                      port_key: str, 
                      endpoint: str = "", 
                      section: str = "server", 
                      default_host: str = "127.0.0.1", 
                      default_port: int = 8000,
                      protocol: str = "http") -> str:
        """
        Constructs a full API URL.
        Example: http://localhost:8000/v1/models
        """
        host = self.get_config_value(section, host_key, default_host)
        port = self.get_config_value(section, port_key, default_port)
        
        # Clean endpoint syntax
        if endpoint and not endpoint.startswith("/"):
            endpoint = f"/{endpoint}"
            
        return f"{protocol}://{host}:{port}{endpoint}"
