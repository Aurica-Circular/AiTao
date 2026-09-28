# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
config_schema.py — Typed Pydantic v2 models for AiTao configuration.

Each TOML section maps to a Python model with typed fields and defaults.
Top-level entrypoint: Settings — built via Settings.model_validate(merged_dict).
Unknown TOML keys are silently ignored on all models (extra='ignore').
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Base(BaseModel):
    """All config models ignore unknown TOML keys."""
    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------------------
# [app]
# ---------------------------------------------------------------------------

class AppConfig(_Base):
    name: str = "aitao"
    mode: str = "normal"


# ---------------------------------------------------------------------------
# [paths]
# ---------------------------------------------------------------------------

class PathsConfig(_Base):
    storage_root: str = ""
    models_dir: str = ""
    logs_dir: str = ""
    queue_dir: str = ""
    cache_dir: str = ""
    corrections_dir: str = ""
    vector_db_dir: str = ""


# ---------------------------------------------------------------------------
# [identity]
# ---------------------------------------------------------------------------

class IdentityConfig(_Base):
    who_are_you: str = ""
    who_is_aitao: str = ""
    # US-20 / US-128 — reply language. "mirror" (default) or empty = reply in
    # the same language as the user's message. Any other value ("français",
    # "English", "中文"…) forces that language regardless of the question.
    response_language: str = "mirror"


# ---------------------------------------------------------------------------
# [llm] and sub-sections
# ---------------------------------------------------------------------------

class LLMOpenAIConfig(_Base):
    base_url: str = "http://localhost:8080/v1"
    model: str = "local-model"
    api_key: str = ""


class LLMGenerationConfig(_Base):
    temperature: float = 0.7
    top_p: float = 0.9
    max_tokens: int = 2048


class LLMConfig(_Base):
    backend: str = "ollama"
    # US-116: empty is a valid, deliberate value — "auto-select the smallest
    # installed model that is safe for RAG chat" (see
    # llm.model_advisor.pick_safe_default, used by OllamaClient/
    # OpenAICompatClient when the configured/requested model is missing).
    # No hardcoded model name belongs here: nothing guarantees it is
    # installed on a given machine (incident of 2026-07-20 — a config
    # rebuilt from the template pointed at an uninstalled model and broke
    # chat outright).
    default_model: str = ""
    ollama_url: str = "http://localhost:11434"
    # request_timeout / timeout: flat keys used by clients. 12B-class local
    # models with a RAG-enriched prompt and multi-turn history routinely need
    # more than 120 s for the first token on consumer hardware (US-17c).
    request_timeout: float = 300.0
    timeout: float = 300.0
    # US-87 p2: max seconds of SILENCE (no new token) during streaming before the
    # stream is cut. Resets on every token, so a slow-but-producing model is never
    # cut — only a true stall (e.g. a model that never emits a token). Applies to
    # streaming only; non-streaming keeps request_timeout.
    stream_idle_timeout: float = 90.0
    # optional list of model definitions (extended configs)
    models: List[dict] = Field(default_factory=list)
    openai: LLMOpenAIConfig = Field(default_factory=LLMOpenAIConfig)
    generation: LLMGenerationConfig = Field(default_factory=LLMGenerationConfig)


# ---------------------------------------------------------------------------
# [indexing]
# ---------------------------------------------------------------------------

class IndexingConfig(_Base):
    enabled: bool = True
    interval_minutes: int = 60
    include_paths: List[str] = Field(default_factory=list)
    exclude_dirs: List[str] = Field(
        # Build/VCS artifacts must never be indexed (US-19: egg-info/top_level.txt
        # was indexed and ranked first on a PRD query).
        default_factory=lambda: [
            ".git", ".DS_Store", "__pycache__",
            ".egg-info", ".dist-info", "node_modules", ".venv", "venv",
            ".mypy_cache", ".pytest_cache", ".ruff_cache",
        ]
    )
    exclude_files: List[str] = Field(default_factory=list)
    exclude_extensions: List[str] = Field(default_factory=list)
    supported_extensions: Optional[List[str]] = None
    # US-28b — days a deleted file stays in the trash before definitive purge
    trash_retention_days: int = 30
    # ÉPIC-31 (US-111, absorbs US-093) — worker batch size: the number of files
    # accumulated before ONE combined Meilisearch write (documents + chunks)
    # instead of one blocking add+wait per file. Default is DELIBERATELY 1 (=
    # today's per-file behaviour), not the ~16 the original US-093 note
    # suggested: batching is opt-in via config, matching Phil's documented
    # preference for deterministic config over implicit defaults, and it keeps
    # every existing worker test (which builds a real ConfigManager from the
    # committed config.toml) on the unchanged single-file path with zero edits.
    # Set e.g. batch_size = 16 in config.toml to enable batching in production.
    batch_size: int = 1
    # US-086 v3 / US-113 (ÉPIC-31 volet 3): how many times a file that keeps
    # failing to index may be re-enqueued by scanner reconciliation before it
    # is left alone (surfaced via the failed-files tracker for manual
    # inspection) instead of being retried forever. Previously a hard-coded
    # constant in indexation/worker.py; promoted to config so it can be tuned
    # without a code change. Does not affect files that succeed — those are
    # never in the failed-files list, so this ceiling never applies to them.
    max_reconciliation_retries: int = 3
    # US-113 (ÉPIC-31 volet 2): max seconds a single file's text extraction
    # (incl. OCR) may run before it is aborted and the task fails with an
    # explicit "extraction timeout" error, letting the worker move on to the
    # next file. Guards against a pathological input (e.g. a vector-only PDF
    # with no text layer, or a cloud-storage placeholder file that blocks on
    # I/O while being materialized) freezing the whole worker — measured once
    # for 1h22 with zero CPU usage (a blocking wait, not a slow computation).
    extraction_timeout_s: int = 300
    # US-126-C: opt-in, OFF by default. When true, every periodic worker scan
    # also runs the out-of-scope prune (US-126-A logic) so documents whose
    # path fell outside every include_paths root get removed automatically.
    # Config-driven only — never based on disk presence, so a demounted-but-
    # still-configured volume is never touched. See indexation/prune_runner.py.
    auto_prune_out_of_scope: bool = False


# ---------------------------------------------------------------------------
# [search] and sub-sections
# ---------------------------------------------------------------------------

class MeilisearchConfig(_Base):
    url: str = "http://localhost:7700"
    api_key: str = ""
    index_name: str = "aitao_documents"
    # ÉPIC-31 (US-110) — dedicated index for the chunk/excerpt fusion stage
    # (userProvided embedder, dim = search.embedding.dimension, same bge-m3
    # vectors). Same test-isolation pattern as index_name: tests override with
    # a "test_" prefix (see tests/golden/corpus_fixture.py).
    chunks_index: str = "aitao_chunks"
    searchable_attributes: List[str] = Field(default_factory=list)
    displayed_attributes: List[str] = Field(default_factory=list)
    filterable_attributes: List[str] = Field(default_factory=list)


class EmbeddingConfig(_Base):
    """Shared bge-m3 embedding identity (ÉPIC-31, US-113).

    Used by BOTH Meilisearch indexes (documents + chunks) for their
    userProvided embedder — model name and vector dimension only. Moved out
    of the former ``[search.lancedb]`` section (which described a real
    LanceDB connection before v4.0; LanceDB is no longer a live store) into
    this neutral name. A config still setting the old
    ``[search.lancedb] embedding_model``/``dimension``/``offline_mode`` keys
    keeps working — see ``SearchConfig._fallback_legacy_embedding_keys``.
    """
    embedding_model: str = "BAAI/bge-m3"
    dimension: int = 1024
    offline_mode: bool = False


class LanceDBConfig(_Base):
    """Legacy LanceDB settings (ÉPIC-31, US-113): the live search engine no
    longer reads this section at all. Kept ONLY so ``search.migrate_v4``
    knows which table to read from the pre-4.0 store on disk — see
    ``docs/MIGRATION-4.0.md``. ``embedding_model``/``dimension``/
    ``offline_mode`` moved to ``[search.embedding]``; kept here too (read as a
    fallback, never written) so an existing config.toml with the old keys
    under ``[search.lancedb]`` is not silently ignored.
    """
    table_name: str = "aitao_embeddings"
    embedding_model: Optional[str] = None
    dimension: Optional[int] = None
    offline_mode: Optional[bool] = None


class HybridSearchConfig(_Base):
    enable_query_expansion: bool = True


class SearchConfig(_Base):
    # Hybrid semanticRatio, PER STAGE and INDEPENDENT (US-106 volets 2/2bis: the
    # optimal ratio does not transpose from one stage to the other — 0.8 breaks
    # exact-word recall at the document stage only, while being ~neutral at the
    # excerpt stage). Fusion is the only engine since v4.0 (US-113 removed the
    # dev-only "[search] engine" rrf/fusion toggle — decision D1).
    semantic_ratio_chunks: float = 0.5
    semantic_ratio_documents: float = 0.5
    meilisearch: MeilisearchConfig = Field(default_factory=MeilisearchConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    lancedb: LanceDBConfig = Field(default_factory=LanceDBConfig)
    hybrid: HybridSearchConfig = Field(default_factory=HybridSearchConfig)

    @model_validator(mode="before")
    @classmethod
    def _tolerate_legacy_settings(cls, data: Any) -> Any:
        """US-113: the v3.x "[search] engine" flag disappears (fusion is the
        only engine). A config.toml still setting it (this machine's does)
        must NOT crash — ``extra="ignore"`` already drops it silently, this
        adds a soft warning so the operator notices and can clean it up
        whenever convenient. Also back-fills ``[search.embedding]`` from the
        legacy ``[search.lancedb]`` keys when the new section is absent, so
        an existing config keeps its configured model/dimension unchanged.
        """
        if not isinstance(data, dict):
            return data
        if "engine" in data:
            logging.getLogger("aitao.config").warning(
                "[search] engine=\"%s\" is ignored: since v4.0 fusion is the "
                "only search engine (no more \"rrf\" fallback). Remove this "
                "key from config.toml — it has no effect.",
                data.get("engine"),
            )
        legacy = data.get("lancedb")
        if isinstance(legacy, dict) and "embedding" not in data:
            fallback = {
                k: legacy[k] for k in ("embedding_model", "dimension", "offline_mode")
                if legacy.get(k) is not None
            }
            if fallback:
                data = {**data, "embedding": fallback}
        return data


# ---------------------------------------------------------------------------
# [rag]
# ---------------------------------------------------------------------------

class RAGConfig(_Base):
    enabled: bool = True
    use_chunks: bool = True
    max_context_chunks: int = 5
    max_context_docs: int = 5
    context_max_tokens: int = 4000
    min_relevance_score: float = 0.3
    include_metadata: bool = True
    # US-89-1: distill the search query to its salient tokens (rare-in-index +
    # structured) before retrieval, so multilingual function words don't pollute
    # the embedding. Set false to search with the raw question.
    distill_query: bool = True
    # A latin word is "frequent" (dropped) when it appears in more than this
    # fraction of indexed documents. Lower = more aggressive pruning.
    distill_max_doc_ratio: float = 0.15
    # US-89-2: a distinctive token (email, ID, quoted, CJK run) found verbatim in
    # the question pins EVERY document that contains it, so "where does this email
    # appear?" lists all sources. pin_max_docs caps how many are pinned (protects
    # the context budget). Set pin_exact_token false to disable.
    pin_exact_token: bool = True
    pin_max_docs: int = 7
    # US-076: post-generation reliability level. Default "fast" since US-092:
    # the deterministic net is free (~0.1 s) and, with the verbatim digit rule,
    # no longer raises false alarms on correct summaries — reliability is
    # acquired, not optional.
    #   "off"  — no check
    #   "fast" — deterministic check only (Core): factual details (numbers,
    #            dates, amounts) are matched verbatim against the context,
    #            spelled-out facts by embedding similarity, ~60-90 ms
    #   "deep" — fast + an LLM pass (Core, opt-in) that catches "right document,
    #            wrong figure" on claims bearing a number / date / amount
    # Back-compat: a legacy boolean maps true->"fast", false->"off".
    verify_answer: str = "fast"

    # Dedicated model for the "deep" verification pass (US-076 B). The sentinel
    # "defaut" (also "default" or empty) means "reuse the chat model" — we cannot
    # know the user's model, so the template ships this neutral value. Point it at
    # a FAST non-reasoning model (e.g. "granite4") so the deep net does not inherit
    # a reasoning model's 15-36 s "thinking" cost. Ollama-oriented (one endpoint
    # serves many models); on a single-model openai backend the model must be
    # served by that endpoint. Unknown model -> silent fallback to the chat model
    # (the reliability net is never lost on misconfig).
    verify_model: str = "defaut"

    # US-102 (ÉPIC-30 brique 1, étude §6.4 5th invariant): every conversation-
    # dossier decision (referent held/recalled/released) is logged. True (the
    # default during the ÉPIC-30 rollout) logs at INFO so the gain can be
    # measured against real usage; false keeps the same logs at DEBUG instead
    # of silencing them, so the trail never fully disappears.
    reliability_debug: bool = True

    # US-105 (ÉPIC-30 phase 2bis, incident I-16): a small LLM call classifies
    # the question as "documentary" (search the user's documents) or "general"
    # (calculation/reasoning/general knowledge — answer without injecting any
    # document context). Default true: fail-open is documentary on every
    # failure mode (disabled, no model, timeout, unparsable response), so
    # turning this off does not restore any lost safety net — it only stops
    # the LLM call and its (small) latency cost.
    intent_router: bool = True

    # US-104 (ÉPIC-30 phase 3, "lecteur de reponse"): when the deterministic
    # grounding check is about to flag a sentence, a small LLM is asked — in
    # ONE grouped call, ONLY on the would-be-flagged sentences (never on a
    # clean answer) — whether it is a factual affirmation or mere habillage/
    # echo of the document metadata (the rules alone are deliberately narrow
    # to avoid false habillage calls). Default true: this appeal can only
    # REMOVE a flag the deterministic pass already raised, never add one, so
    # turning it off does not restore any lost safety net — it only stops the
    # extra LLM call and its (small) latency cost on flagged answers.
    reader_llm: bool = True

    @field_validator("verify_answer", mode="before")
    @classmethod
    def _coerce_verify_answer(cls, v: object) -> str:
        if isinstance(v, bool):
            return "fast" if v else "off"
        if isinstance(v, str) and v.strip().lower() in ("off", "fast", "deep"):
            return v.strip().lower()
        # Unrecognised value -> the default level, NOT "off": a typo in the
        # config must never silently disable the reliability net (US-076 B
        # principle, applied to the level itself since the US-092 default-on).
        return "fast"


# ---------------------------------------------------------------------------
# [chunking]
# ---------------------------------------------------------------------------

class ChunkingConfig(_Base):
    enabled: bool = True
    chunk_size: int = 512
    chunk_overlap: int = 50
    min_chunk_size: int = 100
    max_chunk_size: int = 1024
    split_on_sentences: bool = True
    embedding_model: str = "BAAI/bge-m3"


# ---------------------------------------------------------------------------
# [worker]
# ---------------------------------------------------------------------------

class WorkerConfig(_Base):
    poll_interval: int = 5
    cpu_threshold: float = 80.0
    stuck_task_timeout: int = 600


# ---------------------------------------------------------------------------
# [ocr] and sub-sections
# ---------------------------------------------------------------------------

class OCRRouterConfig(_Base):
    table_area_min: float = 0.05
    min_intersections: int = 2
    min_line_density: float = 0.0002


class OCRQwenVLConfig(_Base):
    model_path: str = ""
    n_gpu_layers: int = -1
    context_size: int = 4096
    max_tokens: int = 2048


class OCRConfig(_Base):
    # OCR engine selection (distinct from the [llm] backend on purpose).
    #   engine       = "auto" follows engine_order; or a name to force one engine.
    #   engine_order = priority list tried in "auto" mode (qwen_vl is opt-in).
    engine: str = "auto"
    engine_order: Optional[List[str]] = None
    # Candidate languages passed to the OCR engine when the document's language
    # cannot be detected (scanned files yield no text → no detection).
    languages: List[str] = Field(default_factory=lambda: ["fr", "en", "zh-Hant"])
    confidence_threshold: float = 0.7
    # Vision-language model used only by the qwen_vl engine (opt-in).
    vision_model: Optional[str] = None
    router: OCRRouterConfig = Field(default_factory=OCRRouterConfig)
    qwen_vl: OCRQwenVLConfig = Field(default_factory=OCRQwenVLConfig)


# ---------------------------------------------------------------------------
# [translation] and sub-sections
# ---------------------------------------------------------------------------

class TranslationMBart50Config(_Base):
    model_name: str = "facebook/mbart-large-50-many-to-many-mmt"
    use_gpu: bool = False
    batch_size: int = 8


class TranslationNLLBConfig(_Base):
    model_name: str = "facebook/nllb-200-distilled-600M"
    use_gpu: bool = False
    batch_size: int = 8


class TranslationConfig(_Base):
    provider: str = "mbart50"
    source_lang: str = "fr"
    target_lang: str = "zh_TW"
    score_translations: bool = True
    min_confidence: float = 0.6
    mbart50: TranslationMBart50Config = Field(default_factory=TranslationMBart50Config)
    nllb: TranslationNLLBConfig = Field(default_factory=TranslationNLLBConfig)


# ---------------------------------------------------------------------------
# [categories]
# ---------------------------------------------------------------------------

class CategoriesConfig(_Base):
    enabled: bool = True
    default: str = "autre"
    labels: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# [resources]
# ---------------------------------------------------------------------------

class ResourcesConfig(_Base):
    max_workers: int = 4
    batch_size: int = 10
    max_file_size_mb: int = 100
    memory_limit_mb: int = 2048


# ---------------------------------------------------------------------------
# [api] and sub-sections
# ---------------------------------------------------------------------------

class APIRateLimitConfig(_Base):
    enabled: bool = True
    requests_per_minute: int = 60


class APIAuthConfig(_Base):
    enabled: bool = False
    api_key: str = ""


class APIConfig(_Base):
    host: str = "127.0.0.1"
    port: int = 8200
    cors_enabled: bool = True
    cors_origins: List[str] = Field(default_factory=lambda: ["*"])
    rate_limit: APIRateLimitConfig = Field(default_factory=APIRateLimitConfig)
    auth: APIAuthConfig = Field(default_factory=APIAuthConfig)


# ---------------------------------------------------------------------------
# [logger]
# ---------------------------------------------------------------------------

class LoggerConfig(_Base):
    level: str = "info"
    console_pretty: bool = True
    file_json: bool = True
    rotation_enabled: bool = True
    max_file_mb: int = 100
    max_files: int = 5


# ---------------------------------------------------------------------------
# Top-level Settings object
# ---------------------------------------------------------------------------

class Settings(_Base):
    """Fully typed representation of the merged TOML config.

    Built once per reload via Settings.model_validate(merged_dict).
    Access via ConfigManager properties: config.llm, config.search, etc.
    """
    app: AppConfig = Field(default_factory=AppConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    identity: IdentityConfig = Field(default_factory=IdentityConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    indexing: IndexingConfig = Field(default_factory=IndexingConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    rag: RAGConfig = Field(default_factory=RAGConfig)
    chunking: ChunkingConfig = Field(default_factory=ChunkingConfig)
    worker: WorkerConfig = Field(default_factory=WorkerConfig)
    ocr: OCRConfig = Field(default_factory=OCRConfig)
    translation: TranslationConfig = Field(default_factory=TranslationConfig)
    categories: CategoriesConfig = Field(default_factory=CategoriesConfig)
    resources: ResourcesConfig = Field(default_factory=ResourcesConfig)
    api: APIConfig = Field(default_factory=APIConfig)
    # 'logger' in TOML, but 'log' in Python to avoid shadowing ConfigManager.logger
    log: LoggerConfig = Field(default_factory=LoggerConfig, alias="logger")

    model_config = ConfigDict(extra="ignore", populate_by_name=True)
