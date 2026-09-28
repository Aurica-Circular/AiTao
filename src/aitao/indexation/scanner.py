# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""Filesystem Scanner: discovers new/modified files for indexing via recursive
traversal, pattern exclusions, mtime+SHA256 change detection, and state persistence.
"""

import os
import hashlib
import json
import unicodedata
from pathlib import Path
from datetime import datetime
from typing import Callable, Optional, List, Dict, Set, Any
from dataclasses import dataclass, field, asdict

from aitao.core.config import ConfigManager
from aitao.core.logger import get_logger
from aitao.core.pathmanager import path_manager

logger = get_logger("scanner")


def _doc_id(path: str) -> str:
    """Document id of a source path (US-086 orphan reconciliation).

    Must stay byte-for-byte identical to
    ``indexation.indexer_helpers.generate_doc_id`` — the scanner compares ids it
    derives here against the store inventory built from that function. Duplicated
    (a one-line, frozen contract) to keep the heavy indexer chain off the
    scanner's import path so reconciliation works in the lightweight CI gate too.

    US-113 finding (volet 3): this MUST NFC-normalize the path first, exactly
    like ``generate_doc_id``/``normalize_path`` do. Without it, a path read
    back from ``os.scandir()`` on macOS (decomposed NFD form for any accented
    or CJK-compatibility character — see ``indexer_helpers.normalize_path``)
    hashes to a DIFFERENT id than the one actually stored (computed from the
    NFC-normalized path at indexing time). The scanner's reconciliation then
    never finds a match for that file in the store inventory, however many
    times it gets re-indexed — a real installation's worker.log showed the
    same handful of accented/CJK-named files reprocessed ~48 times in 24h,
    on a ~10-minute cycle, while plain-ASCII filenames settled normally.
    """
    return hashlib.sha256(unicodedata.normalize("NFC", path).encode()).hexdigest()


@dataclass
class FileInfo:
    """Information about a discovered file."""
    path: str
    size: int
    mtime: float
    sha256: Optional[str] = None
    extension: str = ""
    
    def __post_init__(self):
        """Extract extension from path."""
        if not self.extension:
            self.extension = Path(self.path).suffix.lower()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FileInfo":
        """Create from dictionary."""
        return cls(**data)


@dataclass
class ScanResult:
    """Result of a filesystem scan."""
    new_files: List[FileInfo] = field(default_factory=list)
    modified_files: List[FileInfo] = field(default_factory=list)
    deleted_paths: List[str] = field(default_factory=list)
    # Files already 'seen' (in state) but missing from the stores — re-enqueued
    # so a one-off index failure is no longer permanent (US-086).
    reconciled_files: List[FileInfo] = field(default_factory=list)
    total_scanned: int = 0
    total_skipped: int = 0
    scan_duration_seconds: float = 0.0

    @property
    def has_changes(self) -> bool:
        """Check if scan found any changes."""
        return bool(
            self.new_files
            or self.modified_files
            or self.deleted_paths
            or self.reconciled_files
        )


class FilesystemScanner:
    """Scans configured paths to discover documents with incremental state tracking."""
    
    # Default supported extensions for document indexing
    DEFAULT_EXTENSIONS = {
        # Documents
        ".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt",
        # Spreadsheets
        ".xls", ".xlsx", ".ods", ".csv",
        # Presentations
        ".ppt", ".pptx", ".odp",
        # Images (for OCR)
        ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".webp",
        # Markdown and text
        ".md", ".markdown", ".rst", ".tex",
        # eBooks
        ".epub", ".mobi",
        # Web
        ".html", ".htm",
    }
    
    def __init__(
        self,
        config_path: Optional[str] = None,
        state_file: Optional[str] = None,
        index_inventory: Optional["Callable[[], Set[str]]"] = None,
    ):
        """Initialize the scanner with optional config_path and state_file.

        ``index_inventory`` is an optional callable returning the set of doc ids
        currently present in the stores. When supplied, the scanner reconciles
        its 'seen' state against it: a file recorded in state but absent from the
        stores is re-enqueued (US-086). Left ``None`` (the default), scanning
        behaves exactly as before — reconciliation is opt-in and injected, so the
        scanner stays store-agnostic.
        """
        # Oracle of indexed doc ids for orphan reconciliation (US-086).
        self._index_inventory = index_inventory
        # Transient per-scan reconciliation state (set in scan(), used in the
        # recursive walk, cleared afterwards). None => reconciliation off.
        self._scan_indexed_ids: Optional[Set[str]] = None
        self._scan_doc_id_fn: Optional[Callable[[str], str]] = None
        # Load configuration
        if config_path:
            self.config = ConfigManager(config_path)
        else:
            # Use PathManager for config discovery
            config_file = path_manager.root / "config" / "config.toml"
            self.config = ConfigManager(str(config_file))
        
        indexing = self.config.indexing

        # Include paths come from THIS scanner's (possibly injected) config; PathManager
        # is the resolver (US-098 A4) — it expands ${HOME}/${storage_root}. We keep the
        # not-mounted warning here.
        self.include_paths: List[Path] = []
        for path_str in path_manager.resolve_include_paths(indexing.include_paths, existing_only=False):
            path = Path(path_str)
            if path.exists():
                self.include_paths.append(path)
            else:
                logger.warning(f"Include path does not exist: {path}")

        # Exclude patterns
        self.exclude_dirs: Set[str] = set(indexing.exclude_dirs)
        self.exclude_files: Set[str] = set(indexing.exclude_files)
        self.exclude_extensions: Set[str] = set(indexing.exclude_extensions)

        # Supported extensions (can be overridden in config)
        custom_extensions = indexing.supported_extensions
        if custom_extensions:
            self.supported_extensions = set(custom_extensions)
        else:
            self.supported_extensions = self.DEFAULT_EXTENSIONS.copy()
        
        # State file for tracking previously scanned files
        if state_file:
            self.state_file = Path(state_file)
        else:
            # Use PathManager for state file location
            self.state_file = path_manager.get_scanner_state_file()
        
        # Load previous state
        self._file_state: Dict[str, Dict[str, Any]] = {}
        self._load_state()
        
        logger.info(
            "Scanner initialized",
            metadata={
                "include_paths": [str(p) for p in self.include_paths],
                "exclude_dirs": len(self.exclude_dirs),
                "supported_extensions": len(self.supported_extensions)
            }
        )
    
    def _load_state(self) -> None:
        """Load previous scan state from file."""
        if self.state_file.exists():
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._file_state = data.get("files", {})
                    logger.debug(
                        "Loaded scanner state",
                        metadata={"tracked_files": len(self._file_state)}
                    )
            except Exception as e:
                logger.warning(f"Failed to load scanner state: {e}")
                self._file_state = {}
    
    def _save_state(self) -> None:
        """Save current scan state to file."""
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump({
                    "version": 1,
                    "updated_at": datetime.now().isoformat(),
                    "files": self._file_state
                }, f, indent=2)
            logger.debug(f"Saved scanner state: {len(self._file_state)} files")
        except Exception as e:
            logger.error(f"Failed to save scanner state: {e}")
    
    def _should_skip_dir(self, dir_name: str) -> bool:
        """Check if directory should be skipped.

        Exact name match, plus suffix match for dotted patterns only
        (``.egg-info`` → ``aitao.egg-info``, ``.dist-info`` →
        ``aitao-2.8.1.dist-info``). Suffix matching is restricted to dotted
        patterns so generic names like ``build`` cannot wrongly exclude
        ``rebuild`` — US-19.
        """
        # Skip hidden directories
        if dir_name.startswith("."):
            return True
        lowered = dir_name.lower()
        for pattern in self.exclude_dirs:
            p = pattern.lower()
            if lowered == p:
                return True
            if p.startswith(".") and lowered.endswith(p):
                return True
        return False
    
    def _should_skip_file(self, file_path: Path) -> bool:
        """Check if file should be skipped."""
        name = file_path.name
        
        # Skip hidden files
        if name.startswith("."):
            return True
        
        # Skip excluded file names
        if name in self.exclude_files:
            return True
        
        # Skip excluded extensions
        ext = file_path.suffix.lower()
        if ext in self.exclude_extensions:
            return True
        
        # Skip unsupported extensions
        if ext not in self.supported_extensions:
            return True
        
        return False
    
    def _compute_hash(self, file_path: Path, chunk_size: int = 65536) -> str:
        """Compute SHA256 hash of file using chunked reading."""
        sha256 = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                while chunk := f.read(chunk_size):
                    sha256.update(chunk)
            return sha256.hexdigest()
        except (IOError, OSError) as e:
            logger.warning(f"Cannot hash file {file_path}: {e}")
            return ""
    
    def _get_file_info(self, file_path: Path, compute_hash: bool = True) -> FileInfo:
        """Get information about a file."""
        stat = file_path.stat()
        
        file_info = FileInfo(
            path=str(file_path),
            size=stat.st_size,
            mtime=stat.st_mtime,
            extension=file_path.suffix.lower()
        )
        
        if compute_hash:
            file_info.sha256 = self._compute_hash(file_path)
        
        return file_info
    
    def _is_file_modified(self, file_path: Path, current_mtime: float) -> bool:
        """Check if file was modified since last scan (mtime-based)."""
        path_str = str(file_path)
        
        if path_str not in self._file_state:
            return True  # New file
        
        previous = self._file_state[path_str]
        
        # Quick mtime check
        if previous.get("mtime") != current_mtime:
            return True

        return False

    def _prepare_reconciliation(self) -> None:
        """Snapshot indexed doc ids for this scan (US-086 orphan reconciliation).

        Sets ``_scan_indexed_ids`` / ``_scan_doc_id_fn`` when an inventory is
        injected AND returns a non-empty set; otherwise leaves reconciliation
        disabled (None). An empty snapshot is deliberately treated as
        "unavailable" — re-enqueuing everything on a cold or broken store would
        be worse than skipping reconciliation for one cycle.
        """
        self._scan_indexed_ids = None
        self._scan_doc_id_fn = None
        # getattr default tolerates scanners built via __new__ (test doubles).
        inventory = getattr(self, "_index_inventory", None)
        if inventory is None:
            return
        try:
            snapshot = inventory()
        except Exception as e:
            logger.warning(f"Index inventory unavailable, skipping reconciliation: {e}")
            return
        if not snapshot:
            return
        self._scan_indexed_ids = snapshot
        self._scan_doc_id_fn = _doc_id

    def scan(
        self,
        paths: Optional[List[str]] = None,
        compute_hashes: bool = True,
        save_state: bool = True
    ) -> ScanResult:
        """Scan filesystem for new and modified files, return ScanResult."""
        import time
        start_time = time.time()
        
        result = ScanResult()
        current_files: Set[str] = set()
        
        # Determine paths to scan
        scan_paths = []
        if paths:
            for p in paths:
                path = Path(p).expanduser()
                if path.exists():
                    scan_paths.append(path)
                else:
                    logger.warning(f"Path does not exist: {p}")
        else:
            scan_paths = self.include_paths
        
        if not scan_paths:
            logger.warning("No valid paths to scan")
            return result
        
        logger.info(
            "Starting scan",
            metadata={"paths": [str(p) for p in scan_paths]}
        )

        # Prepare orphan reconciliation (US-086): snapshot the indexed doc ids
        # once. An EMPTY snapshot is treated as "unavailable" (cold start or a
        # failed store query) and disables reconciliation, so a transient store
        # outage can never re-enqueue the entire corpus.
        self._prepare_reconciliation()

        # Scan each path
        for base_path in scan_paths:
            self._scan_directory(
                base_path,
                result,
                current_files,
                compute_hashes
            )
        
        # Find deleted files — restricted to roots that exist NOW (US-28a
        # guard): an unmounted cloud volume (kDrive, pCloud…) must not turn
        # its whole tree into "deleted" files. Files under a missing root
        # keep their state and are reconciled when the volume comes back.
        live_roots = [str(p) for p in scan_paths if p.exists()]
        previous_files = set(self._file_state.keys())
        deleted = {
            path
            for path in previous_files - current_files
            if any(
                path == root or path.startswith(root.rstrip("/") + "/")
                for root in live_roots
            )
        }
        result.deleted_paths = list(deleted)

        # Remove deleted files from state
        for path in deleted:
            del self._file_state[path]

        # US-28a — deleted files enter the trash HERE, in the scanner: every
        # scan path (worker daemon, CLI `scan run`, API) consumes the deletion
        # event, and whoever consumes it must record it or it is lost forever.
        # Dry runs (save_state=False) stay side-effect free.
        if deleted and save_state:
            try:
                from aitao.indexation.trash import get_trash_registry
                get_trash_registry().mark(sorted(deleted))
            except Exception as e:
                logger.warning(f"Could not mark deleted files in trash: {e}")
        
        # Calculate duration
        result.scan_duration_seconds = time.time() - start_time

        # Save state
        if save_state:
            self._save_state()

        # Clear transient reconciliation snapshot (do not leak across scans).
        self._scan_indexed_ids = None
        self._scan_doc_id_fn = None

        logger.info(
            "Scan complete",
            metadata={
                "new": len(result.new_files),
                "modified": len(result.modified_files),
                "reconciled": len(result.reconciled_files),
                "deleted": len(result.deleted_paths),
                "total_scanned": result.total_scanned,
                "skipped": result.total_skipped,
                "duration_s": round(result.scan_duration_seconds, 2)
            }
        )
        
        return result
    
    def _scan_directory(
        self,
        directory: Path,
        result: ScanResult,
        current_files: Set[str],
        compute_hashes: bool
    ) -> None:
        """Recursively scan a directory."""
        try:
            for entry in os.scandir(directory):
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if not self._should_skip_dir(entry.name):
                            self._scan_directory(
                                Path(entry.path),
                                result,
                                current_files,
                                compute_hashes
                            )
                    elif entry.is_file(follow_symlinks=False):
                        file_path = Path(entry.path)
                        
                        if self._should_skip_file(file_path):
                            result.total_skipped += 1
                            continue
                        
                        result.total_scanned += 1
                        path_str = str(file_path)
                        current_files.add(path_str)
                        
                        # Check modification
                        stat = entry.stat()
                        mtime = stat.st_mtime
                        
                        if path_str not in self._file_state:
                            # New file
                            file_info = self._get_file_info(
                                file_path, compute_hashes
                            )
                            result.new_files.append(file_info)
                            self._file_state[path_str] = file_info.to_dict()
                            
                        elif self._is_file_modified(file_path, mtime):
                            # Modified file
                            file_info = self._get_file_info(
                                file_path, compute_hashes
                            )
                            result.modified_files.append(file_info)
                            self._file_state[path_str] = file_info.to_dict()

                        elif (
                            self._scan_indexed_ids is not None
                            and self._scan_doc_id_fn(path_str)
                            not in self._scan_indexed_ids
                        ):
                            # Orphan (US-086): already 'seen' and unchanged, yet
                            # absent from the stores -> a past index failed. Re-
                            # enqueue WITHOUT touching state (the file itself did
                            # not change); it keeps recurring until it lands.
                            file_info = self._get_file_info(
                                file_path, compute_hashes
                            )
                            result.reconciled_files.append(file_info)


                except PermissionError:
                    logger.debug(f"Permission denied: {entry.path}")
                except Exception as e:
                    logger.warning(f"Error processing {entry.path}: {e}")
                    
        except PermissionError:
            logger.debug(f"Permission denied: {directory}")
        except Exception as e:
            logger.warning(f"Error scanning directory {directory}: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get scanner statistics."""
        return {
            "include_paths": [str(p) for p in self.include_paths],
            "tracked_files": len(self._file_state),
            "supported_extensions": len(self.supported_extensions),
            "exclude_dirs": len(self.exclude_dirs),
            "state_file": str(self.state_file)
        }
    
    def clear_state(self) -> None:
        """Clear the scanner state (force full rescan)."""
        self._file_state = {}
        if self.state_file.exists():
            self.state_file.unlink()
        logger.info("Scanner state cleared")
