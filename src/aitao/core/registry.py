# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Central Interface Registry for AiTao.

This module provides a single source of truth for all shared interfaces,
function signatures, and data structures. It prevents bugs caused by:
- Inconsistent attribute names (e.g., result.error vs result.error_message)
- Mismatched function parameters across modules
- Hardcoded values that should be centralized

Usage:
    from aitao.core.registry import ConfigKeys, StatsKeys, Task, TaskStatus

Design Principle (AC-005):
    If a *constant* or shared key set is used by more than one module, it MUST
    be defined here. Validated domain entities (Document, SearchResult, …) live
    in core/models.py and the per-layer model modules (US-23), not here.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


# ============================================================================
# Configuration Keys (prevent typos in config access)
# ============================================================================

class ConfigKeys:
    """
    Centralized config.toml key paths.
    
    Usage:
        config.get(ConfigKeys.STORAGE_ROOT)
        config.get(ConfigKeys.LANCEDB_PATH)
    """
    # Paths
    STORAGE_ROOT = "paths.storage_root"
    LANCEDB_PATH = "paths.lancedb_path"
    QUEUE_PATH = "paths.queue_path"
    LOGS_DIR = "paths.logs_dir"
    MODELS_DIR = "paths.models_dir"
    
    # Search - Meilisearch
    MEILISEARCH_HOST = "search.meilisearch.host"
    MEILISEARCH_API_KEY = "search.meilisearch.api_key"  # config key path, not a secret — pragma: allowlist secret
    MEILISEARCH_INDEX = "search.meilisearch.index_name"
    
    # Search - LanceDB
    LANCEDB_TABLE = "search.lancedb.table_name"
    EMBEDDING_MODEL = "search.lancedb.embedding_model"
    OFFLINE_MODE = "search.lancedb.offline_mode"
    
    # Search - Hybrid weights
    MEILISEARCH_WEIGHT = "search.hybrid_search.meilisearch_weight"
    LANCEDB_WEIGHT = "search.hybrid_search.lancedb_weight"
    
    # Worker
    WORKER_POLL_INTERVAL = "indexing.worker.poll_interval"
    WORKER_CPU_THRESHOLD = "indexing.worker.cpu_threshold"
    WORKER_BATCH_SIZE = "indexing.worker.batch_size"
    
    # API
    API_HOST = "api.host"
    API_PORT = "api.port"
    API_CORS_ORIGINS = "api.cors_origins"
    
    # LLM
    OLLAMA_HOST = "llm.ollama.host"
    OLLAMA_DEFAULT_MODEL = "llm.ollama.default_model"
    LLM_DEFAULT_MODEL = "llm.default_model"       # Simplified key (US-051)
    RAG_ENABLED = "llm.rag.enabled"
    RAG_MAX_CONTEXT_DOCS = "llm.rag.max_context_docs"
    LLM_MODELS = "llm.models"


# ============================================================================
# Statistics Keys (prevent document_count vs total_documents bugs)
# ============================================================================

class StatsKeys:
    """
    Centralized keys for statistics dictionaries.
    
    Prevents bugs like:
    - Producer returns {"total_documents": 100}
    - Consumer expects {"document_count": 100}
    
    Usage:
        from aitao.core.registry import StatsKeys
        
        # Producer (lancedb_client.py, meilisearch_client.py)
        return {StatsKeys.TOTAL_DOCUMENTS: count}
        
        # Consumer (cli/commands/index.py)
        count = stats.get(StatsKeys.TOTAL_DOCUMENTS, 0)
    """
    # Document/Chunk counts
    TOTAL_DOCUMENTS = "total_documents"
    TOTAL_CHUNKS = "total_chunks"
    UNIQUE_DOCUMENTS = "unique_documents"
    
    # Database info
    TABLE_NAME = "table_name"
    INDEX_NAME = "index_name"
    DB_PATH = "db_path"
    
    # Server/Connection
    HOST = "host"
    URL = "url"
    IS_INDEXING = "is_indexing"
    
    # Embedding/Vector
    EMBEDDING_DIMENSION = "embedding_dimension"
    EMBEDDING_MODEL = "embedding_model"
    
    # Size info
    TOTAL_SIZE_BYTES = "total_size_bytes"
    TOTAL_SIZE_MB = "total_size_mb"
    
    # Categories/Languages distribution
    CATEGORIES = "categories"
    LANGUAGES = "languages"
    FIELD_DISTRIBUTION = "field_distribution"


# ============================================================================
# Task & Queue Structures
# ============================================================================

class TaskStatus(str, Enum):
    """Task status values (must match queue.py)."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskPriority(str, Enum):
    """Task priority levels."""
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class TaskType(str, Enum):
    """Types of indexing tasks."""
    INDEX = "index"
    REINDEX = "reindex"
    DELETE = "delete"
    OCR = "ocr"
    TRANSLATE = "translate"


@dataclass
class Task:
    """
    Task structure for the indexing queue.
    
    Canonical definition - all modules must use this structure.
    """
    id: str
    file_path: str
    task_type: TaskType = TaskType.INDEX
    priority: TaskPriority = TaskPriority.NORMAL
    status: TaskStatus = TaskStatus.PENDING
    added_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None  # NOTE: 'error' not 'error_message'
    retries: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


# ============================================================================
# Health Check Structures
# ============================================================================

class ServiceStatus(str, Enum):
    """Service health status."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"
    UNKNOWN = "unknown"


@dataclass
class ServiceHealth:
    """
    Health status of a single service.
    """
    name: str
    status: ServiceStatus
    message: str = ""
    latency_ms: Optional[float] = None


@dataclass
class SystemHealth:
    """
    Overall system health.
    """
    status: ServiceStatus
    version: str
    services: Dict[str, ServiceHealth]
    timestamp: datetime = field(default_factory=datetime.now)


# ============================================================================
# API Endpoints Registry
# ============================================================================

class APIEndpoints:
    """
    Registry of API endpoints.
    
    Prevents hardcoding endpoint paths in multiple places.
    """
    # Core API
    HEALTH = "/api/health"              # Fast minimal check (API responding?)
    HEALTH_DEBUG = "/api/health/debug"  # Slow detailed diagnostics (all services)
    STATS = "/api/stats"
    SEARCH = "/api/search"
    INGEST = "/api/ingest"
    
    # Ollama-compatible
    CHAT = "/api/chat"
    GENERATE = "/api/generate"
    TAGS = "/api/tags"
    EMBEDDINGS = "/api/embeddings"
    
    # OpenAI-compatible
    V1_CHAT = "/v1/chat/completions"
    V1_MODELS = "/v1/models"
    V1_EMBEDDINGS = "/v1/embeddings"


# ============================================================================
# Default Values
# ============================================================================

class Defaults:
    """
    Default values that should be consistent across modules.
    """
    # Timeouts
    HTTP_TIMEOUT = 30  # seconds
    WORKER_POLL_INTERVAL = 30  # seconds
    API_SHUTDOWN_TIMEOUT = 10  # seconds
    
    # Limits
    MAX_SEARCH_RESULTS = 100
    MAX_BATCH_SIZE = 50
    MAX_RETRIES = 3
    
    # Ports
    API_PORT = 5000
    MEILISEARCH_PORT = 7700
    OLLAMA_PORT = 11434
    
    # Models
    EMBEDDING_MODEL = "BAAI/bge-m3"
    LLM_MODEL = "qwen2.5-coder:7b"
    
    # Search weights
    MEILISEARCH_WEIGHT = 0.4
    LANCEDB_WEIGHT = 0.6


# ============================================================================
# LLM Model Management Structures (AC-005)
# ============================================================================

class ModelRole(str, Enum):
    """
    Role/purpose of a model — 3 user-declarable roles only (ADR-001, US-051).

    - CHAT:   General conversation + RAG (default when no roles declared).
    - CODE:   Code assistance — RAG intentionally disabled.
    - VISION: Multimodal (images/PDFs). Also used by AiTao internally for
              Premium OCR — the user never needs to configure an "ocr" role.

    Roles that were removed (and where they live instead):
    - ocr    → internal Premium behaviour, auto-triggered when vision model present
    - embed  → [search.lancedb] embedding_model
    - translate → [translation] section
    - rag    → not a role; RAG is on by default for chat/vision, off for code
    """
    CHAT = "chat"
    CODE = "code"
    VISION = "vision"


@dataclass
class ModelInfo:
    """
    Configuration for a single LLM model.
    
    Canonical structure for model configuration and verification.
    """
    name: str                           # Model name in Ollama (e.g., "llama3.1:8b")
    required: bool = False              # Blocks startup if missing
    size_gb: Optional[float] = None     # Size info for user (e.g., 4.7)
    roles: List[ModelRole] = field(default_factory=list)  # Usage: chat, code, vision, etc.
    description: str = ""               # User-friendly description


@dataclass
class ModelStatus:
    """
    Status of models (present/missing/extra).
    """
    present: List[str]                  # Models configured AND installed
    missing: List[str]                  # Models configured BUT not installed
    extra: List[str]                    # Models installed BUT not configured
    required_missing: List[str]         # Critical: required models that are missing


# ============================================================================
# Singleton Access Functions
# ============================================================================

def get_config():
    """
    Get the global ConfigManager instance.
    
    This is the ONLY way to access config. Never instantiate ConfigManager directly.
    See AC-001 in PRD.
    """
    from .config import get_config as _get_config
    return _get_config()


def get_logger(name: str):
    """
    Get a structured logger instance.
    
    All modules should use this, never use print() in production.
    See AC-003 in PRD.
    """
    from .logger import get_logger as _get_logger
    return _get_logger(name)
