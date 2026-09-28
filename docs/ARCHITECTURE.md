# AiTao — Architecture

> Technical reference for contributors. Describes the system as it currently is.
> Contains no secrets — no keys, no credentials — and is safe to publish.

AiTao is a **local-first RAG backend**: it indexes a folder of documents, searches them
(full-text + semantic), and lets any local LLM answer **grounded in those documents**
("notary, not oracle"). It runs 100% locally. Two editions share one codebase: **Core**
(AGPL, free) and **Premium** (unlocked by a license key, gated through `LicenseManager`).

---

## 1. High-level layers

```
External clients
  OpenAI-compatible UIs (Open WebUI, IDEs)      MCP clients (Claude Desktop, Cursor)
            │                                              │
            ▼                                              ▼
        ┌─────────────────────── AiTao API (FastAPI, :8200) ───────────────────────┐
        │  Chat / context pipeline  →  Hybrid search  →  LLM provider               │
        └───────────────────────────────────────────────────────────────────────────┘
                                         ▲
                 Ingestion pipeline (scanner → queue → worker → indexer)
                 feeds the indexes (LanceDB vectors + Meilisearch full-text)
```

## 2. Ingestion pipeline (`src/indexation/`)

`Scanner → Queue → Worker → DocumentIndexer`.

- `scanner.py` walks the configured `include_paths` and enqueues new/changed files
  (allowed extensions, with exclude rules).
- `worker.py` / `worker_daemon.py` drain the queue and call `DocumentIndexer.index_file()`.
- `text_extractor.py` defines the extraction interface (`ExtractionResult`, `BaseExtractor`)
  and the `TextExtractor` facade, which dispatches by extension to the extractor plugins in
  `src/plugins/extractors/` (`pdf.py`, `simple_extractors.py` for `.txt/.md/.log/.rst/.tex…`,
  `image_extractor.py`, `exif_extractor.py`); the PDF analysis engine stays in
  `indexation/pdf_extractor.py` as a library. `office_extractors.py` (`.docx/.xlsx/.odt/.pptx…`)
  is not in this repository — like the OCR providers below, it ships in the separately
  distributed **AiTao Premium** module (`aitao-premium/src/aitao_premium/extractors/`,
  discovered via the `aitao.plugins` entry point), which also enforces the licence itself at
  the top of each extractor's `extract()`. With no Premium module installed, `TextExtractor`
  simply has no `.docx`/`.pptx`/`.xlsx`/`.odt` entry — the same "unsupported file type" outcome
  as any other unknown extension, never a crash; the core's `item_preparer.prepare_item()`
  gate (`LicenseManager.is_premium_extension`) still refuses these formats first anyway,
  before extraction is even attempted.
- For scanned PDFs/images, `OCRRouter` (`src/ocr/router.py`) selects the best available
  provider: `macos_vision → tesseract → qwen_vl`. The providers themselves are not in this
  repository — they ship in the separately distributed **AiTao Premium** module
  (`aitao-premium`, discovered via the `aitao.plugins` entry point), which also enforces
  the licence itself at the top of each provider's OCR pass. With no Premium module
  installed, `OCRRouter` has zero providers and OCR is refused the same way an unlicensed
  Core user is refused today (`PremiumFeatureError`), never a crash.
- `index_file()` builds a validated `Document` (`core.models`) and indexes it: the text is
  chunked + embedded into LanceDB and indexed into Meilisearch via each client's
  `index_document(document)`.

## 3. Storage & indexes

- **Meilisearch** (full-text) on `:7700`; index name from `search.meilisearch.index_name`.
- **LanceDB** (semantic vectors); embeddings `BAAI/bge-m3` via `sentence-transformers`,
  computed locally (not through Ollama).
- Chunks for RAG retrieval are persisted alongside the document records.
- **Storage abstraction (`src/storage/`)**: a backend-agnostic `DocumentRepository`
  contract (index / search / fetch / delete / stats) that the search engine and the
  indexer depend on via constructor injection — so a backend can be swapped, or mocked
  in tests, without touching business logic. The two clients fulfil the contract; the
  `make_*_client` / `make_*_repository` factories centralize construction.

## 4. Search (`src/search/`)

`HybridSearchEngine` runs semantic (LanceDB) and full-text (Meilisearch) search in parallel
and merges results with **Reciprocal Rank Fusion (RRF)**. Query expansion enriches short
queries before the fan-out. Models live in `search/search_models.py` (Pydantic v2):
`SearchResult`, `HybridSearchResponse`, `ChunkSearchResult`, `ChunkSearchResponse`,
`SearchFilter`.

## 5. LLM provider layer (`src/llm/`)

- **Interface:** `llm/protocols.py` — `LLMBackendProtocol` plus the shared wire types and
  errors (`OllamaChatMessage`, `OllamaModel`, `OllamaConnectionError`, …) every backend
  accepts/returns.
- **Backends** live in `src/plugins/llm/`; `make_llm_client()` (`provider.py`) selects one
  from `[llm] backend` via the plugin registry:
  - `ollama` (default) / `auto` → `OllamaClient` (Ollama native API: `/api/chat`, `/api/generate`,
    `/api/tags`; host from `[llm] ollama_url`, default `:11434`).
  - `openai` / `llama.cpp` / `lmstudio` / `vllm` → `OpenAICompatClient` (OpenAI-compatible API;
    `base_url` + `model` from `[llm] openai`).
- Switching provider is a one-line config change (`backend`, `base_url`, `model`) — no code change.

## 6. Chat / context pipeline (`src/llm/`, `src/api/routes/chat_rag_helpers.py`)

On each chat request:

1. `system_prompt.py` (`SystemPromptBuilder`) injects identity + the fixed "notary" behavior
   contract + user profile + indexed paths (always on).
2. `intent_classifier.py` (regex only, no LLM call) detects config / identity / scope questions.
3. `rag_engine.py` retrieves relevant context from `HybridSearchEngine` and enriches the prompt
   (Core feature — Premium gates the document *perimeter* at ingestion, not the chat engine);
   `rag_context_formatter.py` formats it.
4. `context_adequacy.py` returns a grounded refusal ("not found in your documents") when a
   factual question has no usable local context.

Chat data models (`ChatMessage`, request/response schemas) are Pydantic v2
(`core.models`, `api/routes/chat_models.py`).

## 7. API (`src/api/`) — FastAPI on `:8200`

- `/api/*` — native AiTao endpoints (search, ingest, health, stats).
- `/v1/*` — OpenAI-compatible (chat/completions, models, embeddings).
- `/api/tags` — Ollama-compatible model listing.

Routes are imported lazily inside handlers to keep startup fast.

## 8. MCP server (`src/mcp_server/`)

Built with `fastmcp`. Transports: `stdio` (Claude Desktop, VS Code, Cursor) and `SSE`/`HTTP`
(`:8201`). Tools: `aitao_search`, `aitao_ingest`, `aitao_stats` (Core); `aitao_ocr`,
`aitao_extract` (Premium). In stdio mode **all logs must go to stderr** —
stdout is the protocol wire.

## 9. Core (`src/core/`)

- `config.py` — layered TOML (`config/config.toml` → `~/.config/aitao/user.toml` →
  `APP__SECTION__KEY` env vars), exposed as **typed Pydantic settings** (`config_schema.py`):
  access is by attribute, e.g. `config.llm.backend`, `config.search.meilisearch.url`. Singleton
  via `get_config()`.
- `models.py` — canonical domain entities as Pydantic v2 (`Document`, `ChatMessage` / `ChatRole`).
  A missing or mistyped field raises `ValidationError` at construction, not a `KeyError` later.
- `license.py` — RSA-SHA256 **offline** license validation. `LicenseManager().require_premium(...)`
  gates Premium features (`advanced_formats`, `ocr_advanced`, `extraction`). RAG
  chat over text documents is **Core** (PRD §5). `AITAO_BETA=true` bypasses checks during beta.
- `logger.py` — structured logging via `get_logger(...)`; no `print()` in `src/`.
- `pathmanager.py` — runtime paths (`~/.aitao/...`).
- `registry.py` — shared **constants and keys** only (`ConfigKeys`, `StatsKeys`, `APIEndpoints`,
  `Defaults`), task/queue enums + `Task`, health structures, and model-management structures.
- `plugin_registry.py` — a `(kind, name)` registry; LLM backends and Core file extractors
  (PDF, plain text, code, images, EXIF) **live in `src/plugins/{llm,extractors}/`**
  (built-ins), self-register via `@register(...)` and are loaded by `discover_plugins()`,
  so a new component is one drop-in file with no core change. OCR providers AND the
  Office-format extractors (DOCX/PPTX/XLSX/ODF) self-register the same way from the
  separately distributed `aitao-premium` package, discovered through the `aitao.plugins`
  entry-point group instead of a built-in directory. Premium plugins are gated at lookup.
- `events.py` — a tiny in-process publish/subscribe `EventBus`. The indexer and search engine
  publish `document.indexed` / `document.deleted` / `search.executed`; subscribers (e.g.
  `event_stats.py`, future automations) attach with no change to the pipeline. Handlers are
  error-isolated — a failing subscriber never breaks indexing or search.

## 10. Design principles

- **Work by module.** Each layer above is an independent brick with a clear contract; one brick
  can be fixed or replaced (e.g. the LLM backend) without touching the others.
- **Typed everywhere.** Configuration (`config_schema.py`) and domain objects (`core.models`
  plus the per-layer model modules) are Pydantic v2, so invalid data fails fast at the boundary
  instead of propagating as silent dicts.
- **Validate against reality.** Anything touching the model or the search corpus is "done" only
  when checked against real data and a real model — not when unit tests alone are green.

## 11. Running & testing (for contributors)

- Package manager: **uv** (never bare `pip`). `uv pip install -e ".[dev]"`.
- Run tests: `uv run pytest` (config sets `pythonpath = ["src"]`).
- Conventions: every source file opens with a short purpose header; keep files under ~350–400 LOC;
  code comments and documentation in English; chat / UX strings localized.

---

*AiTao — local document search & grounded chat.*
