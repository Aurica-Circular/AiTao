# Changelog

All notable changes to AiTao will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adopts [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
starting from version `2.5.1`.

---

## [Unreleased]

### Added
- Licence keys can now be cancelled (US-141): AiTao Premium checks a public,
  signed revocation list once a day at most, sending nothing about the user
  or their documents. `aitao license status` shows a calm, dedicated panel
  when the installed key has been deactivated.

### Changed
- Premium now requires a valid, non-expired licence key in every case — the
  pre-commercial "beta mode" shortcut (`AITAO_BETA=true` / config
  `beta_mode`, on by default) is gone; nothing unlocks Premium without a real
  key (US-140).
- `aitao license status` shows one clear panel per licence state (module not
  installed, module installed with no key, active — label/end date/days
  left, expired, invalid) instead of the old "Beta mode active" branch.
- An expired licence key now raises a clear, warm message (not a bare
  refusal): it names the label and end date, thanks the person for testing,
  confirms their documents and index are untouched and Core keeps working,
  and points at how to continue with Premium.

### Removed
- The `AITAO_BETA` environment variable and the `[license] beta_mode` config
  flag — neither is read anywhere in the core or the Premium module anymore.

## [5.0.0] - 2026-09-28

> First release published from the Aurica Circular repository; AiTao Premium modules
> (Office formats, OCR) are now distributed separately; earlier entries document the
> product's history.

### Added
- `LICENSING.md` — a plain-language map of what is free and what is not: the
  additional permission under AGPL-3.0 section 7 that makes AiTao Core and AiTao
  Premium Modules legally combinable, the trademark notice, and where to write for a
  commercial license (US-138-0, US-139). The parties are named explicitly: **Philippe
  BERTIERI** holds the copyright and grants the additional permission; **Aurica Circular
  Co. Ltd. (歐瑞卡有限公司)** publishes and sells the Premium Modules under licence from
  him. Naming them matters — the additional permission only covers modules distributed
  by the party it names, so an approximation there would leave the whole arrangement
  pointing at nobody.
- `CONTRIBUTING.md` — contributor terms: the Developer Certificate of Origin plus one
  additional grant, without which an outside contribution could never be included in a
  commercially licensed module. Contributors keep their copyright and their moral
  rights; they grant a licence, not an assignment (US-142).

### Changed
- **Licence verification moved out of the public core into the separately
  distributed Premium package** (`aitao-premium`, US-138-1). This repository is
  published under AGPL v3: code published here can be read, edited and
  redistributed by anyone, so it could never actually enforce a licence. The RSA
  public key, key-file handling, signature verification and beta-mode logic now
  live in `aitao-premium`, discovered at runtime through the `aitao.plugins`
  entry-point mechanism (see "Premium plugin discovery" below). `aitao.core.license`
  keeps only a small, non-secret facade with the same public API
  (`LicenseManager`, `PremiumFeatureError`, `PREMIUM_FEATURES`,
  `PREMIUM_EXTENSIONS`, `is_premium_extension`): with `aitao-premium` not
  installed, it behaves as a plain Core edition — `is_premium()` is `False`,
  `activate()` fails with a message pointing to `auricacircular.com` instead of a
  stack trace.
- **`LICENSE` replaced by the complete, unmodified AGPL-3.0 text** (34.5 kB,
  sections 0 to 17). The file previously shipped a truncated copy: nine sections were
  missing — among them sections 15, 16 and 17, the warranty disclaimer and the
  limitation of liability, which are the ones that protect the author — section 2 had
  been rewritten by hand, and a "dual licensing" note had been appended inside the
  license text itself. Project-specific terms now live in `LICENSING.md`, where they
  belong (US-139).
- `README.md` — the Core/Premium table now matches the product. Chat over your
  indexed documents was advertised as Premium although it is a Core feature (PRD §5:
  Premium widens the document perimeter at ingestion, it does not gate the chat
  engine), and advanced document formats — the main Premium feature — were missing
  from the table altogether. Both answer reliability checks, fast and deep, are Core
  features.
- Deep answer verification (`verify_answer = "deep"`) is now free (Core). It catches
  "right document, wrong figure" errors that the fast check cannot see, and reliability
  is AiTao's core promise, so it is not reserved to paying users. Still opt-in, because
  its speed depends on the model used — pick a fast, non-reasoning model for the deep
  pass via `[rag] verify_model`.
- `docs/ARCHITECTURE.md` — same correction: RAG enrichment is a Core feature.
- `pyproject.toml` — real author name and a working contact address; the published
  package metadata carried a placeholder `example.com` address.
- `pyproject.toml` — the one-line package summary, the sentence shown on every package
  index page, no longer reads "AI Tao V2 — Document Search & Translation Engine". The
  product is spelled `AiTao`, it is no longer a 2.x release, and translation has been
  removed from the product (see Removed below). The summary now describes
  what the free edition actually does.
- Purchase and contact links now point to Aurica Circular Co. Ltd.
  (`auricacircular.com`), the company that sells AiTao Premium, instead of the
  author's personal domain — in `LICENSING.md`, `README.md`, `pyproject.toml` and in
  the Premium message raised by `core/license.py`.
- **Premium plugin discovery**: in addition to the built-in `aitao.plugins`
  package, `discover_plugins()` now also loads plugins exposed as Python
  entry points under the `aitao.plugins` group. This lets a Premium package
  installed separately (`pip install`) register its plugins with the core
  simply by declaring an entry point — the core does not need to import or
  know about it by name. A third-party entry point that fails to load is
  logged and skipped; it never prevents startup.
- **OCR engines now ship in the separately distributed Premium module**
  (`aitao-premium`, US-138-1). `MacOSVisionProvider`, `TesseractProvider` and
  `QwenVLProvider` moved out of this repository — same `("ocr", name)`
  registration keys, so existing `[ocr]` config is unaffected — because the
  code that actually reads a user's scanned documents cannot live somewhere
  publishable and editable by anyone. The core keeps the OCR router
  (`aitao.ocr.router.OCRRouter`) and interfaces (`aitao.ocr.interfaces`); with
  no Premium module installed, OCR is refused the same way an unlicensed user
  is refused today, never a crash.
- **Office-format extractors now ship in the separately distributed Premium
  module** (`aitao-premium`, US-138-1); their libraries are no longer
  installed with the free edition. `DOCXExtractor`, `PPTXExtractor`,
  `XLSXExtractor` and `ODFExtractor` moved out of this repository — same
  `("extractor", "docx"/"pptx"/"xlsx"/"odf")` registration keys, so nothing
  about how `TextExtractor` dispatches by extension changes. `python-docx`,
  `python-pptx` and `openpyxl` are no longer core dependencies (removed from
  `pyproject.toml` and the portable requirements files). The core's
  `item_preparer.prepare_item()` gate (`LicenseManager.is_premium_extension`)
  still refuses these formats at ingestion the same way it always has, before
  extraction is even attempted; with no Premium module installed,
  `TextExtractor` simply has no extractor registered for these extensions —
  the same "unsupported file type" outcome as any other unknown extension,
  never a crash. `aitao extract file`/`aitao extract types` (the CLI preview
  commands) now look the extractor up in the plugin registry instead of
  importing `DOCXExtractor` by name, and print a clear message pointing at
  the AiTao Premium module when it is absent.

### Removed
- `README.md` — the "one key = one machine" claim: the license key does not bind to a
  machine yet (planned in US-141), so the promise was not kept. The "personal use"
  wording was corrected too — the AGPL-3.0 also permits commercial use, and saying
  otherwise weakened the author's own position.
- The `aitao_translate` MCP tool and the whole translation feature. It was
  never functional: it called `processing.translator.TranslatorPipeline`, a
  module that was never written, so the tool would always fail at runtime.
  Since any AI agent connected to AiTao can already translate text on its
  own, this is not a feature AiTao needs to build or sell — it has been
  removed instead of finished, along with its MCP registration, its tests,
  and every listing of it in the docs and the Premium extra.

### Security
- **A third party's real email address was published in this repository.** It came
  from a real indexed document used while debugging, and appeared in the 3.16.0
  changelog entry as well as in three source files
  (`src/aitao/llm/query_distiller.py`, `tests/unit/test_query_distiller.py`,
  `tests/unit/test_rag_engine.py`). Replaced everywhere by a fictional address of the
  same shape, so the tests keep covering the case they were written for. AiTao's core
  promise is that indexed documents never leave the user's machine — the public
  repository must not say otherwise.

---

## [4.3.1] — 2026-08-07 — the folder filter (--path / path_contains) now actually restricts results (US-125)

### Fixed
- The folder filter (`--path` on the CLI, `path_contains` in the API and RAG
  search filters) was silently ignored: it reached the last step of the
  document search call (`search_fusion_sync`) and the chunk-based chat/RAG
  retrieval call (`search_chunks`), but was never applied there, so a search
  scoped to a folder still returned documents from anywhere in the index — in
  every mode (semantic, hybrid, fulltext). Meilisearch has no native
  substring/CONTAINS filter and `path` isn't a filterable attribute of the
  index, so the fix enforces the folder filter in Python, as a post-filter
  applied after retrieval: the search backend is over-fetched first (more
  candidates than the requested `limit`), then only hits whose path contains
  the requested substring are kept (case-insensitive, Unicode NFC-normalized
  on both sides), before truncating to the requested `limit`. Applies
  identically to every search mode since they all share the same retrieval
  call. No behaviour change when no folder filter is set.

---

## [4.3.0] — 2026-08-06 — optional auto-prune of out-of-scope documents at scan (US-126-C)

### Added
- New opt-in config flag `[indexing].auto_prune_out_of_scope` (default
  `false`). When enabled, every periodic worker scan automatically runs the
  out-of-scope prune (same detection logic as `aitao index prune`, US-126-A)
  and removes indexed documents whose path is no longer under any configured
  `include_paths` root — no manual command needed. Config-driven only: scope
  is decided exclusively from the configured `include_paths` list, never from
  disk presence, so a folder that is still configured but momentarily
  unavailable (e.g. an unplugged external volume or an unmounted cloud
  folder) keeps its documents indexed. Default remains `false`, so existing
  deployments see no behaviour change unless they opt in.

---

## [4.2.1] — 2026-08-06 — reliability banner no longer flags document version numbers (US-127)

### Fixed
- The deterministic grounding check (`answer_validator.evaluate_grounding`) no
  longer flags digits that appear only in a cited document's **title or path**
  (e.g. a version number or date baked into the filename, such as
  `國外出差管理辦法第13.4版-20250312`) as an unsupported figure. The verbatim
  digit rule now also checks each retrieved document's title + path, not just
  its chunk content — a version/date that is part of a genuinely retrieved
  document's own name is not an LLM invention. The embedding corpus used for
  non-digit sentences is unchanged; only the digit-token corpus for the
  verbatim rule was widened.

---

## [4.2.0] — 2026-08-05 — `aitao index prune`: remove out-of-scope documents (US-126-A)

### Added
- **New command `aitao index prune`** removes indexed documents whose file path
  is no longer under any configured `[indexing].include_paths` root — for example
  after you remove or comment out a folder. It prints a report grouped by folder
  and asks for confirmation before deleting. `--dry-run` lists candidates without
  deleting anything; `--yes` skips the confirmation prompt.

### Safety
- Scope is decided **only** from the configured `include_paths`, never from disk
  presence: a still-configured but temporarily unmounted volume keeps its
  documents (no accidental bulk delete when a drive is unplugged). An empty
  `include_paths` refuses to prune, and a failure to read the index aborts
  without deleting anything.

### Internal
- Pin `ruff==0.15.15` in dev dependencies so CI lints with the same version as
  local and the pre-commit hook. An unpinned `ruff>=0.1` let CI float to 0.16.x,
  whose broader default ruleset failed the release lint job (green locally).

---

## [4.1.5] — 2026-08-05 — English comments in shipped config files (US-129)

### Changed
- All comments in `config/config.toml.template` and `config/config.toml.starter`
  are now in English, matching the project rule that everything shipped to
  GitHub (code, CHANGELOG, README, config templates) is English. Example values
  meant for the user (e.g. `who_are_you`) are unchanged. No behaviour change.

---

## [4.1.4] — 2026-08-05 — reply-language "mirror" mode is now the default (US-128)

### Changed
- **`identity.response_language` now defaults to `"mirror"`**, meaning AiTao
  replies in the language of each question instead of a hard-coded language.
  A question asked in English gets an English answer, a question in French a
  French answer. The literal value `"mirror"` (case-insensitive) and an empty
  value behave the same way.
- To **force** a single reply language regardless of the question, set
  `response_language` to that language (`"français"`, `"English"`, `"中文"`…) —
  the previous forcing behaviour is unchanged for existing configurations.
- An explicit request inside the message ("answer in English") still takes
  priority over both, exactly as before.

### Notes
- Existing installations keep their current `config.toml` value untouched; only
  the shipped template default changed. Set `response_language = "mirror"` to
  opt in.

---

## [4.1.3] — 2026-07-24 — reliable language filter across native AND scanned documents (US-85c)

### Fixed
- **The `--language` filter now finds documents regardless of their origin.**
  Languages were stored in three inconsistent notations depending on the
  source (native text: `fr`, `zh-tw`; Apple Vision OCR: `fr-FR`, `zh-Hant`;
  Tesseract OCR: `fra+eng`), while the Meilisearch filter is an exact string
  match — so `--language zh` matched neither a native Chinese document nor a
  Chinese scan. Every language is now collapsed to a **canonical short code**
  (`fr`, `en`, `zh`, `ja`…) at indexing time, and the filter's input is
  normalized the same way (`--language zh-TW` or `FR` both work).

### Changed
- The **precise** language tag (e.g. `zh-Hant` vs `zh-Hans`) is preserved in
  the document metadata (`language_precise`) — nothing is lost.
- A multi-language Tesseract pass (`fra+eng+chi_tra`, no single winning
  language) is now tagged `unknown` instead of being assigned a language at
  random.

> Already-indexed documents keep their old tag until they are re-indexed.

---

## [4.1.2] — 2026-07-24 — Windows OCR: tesseract path validated + installation docs (US-84)

### Validated
- **Cross-platform OCR path (tesseract) validated for Windows**, from macOS,
  via a faithful simulation: forcing the OS to report "Windows" makes Apple
  Vision disable itself through its own guard, and the router falls through
  to tesseract — exactly as it does on real Windows. The **real tesseract
  binary** was then exercised on real fixtures: a French image (perfect
  OCR), an image-only "scanned" PDF (rasterised by poppler), and a
  Traditional-Chinese image (`chi_tra` pack, exact recognition).
- New regression test `tests/unit/test_ocr_tesseract_real.py`: exercises the
  real binary when present, skips cleanly otherwise (CI). Ready to run as-is
  on a Windows runner (remaining item US-84b: smoke test on a real machine).

### Documentation
- **README**: new "Tesseract & Poppler — the OCR engine" section (optional
  Premium prerequisite) with a Windows walkthrough (64-bit installer,
  `fra`/`eng`/`chi_tra` packs, poppler for scanned PDFs) and a note on the
  `qwen_vl` alternative.
- **docs/INSTALLATION.md** (Windows guide): equivalent OCR section + a
  troubleshooting entry ("scanned documents not searchable").
- Clarified "Windows 64-bit only (x64 or ARM64, no 32-bit)".

---

## [4.1.1] — 2026-07-22 — macOS OCR validated end-to-end, reading order fixed (US-81)

### Fixed
- **OCR text block reading order (Apple Vision)** — detected blocks were
  assembled in the raw order returned by the API, which is not guaranteed to
  be Apple's reading order. Documents with an atypical layout (tables,
  columns) could come back scrambled. Added sorting (top-to-bottom,
  left-to-right) before assembling the text.

### Validated
- End-to-end macOS OCR validation (US-81): image, Latin scanned PDF,
  Traditional-Chinese scanned PDF (a real company document) — all
  retrievable via search. `qwen_vl` (vision model) tested on a real table:
  line-by-line reconstruction clearly superior to classic OCR, but ~40×
  slower — not yet triggered automatically (follow-up noted in the backlog,
  US-118).

---

## [4.1.0] — 2026-07-21 — no more hardcoded default model: automatic selection among installed models (US-116)

### Added
- **Automatic chat model selection** — if `[llm] default_model` is absent or
  not found on the configured backend, AiTao automatically picks the
  smallest installed model that carries no known risk for chat (never a
  "reasoning" model that answers by thinking in a loop without ever
  producing a direct reply, never a cloud-proxied model with no locally
  stored weights). Users remain free to set `default_model` explicitly;
  auto-selection only kicks in when it is absent or broken.
- `llm/model_advisor.py`: new `pick_safe_default()` and
  `is_cloud_proxy_model()` functions.

### Fixed
- **Chat no longer breaks when the configured model isn't installed** —
  previously an immediate hard error (real incident on 2026-07-20: a config
  rebuilt from the template pointed at a model that was never pulled, and
  chat refused outright). Now degrades to a clear warning + a working
  fallback model.
- **Three diverging, hardcoded default-model values**
  (`config.toml.template`, the Python config schema, the `aitao init` setup
  wizard) removed in favour of auto-selection.
- **Removed phantom settings** — `[llm.startup] check_models`/`auto_pull`
  were declared in the config schema but no code ever applied them; removed
  so as not to promise a capability that doesn't exist.

---

## [4.0.3] — 2026-07-20 — clear message for legacy Office formats, last US-098 residual closed

### Fixed
- **Legacy Office formats (`.doc`/`.ppt`/`.xls`) unsupported** — product
  decision recorded (option C: no dedicated extractor unless a real customer
  request shows up). The ingestion failure message, previously generic
  ("Unsupported file type"), now points to the modern equivalent to use
  ("re-save the file as .docx to index it").

### Added
- Regression test closing the last open item from US-098 (A2 — missing
  `config.toml` → clear warning, never a folder silently created in the
  wrong directory); the behaviour already existed, only the automated check
  was missing.

---

## [4.0.2] — 2026-07-20 — path resolution centralised on PathManager, no more fragile hop-counting (US-115)

### Fixed
- **`aitao version` showed a stale number** (`2.7.46` instead of the real
  `pyproject.toml` version) — `core/version.py` located the project root by
  counting fixed directory hops (`Path(__file__).parent.parent...`), a count
  left at the old depth after the US-114 packaging refactor;
  `pyproject.toml` became unreachable and the function silently fell back to
  stale installed package metadata. Fixed by delegating to
  `path_manager.root` (marker-based resolution, independent of depth).
- Same latent bug (never triggered, unused) in `cli/commands/models.py`:
  counting 2 hops instead of 4.

### Changed
- **Centralised path resolution** — ~25 places in the code
  (`indexation/{worker,scanner,queue}.py`, about fifteen CLI commands) each
  hand-recomputed the project root or the `src/` folder by counting
  `Path(__file__).parent...` hops. That duplication is exactly what broke so
  many places at once during the US-114 refactor. All of them now delegate
  to the central `PathManager` (`path_manager.root`, new
  `path_manager.get_src_dir()` accessor); the dead import bootstrap (no
  longer useful once the `aitao` package was in place) was removed
  everywhere it appeared. The only two entry points that can run standalone
  (`cli/main.py`, `cli/commands/models.py`, before `aitao` is on the Python
  path) keep a local computation, but a robust one (marker-based search
  rather than a fixed hop count).
- **Anti-regression lock** (`tests/unit/test_no_hand_rolled_paths.py`): a
  test now fails if a new hop-count of this kind appears outside an explicit
  exception list — so a future file move requires fixing zero places
  instead of twenty-five.

---

## [4.0.1] — 2026-07-20 — clean `aitao` package layout, no leaked dev data in release zip (US-114)

### Changed
- **Packaging refactor (src-layout)** — the top-level generic modules (`api`,
  `cli`, `core`, `indexation`, `llm`, `mcp_server`, `ocr`, `plugins`, `search`,
  `storage`) now live under a real `aitao` package (`src/aitao/`) instead of
  sitting loose under `src/`. Fixes `[tool.setuptools] packages = ["src"]`,
  which installed a package literally named `src` instead of `aitao`. All
  internal imports updated accordingly (~150 files); no functional/behavioural
  change — golden bench and full unit suite pass identically before/after
  (1229 passed / 1 skipped, 25 golden scenarios passed).
- `[project.scripts]` now points directly at `aitao.cli.main:app`.

### Fixed
- **Release zip no longer leaks dev data** — the Windows portable archive
  build (`.github/workflows/release.yml`) copied the whole `src/` tree,
  which included `src/data/history/chat_history.db` (a committed dev chat
  history database) and `src/docs/` (dev-only generated CLI reference). It
  now copies `src/aitao/` only, shipping runtime code without dev artifacts.

---

## [4.0.0] — 2026-07-16 — fusion search engine only, batched ingestion, CJK content gluing, migration tooling (ÉPIC-31, US-109→113)

### Added
- **Fusion search engine** (`[search] engine = "fusion"`, US-110) — ONE native
  Meilisearch hybrid call per retrieval stage (documents for `/api/search`,
  excerpts for chat/RAG) replaces the parallel LanceDB+Meilisearch fan-out +
  Reciprocal Rank Fusion merge. Query and document vectors are computed
  locally with the same bge-m3 model as before (`userProvided` embedder,
  dim 1024 — Meilisearch never re-embeds). `semanticRatio` is configurable
  **per stage** (`semantic_ratio_chunks` / `semantic_ratio_documents`,
  default 0.5 each — study US-106: the optimal ratio does not transpose
  between stages). Measured on the golden gate (US-110): excerpt-stage
  recall@5 67.6% → 85.1%, exact-word 45.8% → 100%, median latency
  252 ms → 86 ms; document stage recall@5 85.1% → 90.5%, 228 ms → 69 ms.
- **Batched ingestion** (US-111, absorbs US-093) — the worker groups up to
  `[indexing] batch_size` files into ONE Meilisearch write task for documents
  and ONE for excerpts (measured ~4.6× faster than per-file writes).
  `batch_size` defaults to 1 (today's per-file behaviour, opt-in batching).
- **CJK content gluing at ingestion** (US-111, backlog 89-6) —
  `core/cjk_glue.py`: stray spaces between CJK ideographs (OCR/extraction
  artifacts, e.g. 承擔 stored as « 承 擔 ») are deterministically glued in
  document CONTENT before chunking/embedding/indexing, fixing both jieba
  tokenization and the bge-m3 embedding. Applies to both engines.
- **US-094 index settings** (absorbed into US-111) — `proximityPrecision:
  byAttribute`, `prefixSearch: disabled`, `searchCutoffMs: 150`,
  `localizedAttributes` (fra/cmn/eng) on both indices; each key gated by the
  golden bench in both engine modes.
- **`aitao migrate-v4` command** (US-112) — migrates existing stores to the
  v4 fusion indices with ZERO re-embedding: existing bge-m3 vectors are read
  from LanceDB (`chunks` + doc-level table) and copied verbatim into
  `<index>_next` Meilisearch indices built alongside the live ones
  (rebuild-alongside + `swapIndexes`, zero downtime). Three explicit phases:
  `--dry-run` (DEFAULT — build + verify + report, never swaps), `--swap`
  (interactive confirmation or `--yes`), `--rollback` (re-swap). Documents
  whose stored content changes under CJK gluing are NOT vector-copied (their
  embedding was computed on gapped text): they are pushed lexical-only,
  counted separately in the report, and — on `--swap` only — requeued for a
  full re-extraction/re-embedding through the normal pipeline when their
  source file still exists ("source missing" is reported otherwise).
- `/api/stats`: new additive optional field `meilisearch_chunks` — statistics
  of the dedicated excerpt index (populated under fusion only).
- Shared LanceDB-free embedding source (`search/embedding_source.py`):
  the answer validator's grounding check (`RAGEngine.embed_texts`) and the
  fusion document-stage query vector no longer construct a LanceDB client —
  the last direct LanceDB dependency of the reliability layer is cut.
- **Per-file extraction timeout** (US-113) — `[indexing] extraction_timeout_s`
  (default 300): a pathological file (real incident: a text-less vector PDF
  froze the worker for 1h22 at 0% CPU) now fails cleanly with an explicit
  "extraction timeout" error and the worker moves on to the next file.
- **Capped failure retries** (US-113) — `[indexing] max_reconciliation_retries`
  (default 3): a file that keeps failing reaches a terminal "given up" state
  (visible in `queue failures`) instead of being recycled forever.

### Changed
- **BREAKING — migration required**: the chat/RAG excerpt store moves from
  LanceDB (`chunks` table) to a dedicated Meilisearch index (`aitao_chunks`)
  under the fusion engine. Existing installations must run
  `aitao migrate-v4` (dry-run first, then `--swap`) or fully re-index.
  See `docs/MIGRATION-4.0.md`.
- **BREAKING — `/api/stats`**: the `lancedb` field returns `null` under
  `[search] engine = "fusion"` (the field itself is kept, Optional — least
  breakage). Unchanged under `rrf`.
- CLI (`status`, `db status`, `dashboard`): under fusion, the displays
  reflect the real stores (Meilisearch excerpt index instead of LanceDB) and
  no longer fail if `~/.aitao/data/lancedb` is absent.
- Worker: `task.metadata["force"]` now forces a full re-index of an
  unchanged-on-disk file (used by the migration's CJK requeue — the mtime
  dedup check would otherwise skip those files forever).
- **BREAKING — fusion is the ONLY engine** (US-113, decision D1): LanceDB
  (client, admin, chunk store) and the parallel RRF fan-out are removed from
  the live code path. The dev-only `[search] engine` flag is gone (a leftover
  key in config.toml is ignored with a soft warning). The embedding model
  identity moves to `[search.embedding]` (`embedding_model`, `dimension`) —
  legacy `[search.lancedb]` keys keep working through an automatic fallback.
  `aitao migrate-v4` keeps a LAZY LanceDB reader so pre-4.0 stores remain
  migratable.
- `aitao scan reindex` now actually forces re-processing: it queues files
  with `force: True` (batch queueing gained metadata support). Previously
  every requeued unchanged file was silently skipped as "already indexed" —
  reported as success with no work done.
- `aitao search help` shows the help text instead of running a literal
  search for the word "help" (the group's quick-search positional consumed
  the token before subcommand resolution — caught by the CI help-smoke test,
  which it broke by eagerly loading the embedding model).

### Fixed
- **Endless re-indexing of accented/CJK filenames on macOS** (US-113): the
  scanner computed its reconciliation doc-id from the raw NFD path returned
  by the filesystem while the indexer stored NFC-normalized ids — the same
  files were re-indexed ~48×/24h forever. Both sides now NFC-normalize; the
  bug was latent on Windows/Linux too for folders synced from a Mac.
- Single-file indexing (default `batch_size = 1`) now writes the REAL
  document-level vector; previously only the batched path did, leaving
  documents with an empty-vector opt-out (no semantic leg on `/api/search`).
- `swapIndexes` no longer fails on a first migration when the excerpt index
  does not exist yet (an empty placeholder is created for the missing side);
  `--rollback` refuses to run when there is no prior swap to undo (it would
  have swapped live data against an empty placeholder).
- Pre-existing broken import (`US094_SETTINGS`) that made every
  Meilisearch-touching test fail on the branch.

### Notes
- `~/.aitao/data/lancedb` is NOT deleted by the migration: it stays on disk,
  read-only, as the rollback safety net. It becomes safe to remove after the
  migration has been validated (see `docs/MIGRATION-4.0.md`).
- The `rrf` ↔ `fusion` config flag is a dev/bench comparison tool for the
  duration of the v4 branch only; 4.0 ships fusion-only and the flag is
  removed in US-113 (decision D1 — user rollback = stay on 3.x).

---

## [3.31.0] — 2026-07-10  `main` — meta-questions reduced to their subject: "which documents talk about X" finally finds X (US-108, I-17)

### Added
- **Meta-question reduction** — `llm/query_meta_shape.py`: deterministic
  FR/EN/zh rules recognize the "which documents talk about X / y a-t-il des
  documents sur X / 哪些文件提到 X" shapes and reduce the search query to the
  subject X, kept verbatim. Measured root cause (study US-106): the words
  "documents/parlent" drowned the actual subject at every retrieval stage on
  every engine architecture — the fix had to be upstream of the engines.
  Fail-open (an unrecognized shape changes nothing), no LLM, every firing
  logged with its reason under `[rag] reliability_debug` (étude §6.4
  invariants). Both retrieval stages (documents and excerpts) benefit.
- Golden bench: new committed scenario `i17_meta_question` with a synthetic
  corpus reproducing the incident's self-pollution (decoy docs that
  themselves say "documents qui parlent de…") — proven red before the fix,
  green after. Bench: **16 passed, 0 xfail**.

### Notes
- The 10 meta-question phrasings from study US-106 all reduce to their exact
  ground-truth subject (10/10). Unit suite: 1448 passed.

---

## [3.30.1] — 2026-07-10  `main` — ingestion hygiene: recover the silently failing documents (US-107, study US-106 option C)

### Fixed
- **OCR language-list crash (15 queued files)** — when the OCR router falls
  back to the configured `ocr.languages`, providers received a LIST of
  candidate tags and echoed it raw into `OCRResult.lang_detected`, crashing
  `Document` validation at indexing time. Both providers now always report a
  single string: Tesseract falls back to the combined lang string it actually
  ran with; Qwen-VL reuses its already-coerced language hint. Reproduced by
  test before the fix (verbatim pydantic error from the field incident).
- **Every .pptx failed to index (11 queued files)** — `python-pptx` was
  imported by the PowerPoint extractor but never declared/installed. Added to
  core dependencies (same "Document Extraction" group as python-docx) and to
  both portable requirements files; extractor verified on a real 85-slide
  deck from the corpus.

### Changed
- **144 failed queue tasks requeued** after the fixes (15 language-bug + 11
  pptx + 118 whose causes were lost to log rotation, existing files only) —
  the worker re-processes them; genuinely broken files will re-fail with a
  now-visible cause. Not requeued: 57 legacy .doc/.ppt (format unsupported —
  separate decision) and 9 empty-after-extraction.

---

## [3.30.0] — 2026-07-10  `main` — response reader: sentence roles + extract↔source attribution (US-104, ÉPIC-30 phase 3)

### Added
- **Response reader (brick 3)** — `llm/response_reader.py`: every answer
  sentence gets a ROLE before the grounding check runs — `affirmation`,
  `citation` (names a retrieved doc AND carries a fact), `habillage`
  (ordinal + structural vocabulary: "le premier document du contexte"),
  `echo_metadata` (a doc title/path/Markdown heading echoed back with no
  added fact — the 百 of 百年淬鍊 is no longer read as a numeral), `notice`
  (AiTao's own lines). Rules first, every verdict carries its reason.
- **Extract↔source attribution (I-05, absorbs US-096)** —
  `llm/source_attribution.py`: a citation sentence is checked against the
  document it NAMES, not just "somewhere in the context". Digit rule
  (verbatim, fires only on an unambiguous single candidate) + embedding
  rule (bge-m3, conservative 0.10 margin). Wrong attribution → corrective
  banner naming the probable source — never a rewrite. Always active, Core,
  independent of `verify_answer`.
- **Stale-citation corrective (I-15)** — same module: an extract attributed
  to a document ABSENT from this turn's context (the writer recycling its
  own chat history) now gets the corrective banner too — « l'extrait
  attribué à « X » (introuvable dans les documents de ce tour) semble
  provenir de « Y » » — where G2 previously only warned "don't trust this
  reference". Digit rule or absolute embedding floor (0.58); silent when
  no single probable source stands out.
- **Reader appeal (LLM appeal court)** — `llm/reader_appeal.py` + new
  `[rag] reader_llm = true` key: when the deterministic check is about to
  flag sentences, ONE grouped call to the small `verify_model` (temperature
  0, 3 s budget) asks whether each is a real affirmation or habillage/echo
  the rules missed. It can only REMOVE a flag, never add one — disabling it
  or any failure loses nothing (fail-open). Zero LLM latency on clean
  answers.

### Changed
- **G7 rebranched on roles (I-10, I-09 residual)** — `evaluate_grounding`
  only grounds `affirmation`/`citation` sentences: no more ⚠️ banner on
  dressing ("le premier document du contexte") or echoed CJK titles. Fails
  open to the pre-US-104 behaviour if role classification errors out.
- **Reliability metadata goes structured (C-01 groundwork)** — API
  responses now carry `reliability_roles` (role counts) and
  `reliability_attribution` (wrong-source notes) alongside the text
  banners; the banners remain the user-facing channel for now.
- **Debug mode (étude §6.4, 5th invariant)** — every sentence role is
  logged with its reason, every appeal/attribution verdict too; INFO under
  `[rag] reliability_debug = true` (default), DEBUG otherwise.

### Fixed
- `answer_validator.py` module docstring no longer claims the check is
  "opt-in (off by default)" — it has been default-on ("fast") since US-092.

### Tests
- Golden bench: `i05_attribution` and `i10_habillage` flip from xfail to
  REAL passes; new `i104_reader_appeal` (scripted appeal verdict → unflag;
  no verdict → fail-open asserted both ways) and `i15_stale_citation`
  (subject change + stale attribution → corrective banner names the true
  source). `DELIVERED_PHASE = 3` — every committed scenario now runs as a
  real, unmarked assertion: **15 passed, 0 xfail**. Unit suite: 1399
  passed. The 2 `live_store` e2e failures were proven pre-existing on a
  clean HEAD (prod-store content drift, unrelated).

---

## [3.29.2] — 2026-07-08  `main` — intent router: the probe no longer sees its own question as history (US-105.2)

### Fixed
- **Field replay of I-16 still refused after US-105.1** — root cause found and
  reproduced 5/5: both chat routes passed the request messages as the probe's
  "recent turns", and the LAST entry of those messages IS the question being
  classified. Shown twice (once as meta-history, once as the question),
  granite4 flips deterministically to DOCUMENTARY — the bench and the
  workshop never hit it because both passed history WITHOUT the current
  turn. Call sites now exclude the current turn (`[:-1]`, mirroring the
  bench), and `_build_messages` grew a belt-and-braces guard dropping a
  trailing user turn identical to the question. Post-fix, the prod-shaped
  probe routes the I-16 question GENERAL 3/3 (~120 ms warm).
- The probe now uses fixed deterministic decoding options
  (`PROBE_OPTIONS`: temperature 0, num_predict 8) instead of inheriting the
  chat request's sampling options — a classifier verdict must not vary with
  the UI's temperature setting.

---

## [3.29.1] — 2026-07-08  `main` — intent router: classifier prompt tuned on the live workshop (US-105.1)

### Fixed
- **Intent router prompt improved via US-105.1 workshop**: tested 4 prompt
  variants (V0–V3) against granite4 on 20 calibrated questions
  (10 GENERAL / 10 DOCUMENTARY, FR/EN/zh mix, including I-16 trap). V1
  (positive definition + enriched few-shot) scores documentary 10/10 /
  general 8/10 vs baseline V0 (7/10 general). Eliminated V2–V3 (biased toward
  documentary on binary phrasing). Parsing, fail-open, and timeout (3 s)
  unchanged. Median latency stable at ~191 ms.

---

## [3.29.0] — 2026-07-08  `main` — AiTao can now answer general questions honestly (US-105, ÉPIC-30 phase 2bis)

### Added
- **A pure calculation / reasoning / general-knowledge question is no longer
  contorted through your documents** (incident I-16: "percentage of days
  between two Mondays" was answered through an administrative calendar that
  merely shared the words "Monday"/"day"). A new intent router
  (`src/llm/intent_router.py`) asks a small LLM — the same `verify_model`
  the deep verification pass already uses, no new model to configure —
  whether the question is **documentary** (search your documents, the notary
  pipeline unchanged) or **general** (self-contained). A "general" question
  is answered WITHOUT injecting any document context, prefixed with an
  honest banner: `ℹ️ Réponse générale — pas issue de vos documents.`
- **Fail-open to documentary, never to general**: feature disabled, model
  unavailable, timeout (3 s budget), error, or an unparsable verdict all
  leave the current notary behaviour untouched — the router can only ADD the
  general case, never lose the documentary one. The classification prompt
  itself is biased documentary on any doubt, and sees the last turns so a
  bare follow-up ("quel est son titre ?") is never routed general.
- Config: `[rag] intent_router = true` (default on — synced in schema,
  starter and template). Every verdict is logged with its reason and
  measured latency under `[rag] reliability_debug` (5th invariant).
- The banner is stripped from past assistant turns before history reaches
  the model (like the ⚠️ notices), so the model never imitates it; the
  US-103 "❓ " clarification marker is deliberately NOT touched.
- Golden bench: new committed scenario `question_generale` (I-16 corpus trap
  included) plus a live variant; **zero wrongful "general" routing asserted
  both ways on every turn of every scenario**, mirroring the clarification
  guard. Bench 11 passed + 2 xfailed; full suite 1369 passed; ruff clean.

---

## [3.28.0] — 2026-07-07  `main` — the context gate confronts the retrieved context with your question (US-103, ÉPIC-30 phase 2)

### Added
- **Before answering, AiTao now checks that what it found actually answers
  YOUR question — with three possible outcomes instead of two.** The new
  upstream context gate (`src/llm/context_gate.py`, ÉPIC-30 brique 2) sits
  between retrieval and generation in both chat endpoints:
  1. **Context OK** → the model answers, exactly as before.
  2. **Provably off-topic** → a targeted notary refusal, without calling the
     LLM. Refusal happens ONLY on verbatim proof — the follow-up's referent
     document missing from the retrieved context, or none of the question's
     own salient words present in any retrieved document ("Je n'ai pas
     « télétravail », « Microsoft » dans les documents récupérés…", naming
     exactly what is missing). Never on a similarity score: retrieval scores
     are ranks, not relevance (incident I-12), and a wrongful refusal kills
     trust as much as a false warning banner.
  3. **Provable ambiguity** → ONE short clarification question naming the
     candidate documents ("❓ Tu parles toujours de « fr_bail.md », ou de
     « zh_glass.md » ?") instead of a brilliant answer to the wrong
     question. Strict guardrails: at most one question, NEVER two turns in a
     row (derived statelessly from the conversation history via the stable
     "❓ " marker), and only on deterministic proof — either the session
     holds two candidate referents and the question's own words designate
     the non-held one, or a follow-up ("il", "ce document") arrives with no
     referent at all to hold. Zero questions "just in case".
- This closes the "garbage in, validated garbage out" hole of incident I-11
  (étude §5, manque B): a question whose retrieval only "succeeded" thanks
  to a PAST turn's topic is now refused with the missing terms named, instead
  of being answered from an off-topic document and certified by every
  downstream guard.
- **Every gate verdict is logged with its reason** (5th invariant), at INFO
  while `[rag] reliability_debug` is true (default) and DEBUG otherwise.
  Each clarification asked is additionally logged with the alias and the
  named candidates — these counters feed the "lexique utilisateur"
  return-to-backlog criteria (étude §6.5).
- Golden bench extended (phase 2): new expect keys `clarification`,
  `clarification_names`, `refusal_contains`; the runner threads a
  clarification into history exactly as the API does (so "never two in a
  row" is bench-tested) and asserts **zero superfluous questions on every
  turn of every scenario**, both directions. Three new committed scenarios:
  `clarification_ambigu`, `clarification_jamais_deux`,
  `refus_referent_absent`. `DELIVERED_PHASE` bumped to 2.

### Removed
- **The misleading "Relevance: N%" line is gone from the context shown to
  the model** (C-02, décision Phil 2026-07-07). The percentage was a
  rank-based RRF score (top result ≈ 100% regardless of actual relevance,
  I-12); the model echoed it and users read it as a relevance measure — it
  manufactured confidence with no foundation, the opposite of the notary
  contract. Structural labels (title, path, category, source) remain; the
  raw score is still available programmatically in the API's `rag_context`
  metadata.

---

## [3.27.0] — 2026-07-07  `main` — the conversation dossier holds the referent across turns (US-102, ÉPIC-30 phase 1)

### Added
- **The document you're talking about now survives a follow-up question.**
  Incident I-11: "Quel document parle de 範例玻璃 ?" → "Combien de pages
  contient-il ?" used to restart retrieval from scratch on the follow-up's
  own words, land on a document that only shared a literal word ("pages")
  with the question, and have the whole reliability chain certify that wrong
  answer. AiTao now keeps a "conversation dossier" — the session's current
  referent document — recomputed from the conversation history on every
  request (stateless, deterministic: same history, same dossier). A
  follow-up turn (an anaphor like "il"/"ce document"/它/這個, or a short
  question) holds the dossier's current referent; a new document named
  explicitly still takes over normally.
- **The referent survives an aside, not just the last turn.** Étude
  §6.6 "connexion rationnelle": after "quel document parle de X ?" →
  "combien de pages ?" → a question on a completely different document →
  "reviens au premier document, quel est son titre ?", AiTao now retrieves
  the FIRST document again — never the one from the aside — even though
  that referent is long past the existing 3-turn search window (`US-12
  HISTORY_TURNS`, unchanged for its own narrower purpose). Ordinal
  references ("le premier document" / "le dernier document" / 第一 / 最後)
  are understood.
- **Every dossier decision is logged with its reason** ("referent held: no
  new document name this turn", "referent released: a new document name was
  resolved", "ordinal reference resolved to the session's first referent"),
  at INFO level while `[rag] reliability_debug` is true (the default during
  the ÉPIC-30 rollout, so the real gain can be measured against usage) and
  at DEBUG otherwise — never silenced.
- Purely additive: the dossier only pins its referent on top of the normal
  retrieval for the turn (reusing the existing US-30/US-RAG-name pin/dedup/
  rebuild pipeline) and, when it fires, zeroes the anchoring score of every
  other candidate — nothing is ever removed from the retrieved context, only
  the grounding *signal* is corrected, so a document sharing a stray word
  with the question is never again mistaken for the right one.
- New module `src/llm/conversation_dossier.py` (pure logic, no I/O, no LLM —
  ÉPIC-30 brique 1 is rules-only by design) + its unit tests
  (`tests/unit/test_conversation_dossier.py`, 32 cases: classification,
  full-session replay, the aside-then-return scenario, ambiguous/empty
  sessions, a new name overriding the referent).
- New config flag `[rag] reliability_debug` (default `true`), synced across
  `config_schema.py`, `config.toml.starter` and `config.toml.template`.
- `tests/golden/phase.py::DELIVERED_PHASE` bumped to 1: the `i11_referent`
  and `fil_de_session` golden scenarios (US-101) are real, unmarked passes
  now; `i10_habillage` and `i05_attribution` stay `xfail` until phase 3
  (ÉPIC-30 brique 3, US-104).

---

## [3.26.0] — 2026-07-07  `main` — golden multi-turn conversation bench (US-101, ÉPIC-30 phase 0)

### Added
- **Golden multi-turn conversation bench** (US-101): the single-turn golden
  suite (US-89) now has a multi-turn sibling,
  `tests/integration/test_golden_conversations.py`. Whole conversations —
  not just single questions — are replayed through the real retrieval + gate
  pipeline (`RAGEngine.enrich_messages`, the context-adequacy gate, the
  citation guard, the `answer_validator` fast check) in isolated stores (a
  temp LanceDB dir + a dedicated `test_golden_conversations` Meilisearch
  index), with **no LLM**: a scripted answer stands in for the model so the
  answer-side invariants (banner absent/present, cited source) stay
  deterministic. History is threaded exactly as the API route does
  (`clean_history_messages`, the same notices/banner a real response would
  carry).
- **YAML scenario format** (`tests/fixtures/golden_scenarios/`, loader in
  `tests/golden/scenario_loader.py`): `name`/`phase`/`corpus`/`turns`, each
  turn asserting only the invariants that matter (`context_contains`,
  `context_excludes`, `cited_source`, `refusal`, `banner`,
  `multi_source_note`, `referent_kept`). Fails loudly on any unknown key.
- **8 scenarios v1** from ÉTUDE-FIABILITE.md §8 + §6.6, on 2 new synthetic
  corpus docs (`zh_glass.md`, `fr_barometre.md`): 4 sane cases and I-09 are
  green today (ÉPIC-30 phase 0); I-11 (referent lost on an anaphoric
  follow-up), the "session thread" scenario (§6.6 — a referent must survive
  an aside), I-10 (habillage false positives) and I-05 (wrong-document
  attribution) are wired as **target** behaviour and run `xfail(strict=False)`
  — they reproduce the real incidents today and will flip to a real pass as
  US-102/103/104 ship (module constant `DELIVERED_PHASE` in
  `tests/golden/phase.py` is the single dial to bump).
- **Live variant** `tests/e2e/test_golden_live.py` (marker `golden_live`):
  thin, opt-in field check replaying `corpus: live` scenarios against the
  real running stack (`/v1/chat/completions`, accumulated history), mirroring
  `test_behavior_eval.py` (US-17d). Skips cleanly when the API is unreachable;
  never part of the merge gate — the deterministic runner is.
- I-14 isolated: `test_api_server_lifecycle` (which stops/restarts the REAL
  API server) is now behind an opt-in marker (`stops_server`,
  `AITAO_ALLOW_STOPS_SERVER=true`), mirroring the `live_store` gating
  pattern — a plain `pytest tests/e2e` no longer touches a server in use.

---

## [3.25.0] — 2026-07-03  `main` — reliability check on by default, no more false alarms on summaries (US-092)

### Changed
- **The ⚠️ reliability banner no longer cries wolf on correct summaries and
  translations** (US-092, field report 2026-06-30). The `fast` check now only
  examines sentences carrying a factual detail (number, date, amount); free
  paraphrase — what a summary is made of — is no longer scored, so a good
  summary no longer gets flagged "4 claims not supported by your documents".
- **Figures are now checked verbatim, not by similarity.** A digit token in the
  answer ("2 400", "320 millions", "8,5") must appear (normalised) somewhere in
  the retrieved context. This fixes both directions at once, measured on the
  production embedding model (bge-m3): a correct sentence condensing figures
  from two different chunks is no longer a false alarm, and **an invented
  figure (e.g. 950 M$ instead of 320 M$) is now caught** — the old
  similarity-only check scored it 0.66 and let it pass. Spelled-out facts
  (« trois mois », 三個月) keep the embedding check.
- **`[rag] verify_answer` now defaults to `"fast"`** (was `"off"`). The
  deterministic net is free (~0.1 s), Core, and with the changes above its
  false-positive risk is structurally gone on reformulated prose — reliability
  is acquired, not optional. Explicit `"off"` is honoured; an unrecognised
  value now falls back to `"fast"` instead of silently disabling the net
  (US-076 B principle applied to the level).
- Softer, more precise banner wording: « X détail(s) chiffré(s) de cette
  réponse n'a/ont pas été retrouvé(s) dans vos documents — vérifiez ces points
  dans les sources » (was the accusatory « ne s'appuient pas clairement sur vos
  documents »).

### Fixed
- `config.toml.template` was missing the `[rag] verify_answer` / `verify_model`
  keys (out of sync since v3.18) — the template-sync e2e test failed.

---

## [3.24.0] — 2026-07-02  `main` — every chat request now logs its performance metrics (US-STATS-01)

### Added
- **Every chat request (`/api/chat` and `/v1/chat/completions`, stream and
  non-stream) now emits one structured performance log entry** — the data feed
  for the upcoming stats pipeline (US-STATS-02/03) and the Ollama 0.30→0.31/MLX
  before/after comparison. New `llm/perf_metrics.py` module (`PerfTracker`)
  captures per request: `request_id` (uuid4), `duration_ms`, `ttft_ms`
  (time-to-first-token, streaming only), `model`, `backend`, `ollama_version`
  (cached 5 min — no network call in the critical path), Ollama token counters
  (`eval_count`, `eval_duration_ns`, `prompt_eval_count`,
  `prompt_eval_duration_ns`, `load_duration_ns`), computed `tokens_per_second`,
  `context_chunk_count` (RAG chunks injected), `llm_called` (false for
  deterministic refusals — adequacy gate, named-doc advisory — so they don't
  skew latency stats), `success` and `error_type` (exception class name only).
  **No question or answer text is ever logged** (PRD-AITAO-STATS §3), enforced
  by a privacy non-regression test. Streaming requests previously logged no
  completion entry at all — they now do.
- **The OpenAI-compatible backend (llama.cpp, LM Studio, vLLM) now surfaces
  token counts too**: `usage` (and llama.cpp `timings`) are mapped to the same
  Ollama field names, and streaming requests ask for
  `stream_options.include_usage` so the final usage chunk rides the terminating
  NDJSON line. Servers ignoring the option keep working (fields stay null).

---

## [3.23.6] — 2026-07-02  `main` — warn at startup when the default chat model is slow/risky for RAG (US-097)

### Added
- **AiTao now warns at startup when the configured `default_model` will feel
  stalled on RAG.** Reasoning models burn minutes of hidden "thinking" tokens on
  a long RAG prompt before the first visible word. Measured on the production
  RAG prompt (7k chars, generation capped at 120 tokens): granite4 answers in
  French within 21 s with zero thinking; qwen3.5, qwen3-vl and **both gemma4
  builds (GGUF and MLX)** spend their entire token budget thinking (uncapped,
  qwen3.5 takes ~8 min per answer). New `llm/model_advisor.py` detects known
  reasoning families and non-chat models (e.g. translategemma) by name —
  deterministic and conservative (unknown models are not flagged) — and
  `aitao.sh start/restart` prints a clear advisory (never blocks startup).
  `config.toml.starter` now recommends `granite4:latest` and documents the
  reasoning/non-reasoning distinction with the measured numbers.

---

## [3.23.5] — 2026-07-02  `main` — models no longer refuse to translate retrieved documents

### Fixed
- **A strict model no longer refuses "translate this document" with "I lack a
  translation tool".** The behaviour contract said "answer ONLY from the
  context" and the grounding rules "use ONLY facts stated in the context" — a
  literal model (qwen3.5 at normal temperature) read a translation as *generating
  text absent from the context* and refused. Both now state explicitly that
  TRANSFORMING context content — translating, summarizing, restructuring — is
  part of the job and is not inventing. Validated A/B against qwen3.5 with the
  exact production prompt: full French translation at default temperature, no
  refusal. Anti-hallucination is unchanged (facts must still come from context).
- **Past ⚠️ reliability notices no longer poison follow-up turns.** Chat clients
  send the full transcript back, so the citation-guard/validator warnings —
  "do not trust this reference" — re-entered the model's context attached to its
  own previous answers, entrenching old refusals (observed: two different models
  produced a near-identical refusal because the second copied the first from
  history). Assistant turns are now stripped of their trailing ⚠️ blocks
  (`llm/history_hygiene.py`) before the history is sent to the LLM, in both chat
  routes. The notices still display to the user unchanged.

---

## [3.23.4] — 2026-07-02  `main` — restart shows which version stops and which starts

### Changed
- **`./aitao.sh restart` now proves the upgrade happened.** It prints the version
  of the *running* API server (asked via `/api/health` before stopping — the code
  actually loaded, not the files on disk) next to the version about to start
  (from `pyproject.toml`), so "did I really switch from 3.23.1 to 3.23.3?" is
  answered by the transcript. `start` and the final success line also state the
  version, `stop` reports the PIDs of the processes it stopped, and the API-docs
  link is no longer printed on restart (operational action — versions and PIDs
  matter there, not documentation).

---

## [3.23.3] — 2026-07-02  `main` — a named document stays pinned across turns (US-RAG-name volet C)

### Added
- **A follow-up question keeps the document you named earlier.** "Translate
  X.pdf" then "now complete the translation / any deadlines?" used to lose the
  document: the follow-up doesn't repeat the filename, name-pinning only saw the
  current message, and US-12's history widening never fired (the retrieved
  neighbours looked "good enough" to the adequacy gate) — so the model answered
  from *other* documents. The named-doc resolution now also runs over the recent
  user turns (same `HISTORY_TURNS = 3` window as US-12): a file named in the
  window stays pinned with its full content. Only the name resolver widens to
  history — exact-token and verbatim-phrase pinning stay current-turn only, so a
  stale quote cannot keep pinning documents. Deterministic, coverage-guarded;
  cost ≈ one extra title search per follow-up turn.

---

## [3.23.2] — 2026-07-02  `main` — a named document is sent to the model in full (US-RAG-name fix)

### Fixed
- **"Translate this file" now sees the whole document, not just its first 500
  characters.** A file pinned by name was still formatted with the generic
  500-character context preview, so a multi-page scan reached the model truncated
  mid-page-1 — the model translated the top of page 1 and could not continue.
  Pinned/named documents are now included with their **full content** (bounded by
  the context token budget); other retrieved documents keep the 500-char preview.
  For the Taiwan health-insurance letter, the enriched prompt now contains all
  pages (the establishment notice, the premium/obligation sections, the contact
  form). Ingestion was already complete — this was purely a context-formatting cap.

---

## [3.23.1] — 2026-07-02  `main` — name resolution survives noisy questions (US-RAG-name fix)

### Fixed
- **Naming a file no longer fails when the question mentions a page number.**
  "Give me a translation of the first 2 pages of 20260701_Assurance Maladie
  taiwan.pdf" wrongly answered "this file is not indexed" — although the file was
  indexed. The bare "2" in "2 pages" matched every `…第2版…` (version-2) title in
  the index and crowded the real document out of the title-search candidates, so
  the resolver found nothing and the not-indexed advisory fired by mistake. The
  title search now runs on a denoised query (framing words and tiny tokens like a
  standalone "2" removed) with a larger candidate window; precision is unchanged
  because the title-coverage check still runs on the full query. Validated
  end-to-end: both Taiwan documents now pin correctly for this phrasing.

---

## [3.23.0] — 2026-07-02  `main` — reference a document by its name (US-RAG-name)

### Added
- **You can now ask about a document by its filename.** "Translate
  20260701_Assurance Maladie taiwan.pdf, what is it about?" used to fail: the
  French words of the question pulled French-content documents and the target
  Chinese scan fell out of the top results, so the model reported the file as
  missing. A new deterministic resolver (`llm/named_doc_resolver.py`) matches the
  named document against indexed *titles* (Meilisearch `frequency` strategy on
  the `title` attribute) and **pins its content** into the RAG context, so the
  referenced file always reaches the model — even when its name is buried in a
  question written in another language. Plugs into the existing US-30 pinning
  pipeline (dedup / rebuild / adequacy) and only fires on a genuine name match,
  so ordinary questions are unaffected. All matches are pinned, so a file indexed
  under two Unicode path forms is still fully covered.
- **The assistant now says when a named file is not indexed, and why.** If you
  reference a file that was not retrieved, AiTao answers deterministically
  (no LLM call, no confabulation): a bare filename → "not in your indexed
  documents"; a path inside a configured scan folder → "not indexed yet, run a
  scan"; a path outside the scan folders → "this folder isn't in
  `indexing.include_paths`". Wired into both the Ollama and OpenAI chat routes.

### Fixed
- **A file is no longer indexed twice under different Unicode forms.** macOS
  stores filenames decomposed (NFD) while the same name pasted elsewhere is
  composed (NFC); hashing the raw bytes produced two document IDs for one file.
  Paths are now NFC-normalized before the ID is computed and before storage
  (`indexer_helpers.normalize_path`) — a single cross-platform fix (a no-op on
  Linux/Windows), so re-indexing collapses the duplicate.

---

## [3.22.12] — 2026-07-01  `main` — scanned PDFs with a junk text layer are now OCR'd (US-086, volet 6)

### Fixed
- **A scanned PDF carrying a thin, garbled text layer is now sent to OCR instead
  of indexing the garbage.** A real-world Taiwanese health-insurance scan (4
  full-page JPEGs) shipped with a broken embedded text layer: ~165 characters of
  Latin/digit soup per page where Chinese should be. That density cleared every
  "has enough text" heuristic (>100 chars/page, 100 % page coverage) so OCR was
  skipped, and the mojibake detector (v5) could not catch it either — the junk is
  *Latin*, which is always an "expected" script for a fr/en corpus, and there were
  fewer than its 200-letter trust floor. The document was indexed as garbage and
  the model, unable to read it, wrongly reported the file as missing.
  Two complementary guards now close this gap (belt & braces):
  - **Sparse-scan rule:** when a majority of pages are image-backed yet each
    carries fewer than `SCAN_PAGE_MAX_CHARS` (250) characters, the PDF is treated
    as a scan with a junk layer and routed to OCR (`ocr_reason="scanned_sparse"`).
  - **Missing-expected-script belt:** when a declared non-Latin script (e.g. CJK)
    is *entirely absent* from an image-backed, low-density PDF, the native layer
    is a junk transcription of an un-OCR'd scan and is routed to OCR
    (`ocr_reason="missing_script"`). This catches denser junk layers the sparse
    rule and the mojibake ratio both miss.

  Both guards are conservative: a dense native document (e.g. a French PDF with
  per-page logo images) stays native even when a non-Latin language is declared.
- **OCR no longer inherits the language detected from a garbage native layer.**
  Once a scan was correctly routed to OCR, the indexer still passed the language
  `langdetect` had read from the junk text (`"en"` for the Taiwan scan) as an
  explicit hint — forcing a single wrong-language OCR pass and defeating the
  router's confidence-based candidate selection. The indexer now passes
  `lang=None`, so the router tries the configured `ocr.languages` and keeps the
  most confident pass. For the Taiwan scan, `zh-Hant` now reads the text cleanly
  (`衛生福利部中央健康保險署 函 … 投保金額核定為 42,000元`) and the model can translate it.
- **The stored document language is now taken from the winning OCR pass, not the
  garbage native layer.** `langdetect` read the junk text layer as `"en"` for a
  document that is entirely Traditional Chinese; that stale guess survived OCR and
  was persisted. The indexer now adopts `OCRResult.lang_detected` (the pass the
  router selected by confidence) after a successful OCR, so the Taiwan scan is
  correctly stored as `zh-Hant`.

---

## [3.22.11] — 2026-06-30  `main` — garbled native-PDF text (mojibake) is detected and re-OCR'd (US-086, volet 5 — closes US-086)

### Fixed
- **A text-rich PDF whose extracted text is garbage is now sent to OCR.** A PDF
  exported from a broken-font source (e.g. a PowerPoint) can carry plenty of
  *extractable* text that is actually mojibake (`JENANG최겠합친…` where Latin/Han
  was expected). It passed the "has enough text" checks, so it skipped OCR and the
  garbage got indexed — and the model then confabulated. The PDF extractor now
  measures the script makeup of the native text against the declared
  `ocr.languages`: if too much of it falls outside the expected scripts, the PDF
  is rasterized + OCR'd instead of indexing the garbage.

### Notes
- **Deterministic and opt-in.** Detection only runs when `ocr.languages` is set
  (you declare the language universe; the detector only measures). It is
  conservative — letters only, a 20% out-of-script threshold, a 200-letter floor —
  so legitimate mono- and multilingual documents are never wrongly re-OCR'd.
- **US-086 is now closed** (volets 1, 1b, 2, 3, 4, 5).

---

## [3.22.10] — 2026-06-30  `main` — empty scans get re-OCR'd + `scan run` no longer hides files (US-086, volets 1b & 2)

### Fixed
- **A scanned doc indexed with empty content is now re-OCR'd.** A scanned PDF/image
  whose OCR yielded nothing was indexed in Meilisearch by title only (LanceDB
  rejects empty content), so reconciliation saw it as "present" and never retried
  it — only `--force` fixed it. Reconciliation now uses `healthy_doc_ids` (doc-level
  LanceDB presence, which guarantees real content) as its oracle, so an empty doc is
  treated as not-done and re-enqueued for OCR (bounded by the retry policy).
- **`./aitao.sh scan run` now queues the files it detects.** The scanner marks a file
  "seen" as soon as it detects it, so a manual `scan run` that saved state without
  enqueuing silently hid those files from the daemon. `scan run` now enqueues its new
  + modified files; `--dry-run` previews without touching state or the queue.

---

## [3.22.9] — 2026-06-30  `main` — failures are now visible: `queue failures` + panel summary (US-086, volet 4)

### Added
- **`./aitao.sh queue failures`** lists files that persistently failed to index,
  with reason, retry count and last error — sorted most-retried first, with a
  `--given-up` filter for files that exhausted their retries. These never appear
  in `queue status`/`queue list` (a given-up file is never re-enqueued), so this
  is the only window onto what needs manual attention.
- **A "Persistent failures" section in `queue status`** — total, retryable vs
  given-up, and a breakdown by reason — pulled from the failed-files tracker.

---

## [3.22.8] — 2026-06-30  `main` — bounded index retries: a hopeless file is no longer hammered every scan (US-086, volet 3)

### Added
- **The failed-files tracker is now wired into the worker.** It was complete but
  dead code. The worker records every index failure (path, error, retry count)
  and clears the entry on success or when the file changes (a fresh attempt).
- **`FailedFilesTracker.is_exhausted()`** — answers whether a file has hit the
  retry ceiling.

### Changed
- **Orphan reconciliation is now bounded.** Volet 1 re-enqueues a seen-but-absent
  file every scan; a file that keeps failing would be retried forever. The worker
  now drops reconciled files that have exhausted their retries (`MAX_INDEX_RETRIES`
  = 3), leaving them for inspection (count surfaced in the scan log as `given_up`)
  instead of hammering them.

---

## [3.22.7] — 2026-06-30  `main` — scanner orphan reconciliation: no more silently-lost files (US-086, volet 1)

### Fixed
- **A file that failed to index is no longer skipped forever.** The scanner records a
  file as "seen" (by mtime) as soon as it discovers it — so a one-off failure (OCR
  crash, unmounted cloud volume) left the file marked seen but absent from the stores,
  never to be retried. The scanner now reconciles its state against the documents
  actually present: a seen-but-absent file is re-enqueued on the next scan. The check
  is driven by an injected oracle (`DocumentIndexer.indexed_doc_ids`, the union of the
  doc-level LanceDB and Meilisearch ids), so the scanner stays storage-agnostic.

### Added
- **`DocumentRepository.all_doc_ids()`** on both backends — the bulk inventory powering
  reconciliation.

### Safety
- **An empty/failed inventory disables reconciliation for that cycle.** A cold or
  transiently-unreachable store can never re-enqueue the whole corpus: reconciliation
  only acts when the inventory is non-empty and trustworthy.

---

## [3.22.6] — 2026-06-30  `main` — test/prod store isolation + coherent document deletion (US-088, volets 4-6)

### Added
- **Prod-store guard in the test suite.** A session-wide safety net patches
  `LanceDBClient`, `MeilisearchClient` and `ChunkStore` so any test that builds a
  real client on the PRODUCTION vector-db path or Meilisearch index (or with no
  override, which *resolves* to production) fails immediately. Test runs can no
  longer silently pollute live data. Read-only live smoke tests opt out via
  `@pytest.mark.live_store`.

### Fixed
- **Document deletion now purges chunks too.** `DocumentIndexer.delete_document`
  removed the doc-level LanceDB row and the Meilisearch document but left the
  `chunks` rows behind — orphan chunks that the RAG engine (which reads `chunks`)
  could resurface. It now also drops the document's chunks, and a chunk-purge
  failure makes the whole deletion report failure.

### Documentation
- **Clarified the two LanceDB tables.** `aitao_embeddings` (one embedding per
  document, powers `/api/search`) and `chunks` (one embedding per passage,
  authoritative for RAG/chat) are both alive with distinct granularities. Their
  roles and the "delete must purge both" rule are now documented in the source.

---

## [3.22.5] — 2026-06-30  `main` — temp-path quarantine: no test pollution of prod stores (US-088, volets 1-2)

### Fixed
- **The indexer refuses files under the system temp dir.** Tests previously wrote
  documents whose path was under `tempfile.gettempdir()` into the PROD stores; those
  temp paths later vanished, leaving dead-path chunks served to the model (2303 orphan
  chunks found on 2026-06-19). `DocumentIndexer.index_file` now refuses any path under
  the system temp directory. Tests that legitimately index temp files opt in explicitly
  via `DocumentIndexer(allow_temp_paths=True)`.

### Changed
- **`./aitao.sh index test` is now a component health check only.** It no longer indexes
  a sample document into the live stores (which risked an orphan chunk on partial cleanup —
  the exact bug US-088 addresses). End-to-end indexing is validated by the test suite.
- **Defence-in-depth in the RAG.** The chunk context builder drops any passage whose
  path is under the system temp dir, so a leaked temp/dead path can never reach the
  model. Scoped to the temp dir on purpose — it does not hide legitimately deleted
  files kept by the trash feature (US-28).

---

## [3.22.4] — 2026-06-30  `main` — Config/PathManager responsibility split, no more path "split-brain" (US-098, A4)

### Fixed
- **One source of truth for where data lives.** Previously two systems resolved
  `storage_root` differently: the worker read it from ConfigManager (with env /
  `user.toml` layering) while everything else read it from PathManager (raw TOML,
  no layering). An `APP__PATHS__STORAGE_ROOT` env override or a `user.toml` entry
  was therefore seen by some components but not others — the worker could index in
  one store while search read another. Now **PathManager is the single path
  authority**: it sources the raw setting from the one shared config loader, so all
  layers (config.toml + user.toml + env) are honoured consistently everywhere.

### Changed
- **ConfigManager owns settings, not paths.** It no longer expands `${HOME}` /
  `${storage_root}`; it returns raw values. Resolving any path is PathManager's job
  (`get_storage_root`, `get_logs_dir`, `get_include_paths`, …). All path consumers
  (worker, chunk store, scanner, ingest allow-list, CLI status/config, system prompt,
  intent classifier, dashboard) now go through PathManager.
- PathManager resolves paths and creates directories **lazily** on first access
  instead of at import time (no dir creation as an import side-effect).

### Internal
- Shared, log-free `load_merged_config()` used by both ConfigManager and PathManager
  (single merge, no import cycle). New boundary tests `test_config_path_authority.py`
  including an **architecture guard** that fails if any module outside PathManager
  reads a path setting via ConfigManager — making the responsibility split permanent.

---

## [3.22.3] — 2026-06-30  `main` — single log sink + explicit config fallback (US-098, A2/A5/A6)

### Fixed
- **Logs no longer split across two locations.** The API and MCP launchers
  redirected their process console output to a hard-coded `<project>/logs/`
  directory, in parallel with the structured logs under `storage_root/logs`.
  Both now write to the configured `storage_root/logs` (as `api.console.log`
  and `mcp.console.log`, kept distinct from the structured logs). A single,
  predictable log location — and no more file-lock rotation issues on Windows.
- **No more silent fallback for the storage location.** When `[paths].storage_root`
  is missing from the config, the PathManager now emits a clear warning (stderr,
  MCP-safe) naming the fallback location used and how to set it, instead of
  silently writing data to a guessed path.

### Notes
- The duplicate-singleton risk (two `AitaoPathManager` instances) is neutralised
  by the deterministic root resolution shipped in 3.22.2: any second instance now
  resolves to the same root, hence the same single log sink.

### Migration
- API/MCP console logs move from `<project>/logs/api.log` (and `mcp.log`) to
  `<storage_root>/logs/api.console.log` (and `mcp.console.log`). No action needed;
  the old `<project>/logs/` is no longer written and can be deleted.

---

## [3.22.2] — 2026-06-30  `main` — deterministic, cross-platform path resolution (US-098, A1)

### Fixed
- **PathManager no longer scatters `data/` skeletons in the working directory.**
  Project-root detection no longer falls back to the current working directory:
  it now resolves in order from `AITAO_HOME` → markers above the CWD → markers
  above the **code location** (`__file__`) → a neutral `~/.aitao` fallback — never
  the bare CWD. Launching AiTao from an unrelated directory can no longer create
  stray empty store skeletons (root cause of the scattered `data/` folders).
- **Cross-platform `${HOME}` substitution (Windows).** `${HOME}`/`${USERPROFILE}`
  now fall back to the OS-resolved home (`Path.home()`) instead of being left as a
  literal when `HOME` is unset — configured paths no longer break on Windows.

### Internal
- New failure-mode regression suite `tests/unit/test_pathmanager_robustness.py`
  (foreign-CWD isolation, CWD-independent root, cross-platform home) — written
  test-first; full suite green (1054 passed). Tidied pre-existing lint in
  `lib/path_manager.py` (unused imports, latent undefined `yaml` guard).

---

## [3.22.1] — 2026-06-29  `main` — verify_answer / verify_model documented in the config template (US-076)

### Added
- **`[rag] verify_answer` and `verify_model` shipped in the config template** —
  both keys now appear, documented, in `config.toml.starter` (and the default
  `config.toml`) so the reliability net is discoverable, not hidden. `verify_answer
  = "off"`, `verify_model = "defaut"`.

### Changed
- **`verify_model` accepts the sentinel `"defaut"` (default) — reuse the chat
  model (US-076)** — the template cannot hardcode a real model (the user's model
  is unknown), so it ships `"defaut"`. `"defaut"`, `"default"`, `"auto"` and empty
  are all treated as "reuse the chat model" and short-circuit before any model
  lookup (no spurious "model not available" warning). The schema default is now
  `"defaut"`.

---

## [3.22.0] — 2026-06-29  `main` — US-076 closure: real-model validation, parsing hardened, dedicated verify model

This release closes US-076 phase 3: the deep verification pass was validated on
real local models, its output parsing was hardened, and a dedicated verification
model was added so the reliability net no longer pays the chat model's latency.

### Added
- **`[rag] verify_model` — dedicated model for the deep pass (US-076 B)** — the
  "deep" verification can now run on a model distinct from the chat model. Empty
  (default) reuses the chat model — fully back-compatible. Pointing it at a fast
  non-reasoning model (e.g. `granite4`) brings the deep pass back to ~0.2 s
  instead of the 15–36 s a reasoning chat model spends "thinking". Measured
  end-to-end: chat model `qwen3.5` (reasoning) + `verify_model = "granite4"` →
  verification routed to granite4, **209 ms**, contradiction still caught.
  Resolution is lazy + memoised (a non-deep chat pays no model lookup); a
  configured-but-missing model **falls back silently to the chat model** and logs,
  so a bad config never disables the reliability net. Ollama-oriented (one
  endpoint, many models).

### Fixed
- **Reasoning-model `<think>` scaffolding stripped before verdict parsing
  (US-076)** — the deep verification parser now removes `<think>…</think>` blocks
  (and keeps only the tail after a stray `</think>`) before matching the strict
  `<n>: <verdict>` lines, so a reasoning model's scratch tokens can no longer leak
  a wrong verdict.
- **Unparsed verdicts are now logged, not silently swallowed (US-076)** — a claim
  with no parsable verdict still defaults to `supported` (no news = no flag), but
  the parser now emits a `WARNING` listing the missing claims, so a format-breaking
  model that would otherwise hide every contradiction is observable in the logs.
- **Verdict parser tolerates angle-bracketed numbers (US-076)** — granite4 was
  observed emitting `<1>: CONTRADICTED`; the line regex now accepts `<n>:` (on top
  of `1:`, `1)`, `1 -`). Missing it would silently default the claim to `supported`
  and hide a real contradiction. Found while validating `verify_model` on a real
  model.

### Validated (real model)
- **Deep pass measured end-to-end on Ollama** (granite4, qwen3.5) — confirms it
  catches "right document, wrong figure" contradictions (FR + CJK). Two findings:
  latency is dominated by the chat model (~200 ms on a non-reasoning model vs
  15–36 s on a reasoning model — addressed by `verify_model` above), and verdict
  quality is stochastic. `verify_answer` therefore stays **opt-in / off by
  default**; the default-on re-arbitration is tracked as US-092.

---

## [3.21.0] — 2026-06-26  `main` — LLM "high-reliability" verification pass (US-076 phase 2-C)

### Added
- **Deep verification — LLM pass (US-076 phase 2-C, Premium)** — a new
  `verify_answer = "deep"` level adds a second, LLM-based pass on top of the
  deterministic check: for the claims that passed the deterministic check AND
  carry a number / date / amount, it asks the model — in ONE grouped call —
  whether each is SUPPORTED, CONTRADICTED or ABSENT, and flags the contradicted
  ones. This catches "right document, wrong figure" — the deterministic blind
  spot. New module `answer_validator_llm`, new Premium feature
  `deep_verification`, latency logged. Wired into all four chat paths (native +
  OpenAI, streaming + non-streaming); never breaks a chat (any LLM failure
  degrades to the deterministic result).

### Changed
- **`[rag] verify_answer` is now a level** — `"off"` (default) / `"fast"`
  (deterministic, Core) / `"deep"` (deterministic + LLM, Premium). Back-compatible:
  a legacy boolean maps `true → "fast"`, `false → "off"`.

---

## [3.20.2] — 2026-06-26  `main` — Deterministic grounding check is Core (US-076 phase 2-B)

### Changed
- **Post-generation grounding check moved to Core (US-076)** — the deterministic
  `verify_answer` net no longer requires Premium. Searching, chatting AND the
  deterministic reliability net over text documents are all free (PRD §5). Still
  opt-in (off by default); default-on awaits validation on real model answers.
  `answer_validation` removed from `PREMIUM_FEATURES`. The upcoming LLM
  "high-reliability" pass (phase 2-C) will be the Premium tier.

---

## [3.20.1] — 2026-06-26  `main` — Grounding threshold calibrated (US-076 phase 2-A)

### Changed
- **Grounding threshold calibrated `0.50 → 0.58` (US-076)** — measured on a
  synthetic, secret-free set (`tests/fixtures/grounding_calibration.json` +
  `scripts/calibrate_grounding_threshold.py`): supported claims floor at ~0.64,
  off-topic caps at ~0.54, so 0.58 separates them with margin (0 false positives,
  all off-topic flagged on the set). Contradictions (right topic, wrong figure)
  still score high — the deterministic blind spot the upcoming LLM pass covers.

### Fixed
- **CJK sentences no longer skipped before scoring (US-076)** — a dense Chinese
  clause under 20 characters was discarded by the latin `MIN_SENTENCE_CHARS`
  floor; `_is_checkable` now judges CJK on ideograph count (`MIN_CJK_CHARS`).

---

## [3.20.0] — 2026-06-26  `main` — Freemium scope realigned on the PRD (RAG is Core)

### Fixed
- **Premium scope drift (PRD §5)** — the whole RAG/chat engine was gated Premium
  (`require_premium("rag_chat")` in `RAGEngine.__init__`), so a Core user could
  not search or chat over their own indexed documents. The PRD reserves Premium
  to the document *perimeter* (advanced formats / OCR), **not** the engine. The
  drift was introduced with the v3 LicenseManager and stayed dormant under
  `AITAO_BETA=true`. RAG chat over text documents is now **Core**.

### Changed
- **Premium boundary moved to ingestion.** Advanced document formats
  (`docx, docm, pptx, pptm, xlsx, xlsm, odt, ods, odp, epub`) now require a
  Premium licence **at indexing time** (`DocumentIndexer`), mirroring how scanned
  PDF / image OCR is already gated. Text formats (pdf-with-text, txt, md, log,
  ini, json, code…) stay Core. New Premium feature `advanced_formats`; `rag_chat`
  removed from `PREMIUM_FEATURES`.
- Docs realigned (`CLAUDE.md`, `docs/ARCHITECTURE.md`).

### Notes
- No user impact in beta (`AITAO_BETA=true` bypasses all gates); the change only
  takes effect in a real Core edition.
- `answer_validation` (US-076 grounding check) stays Premium for now; its move to
  Core is scheduled with US-076 phase 2.

---

## [3.19.0] — 2026-06-26  `main` — Post-generation grounding check (US-076 phase 1)

### Added
- **Answer grounding check — `answer_validator` (US-076, phase 1)** — an opt-in
  reliability net that re-reads the model's own answer and flags claims not
  clearly supported by the retrieved documents. Each answer sentence is embedded
  with the bge-m3 model already loaded for retrieval and compared to the
  retrieved context by cosine similarity; sentences below the grounding
  threshold are listed in a notary-style warning appended to the answer. No
  second LLM pass — deterministic, measured at **~60–90 ms** per answer on a
  local machine (reducible to ~25–45 ms by reusing the LanceDB chunk vectors).
- **Config toggle `[rag] verify_answer`** (off by default), wired into both the
  native and OpenAI chat endpoints (streaming and non-streaming). The starter
  config documents the latency/caution trade-off.
- **API grounding score** — non-streaming chat responses expose `grounding_score`
  (0–1) and `grounding_unsupported` (the flagged sentences). Latency is logged on
  every check.
- Premium feature `answer_validation`; the check never breaks a chat response
  (any failure is swallowed and logged).

### Notes
- Phase 1 catches "a claim with no support in the sources", not yet "right
  document, wrong figure" — that finer check is the planned phase 2 LLM pass.
- On streaming responses the textual warning is appended, but the structured
  `grounding_score` field is only populated for non-streaming responses.

---

## [3.18.1] — 2026-06-24  `main` — Abbreviated Chinese titles no longer flagged as fabricated

### Fixed
- **Citation guard false positive on abbreviated titles (US-17c)** — Chinese RH
  documents are named `<title>-第X版-date.pdf`, and the model naturally cites just
  the `<title>` (e.g. `外籍從業人員管理辦法.pdf`). The guard required the full name
  and wrongly marked the title as "not found / fabricated". It now accepts a
  citation that is the **prefix-title** of a retrieved name up to a version/date
  boundary (≥ 4 chars, separator follows) — so it references a real retrieved
  doc, never an invention. Invented names and generic suffix-words are still
  flagged (anti-regression covered by tests).

### Notes
- The **latin over-refusal** observed on 2026-06-22 ("a document about generative
  AI" wrongly refused) is **no longer reproducible** (resolved by the 89-4-bis
  gate scoring); verified on the real index across FR/EN/cross-lingual queries.

---

## [3.18.0] — 2026-06-23  `main` — Smart stream cutoff: stop a stalled model fast

### Added
- **Inter-token (idle) streaming timeout (US-87 p2)** — streaming now uses a short
  READ timeout (`httpx.Timeout(request_timeout, read=stream_idle_timeout)`) that
  measures SILENCE between tokens, not total time. It resets on every token, so a
  slow-but-producing model is never cut; only a true stall is. A model that emits
  no token (observed: `qwen3.5` at 2% CPU) is now cut in ~90 s instead of the
  300 s global timeout, and the user gets a clear message ("⚠️ Le modèle n'a pas
  répondu (silence prolongé). Réessayez, ou changez de modèle.") instead of an
  empty answer with a stuck stop button. Applies to streaming only (native +
  OpenAI-compatible, Ollama + llama.cpp backends); non-streaming keeps the global
  timeout.

### Configuration
- `[llm] stream_idle_timeout` (default `90.0` seconds) — max silence between
  tokens during streaming before the stream is cut.

---

## [3.17.2] — 2026-06-23  `main` — Content-hash dedup guard (closes US-90)

### Added
- **Defensive content dedup (US-90-3)** — when the same content is indexed under
  two paths (a copied file), it now appears only once in the retrieval context,
  the cited sources, and the multi-source notice; `count_token_bearers` likewise
  counts distinct contents. Order-preserving (the first / most-relevant copy is
  kept). No effect on a clean index (the current `_Volumes` index has zero
  duplicates) — a guard so a duplicated file can never surface twice. Closes
  US-90 (cite every source: exact-token pinning, exhaustive citation, token
  excerpts, deterministic notice, true total, dedup).

---

## [3.17.1] — 2026-06-23  `main` — Multi-source notice states the true total

### Fixed
- **Multi-source notice under-counted capped results (US-90-5)** — when a token
  appears in more documents than `[rag] pin_max_docs` (default 7), the notice
  listed the pinned subset but stated that subset's size as the total. It now
  reports the true number of bearer documents and says "au moins N documents
  (les X plus pertinents)" when the listed set is a subset. `RAGEngine.count_token_bearers`
  computes the real count (full-text search + verbatim confirmation, uncapped).
  Verified on the real index: `IP65` 7→"au moins 8", `管理辦法` 8→"au moins 35";
  tokens within the cap (e.g. an email in 3 docs) still report the exact count.

---

## [3.17.0] — 2026-06-23  `main` — Cite every source: deterministic multi-source notice + token-centered excerpts

### Added
- **Deterministic multi-source notice (US-90-4)** — when a distinctive token of
  the question (email, ID, reference, CJK run) is carried by several retrieved
  documents, AiTao appends a deterministic list of ALL of them to the answer,
  independent of what the model cited. Local models routinely name a single
  source even when several hold the answer (granite4 was observed citing 1 of 3);
  this notary-style note guarantees the user sees every source. Works in
  streaming and non-streaming, native and OpenAI-compatible chat.
- **Exhaustive-citation rule (US-90)** — the grounding rules now instruct the
  model that when SEVERAL documents in the context contain the requested
  information (the same email, name, reference, or fact appears in more than
  one), it must cite ALL of them and never imply a single source. Conditional on
  plurality, so single-source answers stay concise.
- **Token-centered excerpts for pinned docs (US-90)** — a document pinned by
  exact-token retrieval (US-89-2) now carries an excerpt centered on the token
  instead of the document head. A long document otherwise gets cropped to its
  first 500 chars by the context formatter, which can hide the very token the
  question is about (e.g. an email deep in a 10k-char PDF) and stop the model
  citing that source. The excerpt guarantees the model sees the token in every
  bearer document. Scoped to exact-token pins only — no change to other queries.

### Fixed
- The "where does this email appear?" case now surfaces all bearer documents to
  the model with the address visible in each (verified on the real index: a
  supplier contact address went from 2 to 4 in-prompt occurrences across its 3
  documents).

---

## [3.16.0] — 2026-06-23  `main` — Exact-token pinning: a distinctive token surfaces every document that holds it

### Added
- **Exact-token pinning (US-89-2)** — a distinctive token typed verbatim in a
  question (an email, code/ID, quoted phrase, or CJK run) now pins **every**
  indexed document that contains it to the retrieval context, not just the top
  semantic hit. This fixes the "where does this email appear?" case where the
  address lived in several documents but only one was surfaced. Distinctiveness
  replaces the exact-phrase pin's ≥ 5-words guard, so an ordinary question pins
  nothing (off-corpus queries are unaffected). The token patterns are shared
  with the query distiller (`structured_tokens`), and matches are confirmed on
  the raw stored content (NFKC-folded), never the cropped search excerpt.

### Configuration
- `[rag] pin_exact_token` (default `true`) toggles the feature; `[rag] pin_max_docs`
  (default `7`) caps how many documents a single token may pin, protecting the
  context budget.

## [3.15.1] — 2026-06-22  `main` — Chinese questions reach the refusal gate; golden non-regression suite

### Fixed
- **Chinese questions bypassed the anti-hallucination gate (US-89-4)** — the
  small-talk classifier counted words via whitespace, but CJK has none, so a
  10-character Chinese question looked like a one-word greeting and was never
  refused (the model answered, sometimes confabulating). A run of ≥4 ideographs
  is now treated as a real query.
- **Adequacy gate scoring (US-89-4)** — an anchored chunk is now scored as
  strong (so a genuinely relevant document is never refused on a noisy ~0.5
  similarity), while a chunk sharing nothing with the query is scored 0; an
  absent term with zero anchors now yields an honest refusal instead of a guess.

### Added
- **Golden non-regression suite (point c)** — `tests/integration/test_golden_retrieval.py`
  replays fixed multilingual questions against a committed fixture corpus
  (`tests/fixtures/golden_corpus`) in isolated stores, asserting deterministic
  retrieval + CJK anchoring + gate behaviour (no LLM). Run by the dedicated
  `Golden retrieval` workflow (Meilisearch service + bge-m3).

---

## [3.15.0] — 2026-06-22  `main` — Multilingual retrieval: query distillation + CJK anchoring gate

### Added
- **Query distillation (US-89-1)** — RAG now searches with the question's
  **salient tokens** instead of the whole sentence. Structured tokens (emails,
  IDs, CJK/Arabic/Cyrillic runs, quoted phrases) are always kept; plain words are
  kept only when **rare in the index** ("salient = rare", via Meilisearch
  document frequency). Multilingual function words no longer dominate the
  embedding and drown the real terms. Deterministic, model-agnostic, no stopword
  lists. Config: `[rag] distill_query` (default on) + `distill_max_doc_ratio`.
- **CJK anchoring gate (US-89-4)** — the relevance gate decomposes Chinese/Japanese
  query runs into **character bigrams** (`事假扣薪` → `事假`, `扣薪`). A document is
  now kept when it contains the *parts* of a compound term, and rejected when it
  shares nothing — so AiTao stops wrongly refusing (or citing an off-topic
  document) on Chinese questions. Latin behaviour is unchanged.

### Fixed
- **Kangxi radicals & spaced ideographs in queries** — query terms are NFKC-
  normalised and spaced CJK is glued, so `粒 ⽶ ⼥ …` is treated as `粒米女…`.

### Changed
- **AiTao's own PM docs can be excluded from the index** — example `exclude_dirs`
  now covers `docs-aitao` (a backlog/PRD that discusses documents pollutes
  retrieval of the user's actual documents).

---

## [3.14.1] — 2026-06-19  `main` — Fix: streaming chat could hang the client (no terminator on stall)

### Fixed
- **Streaming chat could freeze the client UI (US-87)** — the OpenAI-format
  stream (`/v1/chat/completions`, used by OnlyOffice, Open WebUI…) only sent the
  `data: [DONE]` terminator on success. When the model stalled (request timeout
  fired), dropped, or ended the stream without a final `done` chunk, no
  terminator was sent and strict clients waited forever (the "frozen stop
  button"). The terminator is now **always** emitted via a `finally` block; the
  underlying error is logged server-side and the message is closed cleanly. The
  native Ollama NDJSON stream (`/api/chat`) gets the same guarantee. This is
  model-agnostic — it fixes the hang for any model and any client.

---

## [3.14.0] — 2026-06-18  `main` — Multilingual OCR: automatic language selection for scanned documents

### Added
- **Automatic OCR language selection (US-85a)** — when a scanned document's
  language is unknown, OCR no longer guesses blindly. Apple Vision runs one
  pass per configured candidate language and keeps the most confident result
  (scored by confidence × recognised text length), then tags the file with the
  detected language. Scanned Chinese (zh-Hant) documents now extract correctly
  instead of producing garbage. Tesseract keeps a single combined-language pass.
- **`[ocr] languages` now reaches the OCR engine (US-80)** — candidate languages
  (default `["fr", "en", "zh-Hant"]`) are passed as a fallback hint when a
  scanned file yields no detectable language.

### Changed
- **OCR config keys renamed for clarity (US-80)** — `[ocr] provider` → `engine`,
  and a new `engine_order` list replaces the previously non-functional
  `providers`/`default_provider` keys. "engine" is deliberately distinct from
  `[llm] backend` so the two layers can't be confused. Old keys are ignored (no
  error) — update your config to the new names. `qwen_vl` is now opt-in (removed
  from the default `engine_order`; add it back if you run an Ollama vision model).
- **Docs: LLM engine references made agnostic (US-079)** — README no longer
  implies Ollama is required; LM Studio/vLLM (unvalidated) removed, llama.cpp
  kept as the documented OpenAI-compatible alternative.

### Fixed
- **Apple Vision rejected short language tags** — `fr`/`en` are now mapped to the
  region tags Apple Vision requires (`fr-FR`/`en-US`); unsupported tags are
  dropped instead of crashing the call. Previously every macOS OCR attempt
  silently fell back to Tesseract.
- **`OCRRouter.extract_sync` crashed on Python 3.14** — replaced the removed
  `asyncio.get_event_loop()` path with `get_running_loop()` detection.

---

## [3.13.1] — 2026-06-15  `main` — Windows portable: openpyxl + release test gate

### Fixed
- **Windows portable could not extract `.xlsx` files** — `openpyxl` was in
  `pyproject.toml` but missing from `requirements-portable.txt` (x64 and
  ARM64); added so spreadsheet extraction works in portable installs.

### Internal
- **Release workflow now has a test gate** — `release.yml` runs lint + the
  unit suite (`pytest -m "not slow"`) before building/publishing, so a broken
  tag no longer ships. Added a `workflow_dispatch` trigger to run the gate
  manually; build/release steps remain tag-only.

---

## [3.13.0] — 2026-06-15  `main` — US-29 CLI command reference + global help

### Added
- **`./aitao.sh docs`** — generates `docs/COMMANDS.md`, a full command
  reference (every group, subcommand, argument and option) introspected from
  the live CLI, so it can never drift. English output; ready to paste into the
  GitHub wiki.

### Fixed
- **`<command> help` no longer indexes a file named "help"** — a trailing bare
  `help` (or `-h`) after any command is now treated as `--help`, so
  `queue add help` shows help instead of failing with "File not found: help".
- **Discoverability** — built-in help now surfaces each command's options
  (e.g. `queue add --priority`), addressing the US-29 findings.

---

## [3.12.0] — 2026-06-15  `main` — US-30 Filename & exact-phrase retrieval in chat

### Added
- **Naming a file or quoting it verbatim now pins it to the chat context** —
  asking about a file by a distinctive name ("what is jobs.txt about?"), or
  pasting an exact sentence from a document, surfaces that document even when
  semantic chunk retrieval missed it. The match is exact (filename stem, or a
  run of ≥5 consecutive words found verbatim), so it is high-precision and
  additive (it never replaces the normal results, and an off-corpus question
  still gets a reformulation prompt — no hallucination). A pinned document
  already retrieved but scored 0 is upgraded; a pinned trashed file keeps its
  "deleted from disk" note (US-28). Implementation uses fast per-term
  Meilisearch lookups (title-ranked) plus raw content via `get_document`.

### Note
- The filename pinning targets distinctive single-token names; multi-word
  filenames are already well served by normal retrieval (their words anchor),
  so they do not need pinning.

---

## [3.11.0] — 2026-06-15  `main` — US-12 Multi-turn memory (follow-up questions)

### Added
- **Follow-up questions now reuse recent conversation turns** — a terse
  follow-up like "and from Giant?" after "what offers from Micron?" now
  retrieves the right document. Strategy: search with the current question
  alone first (one pass, no pollution); only if the adequacy gate could not
  answer, retry with the last few user turns added to the *search* (the
  question itself is never changed), adopting the wider result only when it is
  genuinely better. The fallback trigger is the gate's own decision
  (`evaluate_refusal`), not an unreliable raw score, so a chunk anchoring on a
  common word can't fake success. Off-corpus follow-ups still get a
  reformulation prompt (no hallucination). `rag_engine.HISTORY_TURNS` controls
  the window (default 3). No cross-conversation leak: the API is stateless,
  each request only sees its own messages.

---

## [3.10.2] — 2026-06-15  `main` — Citation guard: spaced-filename false positive

### Fixed
- **Citation guard wrongly flagged real sources whose filename contains
  spaces** — the citation regex stops at whitespace, so "Ergonomie des
  interfaces - Dunod.pdf" was captured only as its tail "Dunod.pdf" and
  reported as a fabricated source. A citation now also counts as known when it
  is a word-boundary suffix of a retrieved filename; the boundary guard keeps
  it precise ("report.pdf" still ≠ "finalreport.pdf").

---

## [3.10.1] — 2026-06-15  `main` — Reply-language layering + README docs

### Changed
- **Reply-language rules are now layered, in priority order** (refinement of
  US-20): (1) an explicit language request in the user's message always wins,
  (2) else the configured `response_language`, (3) else the question's
  language. Layer 1 is now active even when `response_language` is empty —
  previously an explicit in-message request was only honoured when a forced
  language was configured.
- **README** — added a beginner-friendly "Which language should AiTao reply
  in?" entry to the configuration section documenting `response_language`.

---

## [3.10.0] — 2026-06-15  `main` — US-20 Search quality: titles + reply language

### Added
- **Forced reply language** — new `[identity] response_language` config
  ("français", "English", "中文"…). When set, AiTao answers in that language
  regardless of the documents' or question's language (the user may still
  override per message). Empty = reply in the question's language (previous
  behaviour). Deterministic, all-languages, no detection needed.
- **`./aitao.sh index reindex`** — force re-index of every document already in
  the index, to rebuild stored fields (e.g. titles) after a fix. Files gone
  from disk are skipped (handled by the trash).

### Fixed
- **Wrong document titles** — the title now comes from the filename, not the
  embedded metadata title (a PDF's `/Title` often carries a template or
  software name, e.g. "driver_out" on an AMELI document, so users could not
  recognise their own files). Run `index reindex` to fix already-indexed docs.

### Note
- Filename / exact-phrase retrieval in chat ("what is jobs.txt about?") was
  split out to a dedicated story (needs a fast+reliable filename lookup; the
  reliable path is 6.6 s/message) — to be done together with multi-turn memory.

---

## [3.9.1] — 2026-06-15  `main` — US-19 Meilisearch index cleanup

### Added
- **`./aitao.sh ms prune`** — idempotent maintenance command that deletes
  every Meilisearch index except the canonical one (`[meilisearch] index_name`,
  default `aitao_documents`), removing legacy ghosts (`documents`,
  `indexao_*`) and leftover `test_*` indexes after confirmation (`--yes` to
  skip). Also purges build-artifact documents already in the canonical index
  (paths under `.egg-info` / `.dist-info` / `__pycache__` / caches) — the
  exclusion rule below only prevents future indexing.

### Fixed
- **Build artifacts were indexed** — `aitao.egg-info/top_level.txt` ranked
  first on a PRD query. The scanner now excludes build/packaging directories;
  dotted patterns (`.egg-info`, `.dist-info`) match by suffix so real dir
  names (`aitao.egg-info`, `aitao-2.8.1.dist-info`) are caught, while generic
  names stay exact-match (no over-exclusion of e.g. `rebuild`).
- **Tests left `test_*` indexes on the production Meilisearch** — a
  session-scoped autouse fixture now deletes any `test_*` index created during
  the run; pre-existing indexes are left untouched.

---

## [3.9.0] — 2026-06-15  `main` — US-28 Deleted-files trash lifecycle

### Added
- **Trash registry for deleted files** (`src/indexation/trash.py`) — when the
  scanner detects an indexed file gone from disk, it enters the trash: a JSON
  registry (path → deleted_at) under the storage root. Cross-process safe
  (mtime-based reload so CLI scan, worker daemon and API agree).
- **Visible-but-marked behaviour** (product decision 2026-06-12): trashed
  documents stay searchable.
  - Search API exposes `deleted: true` on each result (`SearchResultItem`).
  - RAG context flags the source to the model.
  - **Deterministic chat notice**: if an answer cites a trashed source, AiTao
    appends "ℹ️ Note : le fichier « … » n'existe plus sur le disque" itself —
    it does not rely on the model heeding the in-context flag (granite4 was
    observed ignoring it). Mirrors the US-17c citation guard for both
    streaming and non-streaming endpoints.
- **Automatic purge** (`trash.purge_expired`) — after
  `indexing.trash_retention_days` (default 30, configurable) a trashed file is
  definitively removed from Meilisearch, LanceDB and the chunk store. Runs on
  every periodic scan; a store failure keeps the entry for the next pass.
- **Automatic restore** — a file that reappears (volume remounted, manual
  restore) is unflagged at the next indexing, even when its content is
  unchanged.

### Fixed
- **Unmounted-volume guard** — a missing `include_paths` root (cloud volume
  offline) no longer marks its whole tree as deleted; files keep their state
  and reconcile when the volume returns.
- **Deletions detected by the CLI/API scan are now recorded** — trash marking
  moved into `scanner.scan()` itself, so every scan path records deletions
  (the worker-only hook missed `./aitao.sh scan run`).
- **Scanner tests no longer clobber the real `scanner_state.json`** — isolated
  via fixture; deletion tests also isolate the trash registry.

---

## [3.8.0] — 2026-06-12  `main` — US-17 complete: anti-hallucination suite + demo gate

US-17 (8 pts) closed: Tier 0 system facts (17a, v3.7.6), conversational gate
(17b, v3.7.7), anti-fabrication & grounded answers (17c, v3.7.8), and the
behavioural evaluation harness (17d, this release).

### Added
- **Behavioural eval harness — demo gate** (`tests/e2e/test_behavior_eval.py`):
  replays the US-17 anti-hallucination checklist against the real stack
  (running API, real indexes, real LLM, zero mock). 7 checks: system date
  with/without greeting prefix (zero citation), greeting never refused,
  off-corpus questions get the instant reformulation (latency-asserted: no
  LLM call), and every citation in corpus-grounded answers must be a
  retrieved source. Assertions are deterministic — gate messages, dates,
  citation audit — never the model's prose.
  Run before any demo:
  `uv run pytest tests/e2e/test_behavior_eval.py -v`
  (model from `llm.default_model`, override with `AITAO_EVAL_MODEL=…`).
  First run: 7 passed in 5m10s (granite4:latest).

---

## [3.7.8] — 2026-06-12  `main` — US-17c Anti-fabrication & grounded answers

### Added
- **Citation guard** (`src/llm/citation_guard.py`) — any file cited by the
  model that is absent from the retrieved context is treated as fabricated:
  removed and replaced by `[source non vérifiée retirée]` (non-streaming), or
  flagged by a trailing ⚠️ warning naming the source (streaming, where sent
  text cannot be recalled). Wired into both chat endpoints.
- **Weak-context reformulation gate** — chunk retrieval is semantic-only and
  its scores are rank-based (an off-corpus query scored 0.554 vs 0.586 for the
  right answer), so the adequacy gate now uses keyword anchoring: a chunk
  counts as relevant only if its document also matches the question's salient
  terms in full-text search, or contains one of those terms itself (semantic
  synonym bridging preserved — "bail" still finds "contrat de location").
  When everything is unanchored, AiTao instantly asks the user to reformulate,
  listing the closest documents — no LLM call.
- **Salient-term extraction** (`src/llm/query_terms.py`) — FR/EN stopword
  filtering so full-text probes are not drowned by question words.
- **Grounding rules** appended to every RAG context section: answer only from
  context, state explicitly when the exact fact (year, amount…) is absent and
  point to the closest available, never extrapolate across years, cite only
  retrieved sources.

### Fixed
- **Temporal patterns** — "Quel jour de la semaine sommes-nous ?" now routes
  to Tier 0 system facts (the pattern required "quel jour sommes nous"
  verbatim).
- **Default LLM timeout 120 s → 300 s** — 12B-class local models with a
  RAG-enriched prompt and multi-turn history routinely exceed 120 s for the
  first token; every observed "timed out" error was this internal limit.
- **OpenAI-format static streams** (refusals/reformulations) now open with a
  `role: assistant` delta per the OpenAI chunk sequence — spec-strict clients
  could drop content sent without it.
- **Test-suite lint debt** — 32 accumulated ruff errors cleaned across 18 test
  files; the pre-commit hook now lints the full `src/` and `tests/` trees.

---

## [3.7.7] — 2026-06-11  `main` — US-17b Conversational gate + re-indexing fix

### Added
- **Greeting-aware gate** (`src/llm/context_adequacy.py`) — a politeness prefix
  ("Bonjour, …") no longer masks the real question behind it: the remainder is
  classified on its own, so greeting+factual questions reach document retrieval
  (and get an honest refusal when nothing relevant exists). Pure greetings,
  including "Bonjour AiTao !", remain small talk.

### Fixed
- **Temporal patterns too broad (US-17a hotfix)** — "Quelle est la date du PRD
  X ?" was classified as a temporal question and never reached RAG. Date
  patterns are now end-anchored; document-date questions search documents.
- **Modified files were never re-indexed** — the scanner detected changes but
  the indexer skipped every known doc_id. The stored `mtime` is now compared
  with the file on disk; modified files re-index automatically (overwrite-safe
  in Meilisearch, LanceDB, and the chunk store). Legacy documents without a
  stored mtime keep the old skip behaviour (no mass re-indexing); `--force`
  refreshes them.
- **`is_indexed()` API check** — the ingest route's "already indexed" check
  compared a raw file path against document IDs and never matched; it now
  derives the real doc_id and honours the freshness check.

---

## [3.7.6] — 2026-06-11  `main` — US-17a Tier 0 system facts + temporal intent

### Added
- **Tier 0 system facts** (`src/llm/system_facts.py`) — current date, time,
  timezone, and AiTao version are now injected into every conversation's
  system prompt, read live from the local system (never from documents).
- **Temporal intent detection** (`src/llm/intent_classifier.py`) — questions
  about today's date / current time / AiTao version ("Quel jour sommes-nous ?",
  "what time is it?") are detected by regex and answered directly from Tier 0
  facts: no document retrieval, no citation. Patterns are deliberately narrow
  so document-date questions ("quelle date figure dans le contrat ?") still go
  through RAG.

### Fixed
- **Hallucinated date + fabricated citation** (June 5th demo) — "Quel jour
  sommes-nous ?" answered "October 21st, 2023" with an invented source; it now
  returns the system date with zero citation, with or without a greeting prefix.

---

## [3.7.5] — 2026-06-11  `main` — US-077 Documentation fixes

### Fixed
- **`docs/CONTINUE-INTEGRATION.md` rewritten** — the previous version described
  virtual model suffixes (`-basic`, `-context`) and `BackendRouter` MLX/Ollama,
  both removed in v3.0.0. New content: plain Ollama model IDs, RAG opt-in via
  `aitao.rag: true` in `requestOptions.extraBodyProperties`, curl verification
  examples for both RAG and non-RAG calls.
- **`CHANGELOG.md` footer** — replaced placeholder owner `your-org` with
  `shamantao`; added comparison links for all 3.x versions (3.0.0 → 3.7.4);
  completed the 2.x chain (2.7.49, 2.8.x, 2.9.x, 2.10.x were missing).

---

## [3.7.4] — 2026-06-11  `main` — US-072 Obsolete dependencies cleanup

### Fixed
- **Windows memory detection** — replaced the deprecated `wmic` subprocess call
  in `src/core/platform.py` with `psutil.virtual_memory()` (already a
  dependency). The function is now cross-platform with a single code path.
- **Duplicate `pytest_configure`** — merged the two identical `pytest_configure`
  hooks in `tests/conftest.py` into one; removed the unreachable copy in
  `tests/e2e/test_startup_chain.py` (pytest never calls hooks from test files).
- **Stale imports in `test_startup_chain.py`** — removed unused `tempfile`,
  `subprocess`, `Optional`, and unused local variables flagged by ruff.

---

## [3.7.3] — 2026-06-11  `main` — US-070 Deterministic language detection

### Fixed
- **`langdetect` non-determinism** — `DetectorFactory.seed = 0` is now set at
  lazy-load time in both `text_extractor.py` and `pdf_extractor.py`. Without
  a fixed seed, the probabilistic algorithm could return different language
  codes for the same text across runs, making `TestLanguageDetection` flaky.
  No behaviour change for users; the detected language remains the same.

---

## [3.7.2] — 2026-06-11  `main` — US-071 Security + Windows fix

### Security
- **`/api/ingest` path traversal** (HTTP 403). The endpoint now rejects any
  file path that does not fall under `config.indexing.include_paths`. Symlinks
  are resolved before the check to prevent bypass. The batch endpoint applies
  the same rule per file (skipped with error, not rejected wholesale). Empty
  `include_paths` allows all paths for backwards compatibility.

### Fixed
- **Windows portable `start-aitao.ps1`** (arm64 + amd64): the uvicorn module
  was `src.api.app:app` (file does not exist). Corrected to `api.main:app`
  with `WorkingDirectory` pointing to `<AITAO_DIR>\src`, matching the
  macOS/Linux lifecycle behaviour.

---

## [3.7.1] — 2026-06-11  `main` — US-073 Correctifs tests + bug MCP search

### Fixed
- **`aitao_search` MCP tool** always returned an empty results list. `_normalise_results`
  only handled `dict` and `list`; the real search engine returns a `HybridSearchResponse`
  Pydantic object. The function now unpacks `.results` and converts each `SearchResult`
  via `.model_dump()`. Regression test added.
- **`pytest tests/` collection** was interrupted by an `ImportError` in
  `tests/e2e/test_virtual_models_e2e.py` (imported `api.virtual_models` removed in v3.0.0).
  File deleted.
- **`tests/test_cli_chat.py`** produced 9 errors + 2 failures (module `cli.chat` removed
  in v3.0.0). File deleted.

### Removed
- `docs/ONLYOFFICE_INTEGRATION.md` — exact duplicate of `ONLYOFFICE-INTEGRATION.md`
  (diff was empty).

---

## [3.7.0] — 2026-06-10  `main` — US-26 Event Bus (extensible pipeline)

### Added
- **`src/core/events.py`** — a tiny in-process publish/subscribe `EventBus`
  (synchronous, per-handler error isolation: a handler that raises is logged and
  skipped, never breaking the publisher). Event-name constants:
  `document.indexed`, `document.updated`, `document.deleted`, `search.executed`.
- **`src/core/event_stats.py`** — `EventStats`, an example subscriber tallying
  pipeline events, demonstrating the bus.

### Changed
- **The pipeline now publishes events**: `DocumentIndexer.index_file` →
  `document.indexed`, `delete_document` → `document.deleted`,
  `HybridSearchEngine.search` → `search.executed`. Subscribers (stats, future
  automations, webhooks) attach with `event_bus.subscribe(...)` — no change to
  the pipeline.

### Notes
- Scope deliberately limited to the bus + emission + one proof subscriber. The
  ticket's "auto-categorization" and "auto-translation" handlers are **not** built
  — they are undefined product features (auto-translation, Premium, is not a
  confirmed need) and remain backlog ideas to specify or drop separately.

---

## [3.6.1] — 2026-06-10  `main` — US-25b Built-in components relocated to src/plugins/

### Changed
- **One single home per component family** (squares off US-25): the built-in OCR
  providers, file extractors and LLM backends now live in
  `src/plugins/{ocr,extractors,llm}/` alongside future drop-ins, loaded by
  `discover_plugins()` — no direct backend imports left in the core.
  - OCR: `native_provider.py`, `qwen_vl_provider.py` (from `ocr/providers/`, removed).
  - Extractors: `simple/office/exif/image` + the `PDFExtractor` wrapper (new
    `plugins/extractors/pdf.py`); `indexation/text_extractor.py` keeps only the
    interface + facade (the PDF analysis engine stays as a library).
  - LLM: `OllamaClient`, `OpenAICompatClient`; the shared wire types/errors move
    to `llm/protocols.py` (single interface home).

### Fixed
- **Pre-commit hook** no longer fails on staged file deletions
  (`--diff-filter=ACMR`).
- **Worker tests made deterministic**: an autouse fixture pins
  `psutil.cpu_percent` below the load-gate threshold for the whole file (the gate
  read the real CPU for 1s per crossing — flaky under load and slow); gate-specific
  tests override it. `test_worker.py` drops from ~16s to ~5s.

---

## [3.6.0] — 2026-06-10  `main` — US-25 Plugin Registry (extensible components)

### Added
- **`src/core/plugin_registry.py`** — a `(kind, name)` plugin registry with a
  `@register` decorator, a lookup that raises an explicit "not found" error
  listing the available names, Premium gating enforced at lookup, and
  `discover_plugins()` which imports the modules under a package so their
  decorators run (idempotent; a broken plugin is logged and skipped, not fatal).
- **`src/plugins/{ocr,llm,extractors}/`** — drop-in folders: a new component is a
  single self-registering file here, auto-discovered with no change to the core.

### Changed
- **OCR providers, LLM backends and file extractors** now self-register with
  `@register(...)` and are looked up by name from the registry instead of
  hard-coded tables / `if-else`. Priority order and selection behaviour are
  preserved; discovered components are appended automatically.

### Notes
- A component registered with a `premium_feature` is gated through
  `LicenseManager` at lookup. OCR's existing centralized Premium gate is unchanged.

---

## [3.5.0] — 2026-06-09  `main` — US-24 Repository Pattern (storage abstraction)

### Added
- **`src/storage/`** — a backend-agnostic `DocumentRepository` Protocol
  (index_document, search, get_document, delete, delete_by_path, get_stats) and
  construction factories `make_lancedb_client` / `make_meilisearch_client`
  (single construction point per backend) plus `make_lancedb_repository` /
  `make_meilisearch_repository`. The existing LanceDB/Meilisearch clients fulfil
  the contract structurally — no wrapper classes.

### Changed
- **`HybridSearchEngine`** and **`DocumentIndexer`** now accept injected
  repositories (constructor injection), defaulting to the real backends. Business
  logic can therefore be tested by swapping in a storage double — the search
  pipeline runs end-to-end against an in-memory repository with no real backend.
- **CLI commands** (database, meilisearch, scan, status, dashboard) and **API
  routes** (health, stats) build their clients via the storage factories instead
  of instantiating them directly (centralized construction).

### Notes
- Backend-specific admin/diagnostic operations (is_healthy, get_version,
  clear_index, get_all_vector_paths, …) intentionally stay on the concrete
  clients, out of the narrow repository contract.

---

## [3.4.1] — 2026-06-09  `main` — US-23b Document object through the indexing pipeline

### Changed
- **`DocumentIndexer.index_file()`** now builds a validated `core.models.Document`
  and passes it through the indexing wrappers to the storage clients, which expose
  a new `index_document(document)` method (a thin adapter over the unchanged
  `add_document()`). The `Document` domain object travels extraction → indexer →
  DB clients with no loose dict — completing the last US-23 acceptance criterion.
- **`core.models.Document`** gains `file_type` and `file_size` fields.

### Fixed
- Cleaned pre-existing lint in `tests/unit/test_ocr_pipeline.py` (unused imports,
  a one-liner helper, an unused variable) surfaced by the pre-commit hook.

---

## [3.4.0] — 2026-06-09  `main` — US-23a Domain Models (Pydantic v2 domain objects)

### Added
- **`src/core/models.py`** — canonical, cross-layer domain entities as Pydantic v2
  models: `Document` (indexed document) and `ChatMessage` / `ChatRole` (chat turn).
  A missing or mistyped field now raises `ValidationError` at construction instead
  of surfacing as a silent `KeyError` downstream.

### Changed
- **Domain objects converted from dataclasses to Pydantic v2** (validation at
  construction; field names/defaults/behaviour unchanged):
  - `search/search_models.py` — `SearchResult`, `SearchFilter`, `HybridSearchResponse`,
    `ChunkSearchResult`, `ChunkSearchResponse`
  - `llm/rag_models.py` — `ContextDocument`, `ContextChunk`, `RAGResult`
  - `indexation/indexer_helpers.py` — `IndexResult`, `BatchIndexResult`
  - `llm/protocols.py` — `GenerationResult`
- **`ChatMessage` unified to a single source of truth**: `llm/protocols.py` now
  re-exports `core.models.ChatMessage` (the local dataclass is gone).

### Removed
- **Dead, duplicated dataclasses in `core/registry.py`** (`Document`, `SearchResult`,
  `SearchResponse`, `IndexResult`) — none were imported anywhere; the live versions
  live in the per-layer model modules.

### Notes
- The remaining US-23 acceptance criterion (thread a `Document` object through the
  indexing pipeline) was descoped to optional **US-23b** — high risk on the indexing
  core for low marginal value (the pipeline already uses explicit named params, not
  loose dicts).

---

## [3.3.0] — 2026-06-09  `main` — US-22 TypedSettings (Pydantic v2 config)

### Added
- **`src/core/config_schema.py`** — 20+ Pydantic v2 models covering every TOML
  section (`LLMConfig`, `SearchConfig`, `RAGConfig`, `IndexingConfig`, etc.).
  `Settings.model_validate(dict)` builds the full typed tree from the merged
  TOML data on every config reload.
- **Typed property accessors on `ConfigManager`** — `.llm`, `.search`, `.rag`,
  `.indexing`, `.api`, `.ocr`, `.chunking`, `.worker`, `.paths`, `.identity`,
  `.translation`, `.categories`, `.resources`, `.log`. The legacy `get()` and
  `get_section()` methods are kept but their docstrings note the typed API as
  preferred.

### Changed
- **~73 `config.get("section.key")` calls** across `src/` replaced with typed
  attribute access (`config.llm.backend`, `config.search.meilisearch.url`, etc.)
  in: `llm/`, `search/`, `indexation/`, `api/`, `mcp_server/`, `ocr/`,
  `cli/commands/`.
- **`pydantic>=2.0`** added to core dependencies (`pyproject.toml` +
  `portable/*/requirements-portable.txt`).

### Fixed (pre-existing wrong config keys caught during migration)
- `meilisearch.url` (wrong section) → `search.meilisearch.url` in
  `dashboard_panels.py`, `dashboard.py`, `_lifecycle_utils.py`.
- `search.semantic_weight` (missing sub-section) → `search.hybrid.semantic_weight`
  in `mcp_server/server.py`.
- `indexation.max_file_size_mb` (wrong section) → `resources.max_file_size_mb`
  in `text_extractor.py`.

---

## [3.2.1] — 2026-06-08  `main` — US-21 Grand ménage (config, zombies, lint, pre-commit)

### Changed
- **`config/config.toml.template` and `config.toml.starter`** reorganized into
  4 labelled blocks (🟢 QUI SUIS-JE / 🟡 MES DOCUMENTS / 🔵 SYSTÈME / ⚙️ AVANCÉ).
  Zero key renames — pure visual improvement. The starter now also documents the
  `openai` backend option.
- **`config.toml.starter`**: fixed broken reference to non-existent
  `config.toml.full` (now points to `config.toml.template`).
- **`docs/ARCHITECTURE.md`** is now the single source of truth for architecture.
  The V2 copy in `docs-aitao/` has been archived as `ARCHITECTURE.v2.archived.md`.

### Removed
- **Zombie files deleted** (zero imports anywhere): `src/llm/ollama_adapter.py`
  (V2 BackendRouter-era adapter), `src/dashboard/` (V2 TUI stub),
  `src/translation/` (V2 translation stub — no implementation).

### Fixed
- **`aitao_stats` MCP tool** — imports corrected: `from storage.meilisearch_client`
  and `from storage.lancedb_client` → `from search.*` (module has been `search/`
  since v3.0.0; the wrong path caused a silent `ModuleNotFoundError` at runtime).
- **ruff `src/`** — 0 errors (was 43): 33 × E402 silenced with targeted
  `# noqa: E402`; 10 manual fixes (F401 unused imports, F841 unused variables,
  E731 lambda-as-assignment).

### Added
- **`ruff` gate in `.githooks/pre-commit`** — staged `.py` files are checked by
  `ruff` before every commit; blocks on new errors.

---

## [3.2.0] — 2026-06-08  `main` — Multi-provider LLM (llama.cpp / OpenAI-compatible)

### Added
- **AiTao no longer depends on a single LLM engine.** A new OpenAI-compatible
  backend (`src/llm/openai_compat_client.py`, drop-in for `OllamaClient`) plus a
  config-driven factory (`src/llm/provider.py`) let you point AiTao at **Ollama,
  llama.cpp (`llama-server`), LM Studio or vLLM**. Switching providers is a single
  config line: `[llm] backend = "ollama" | "openai"` (with `[llm.openai] base_url`
  and `model`). Validated end-to-end: with Ollama **stopped**, `aitao.sh start`
  runs and `/v1/chat/completions` answers via llama.cpp. The context-grounding
  pipeline (identity, profile, documents) is **shared across providers**.
- **`docs-llama.cpp/llama.sh`** — start/stop/status helper to run a GGUF model
  with llama.cpp and point AiTao at it.

### Changed
- **`aitao.sh start` and `status` are now provider-agnostic.** Startup no longer
  hard-checks Ollama and never blocks when the provider is down (the API surfaces
  LLM errors per request). The status panel shows the **configured provider**, its
  URL, the active model and a live health probe — instead of a hardcoded
  "Ollama Server" section.

---

## [3.1.7] — 2026-06-05  `main` — Fix: /api/ingest returned HTTP 500 (Task object in response)

### Fixed
- **`POST /api/ingest` crashed with HTTP 500 on every successful ingestion.**
  `TaskQueue.add_task()` returns a `Task` object, but the route handler passed it
  straight into `IngestResponse.task_id` (typed `str`), so pydantic raised
  `string_type` (`Input should be a valid string … input_value=Task(id=...)`) and the
  endpoint returned 500 — **even though the file was indexed correctly**. The handler
  now passes `task.id`. The existing unit-test mock returned a plain string, which hid
  the bug; it now returns a `Task`-like object and a regression test asserts `task_id`
  is the serialized id string (`tests/unit/test_api.py`).

---

## [3.1.6] — 2026-06-04  `main` — Fix: adequacy gate refused all document retrieval

### Fixed
- **The context adequacy gate (v3.1.3) wrongly refused every document-grounded answer.**
  `RAGEngine.enrich_messages()` returned only `result.context_docs`, which is **empty in chunk
  mode** (the default) — the retrieved context lives in `result.context_chunks`. The gate then
  saw "no context" and refused, so AiTao answered *"Je n'ai pas trouvé…"* even when the document
  was clearly indexed (hybrid search ranked it at score 1.0). `enrich_messages` now surfaces the
  retrieved chunks (as `ContextDocument`s), so the gate **and** the `rag_context` response field
  reflect the real retrieval. Regression test added in `tests/unit/test_rag_engine.py`.
  This unblocks the core "find the source document + summarize" use case.

---

## [3.1.5] — 2026-06-04  `main` — Observability: Ollama health + live queue view

### Added
- **`aitao.sh status` now probes Ollama's inference engine, not just its port.** A TCP ping
  reported "Running" even when Ollama's runner was broken (missing `llama-server` binary after a
  partial brew upgrade) or stuck — which cost real debugging time. `OllamaClient.health()` now
  attempts a 1-token generation and, on failure, status shows **"INFERENCE FAILED"** with the
  underlying error and a remediation hint
  (e.g. `brew upgrade ollama && brew services restart ollama`).
- **`aitao.sh queue status` shows the file(s) currently being indexed** (name + elapsed time),
  and a new **`--watch` / `-w`** flag gives a live view that refreshes as files are processed.

### Changed
- `OllamaClient` exposes `health()` / `OllamaHealth` so other surfaces (dashboard, start) can
  reuse the same check. Covered by `tests/unit/test_ollama_client_config.py`.

---

## [3.1.4] — 2026-06-04  `main` — LLM timeout + skip RAG for config questions

### Fixed
- **The Ollama request timeout was a hard-coded 60s** and could expire on a cold model load or
  a busy machine (e.g. while the initial index scan runs), surfacing as "Error: timed out" in
  chat clients. It is now configurable via `[llm] request_timeout` and defaults to **120s**.

### Changed
- **Config questions and small talk now skip the Tier 2 document search.** "Qui es-tu ?",
  "quels volumes peux-tu indexer ?" and greetings are answered from config / directly — no
  hybrid search, a smaller prompt, and a faster, cleaner answer. Document-grounded questions
  are unaffected. Driven by `context_adequacy.needs_document_retrieval`.

---

## [3.1.3] — 2026-06-03  `main` — Context adequacy gate (explicit refusal)

### Added
- **`src/llm/context_adequacy.py`** (US-DEMO-10). Before calling the LLM, AiTao now refuses a
  *factual* question when no relevant local document was retrieved — returning *"Je n'ai pas
  trouvé cette information dans vos documents…"* **without any LLM call** — instead of letting
  the model invent an answer or borrow an irrelevant document. Config questions
  (identity/scope) and small talk are never refused. Works in streaming and non-streaming
  mode, on both `/v1/chat/completions` and `/api/chat`.
  Covered by `tests/unit/test_context_adequacy.py`.
- Chat responses now expose **`context_source`** (`config` | `docs` | `session` | `none`).

### Fixed
- The intent classifier now recognizes *"tu connais mon nom ? / comment je m'appelle ? /
  what's my name"* as a user-identity question (answered from `who_are_you`) — fixing the model
  pulling an irrelevant document (the "Emo" story) to guess the user's name.

---

## [3.1.2] — 2026-06-03  `main` — Config-priority answers (intent directives)

### Added
- **`src/llm/intent_classifier.py`** (US-DEMO-9). A pure-regex, LLM-free classifier that
  detects configuration questions and injects a precise, config-grounded directive into the
  system prompt:
  - *"Qui suis-je ?" / "what do you know about me"* → answer from `who_are_you`
    (fixes the assistant describing itself instead of the user).
  - *"Quels volumes peux-tu indexer ?" / "what can you index"* → reply with the exact
    `include_paths`, one per line, *do not generalize* (fixes the invented generic-category
    answer from Granite).
  - *"Qui es-tu ?"* → answer as AiTao from the identity section.
  Accent/punctuation-tolerant (FR + EN). Wired into `inject_base_context`.
  Covered by `tests/unit/test_intent_classifier.py`.

---

## [3.1.1] — 2026-06-03  `main` — Hardened anti-hallucination system prompt

### Added
- **`src/llm/system_prompt.py` — `SystemPromptBuilder`** (US-DEMO-8). Assembles the Tier 1
  system prompt, keeping AiTao's *editable* identity (from config) separate from a *fixed,
  non-negotiable* behavioural contract: answer only from the provided context, cite sources,
  refuse plainly when the context is silent (never invent facts/dates/names/figures), and
  always reply in the user's language.

### Changed
- The Tier 1 system prompt now clearly separates **"WHO YOU ARE (AiTao)"** from
  **"ABOUT THE USER (this is NOT you)"** — fixing the assistant answering *as* the user
  (e.g. "Je suis Phil"). Indexed paths are listed under **"INDEXED LOCATIONS"** with an
  explicit *do not generalize* instruction. `_build_rag_system_context()` now delegates to
  `SystemPromptBuilder`. Covered by `tests/unit/test_system_prompt.py`.

---

## [3.1.0] — 2026-06-03  `main` — Context-Grounded by Default + Lifecycle Hardening

### Fixed
- **Context now injected by default on `/v1/chat/completions`** (US-DEMO-7). OpenAI-compatible
  clients (OnlyOffice, Continue.dev…) that cannot send the `aitao.rag` flag now receive AiTao's
  system context automatically. Previously `rag` defaulted to `false`, so these clients got a
  bare Ollama proxy with no identity and no document context.
- **Identity/profile keys were never injected**: `_build_rag_system_context()` read `who_is_Aitao`
  / `who_are_you` as flat keys instead of `identity.who_is_aitao` / `identity.who_are_you`, so the
  assistant's identity and the user profile were always empty in the system prompt. Now read correctly.
- **`aitao.sh` could orphan the API server.** `stop` consulted a PID file in `$TMPDIR`
  (which varies per shell context on macOS), so a server started in one context was never
  killed; the next `start` then silently failed to bind the busy port while reporting success.
  PID files now live in a stable, config-derived directory (`<storage_root>/api.pid`), `stop`
  reaps whatever LISTENS on the API port, and `start` confirms readiness via `/api/health`
  instead of a blind 1-second sleep. Worker PID and the lifecycle `status` view are now
  port/PID-accurate. Covered by `tests/unit/test_lifecycle_process.py`.

### Changed
- **Tier 1 context (identity + profile + indexed paths) decoupled from the Premium RAG engine.**
  It is now always injected (Core edition included) and survives gracefully when document retrieval
  (Tier 2) is unavailable. An explicit `"aitao": {"rag": false}` still yields a transparent proxy.

---

## [3.0.0] — 2026-06-02  `main` — AiTao v3 — Local Document Search Backend

### BREAKING CHANGES
- **Virtual models removed**: `-basic` / `-context` model name suffixes no longer
  exist. `/v1/models` and `/api/tags` now return real Ollama models only.
  Use `"aitao": {"rag": true}` in the request body to enable RAG enrichment.
- **`skip_pull` flag removed** from `aitao start` — AiTao no longer manages
  Ollama model downloads. Use `ollama pull <model>` directly.
- **Config key `[[llm.models]]`** section is now fully ignored (removed).
  Only `llm.default_model` is used.

### Removed
- `src/api/virtual_models.py` — virtual model router (`-basic`, `-context`)
- `src/llm/mlx_backend.py` — Apple Silicon MLX direct inference
- `src/llm/backend_router.py` — MLX/Ollama routing layer
- `src/llm/model_manager.py` — Ollama model lifecycle management
- `src/llm/model_puller.py` — automatic model download
- `src/llm/ollama_models.py` — data models inlined into `ollama_client.py`
- `src/llm/intent_router.py` — factual/rag/summarize intent classification
- `src/llm/factual_query.py` — factual query handler
- `src/llm/summarizer.py` — Map-Reduce summarization pipeline
- `src/cli/chat.py` + `src/cli/chat_repl.py` — CLI chat REPL
- `src/core/model_config.py` — model role configuration
- 8 corresponding test files (~3 000 lines of tests for removed modules)

### Added
- `"aitao"` extension field in `OpenAIChatRequest`:
  `{"rag": true, "folder": "my-folder"}` — RAG opt-in per request
- Startup check: verifies `llm.default_model` is present in Ollama
  (warning only, does not block start)

### Changed
- `/v1/chat/completions` — RAG controlled by `aitao.rag` flag (default: false)
- `/api/chat` — RAG controlled by `rag_enabled` field (default: true, unchanged)
- `/v1/models` + `/api/tags` — pure proxy to Ollama, zero virtual models
- `aitao models status` — lists Ollama models directly
- `aitao models pull/add/remove` — redirects to `ollama` CLI
- `chat_rag_helpers.py` — stripped to 4 core helpers (system context, attachments,
  injection, serialization); intent routing and Map-Reduce removed
- `src/llm/__init__.py` — cleaned, references only remaining modules

### Stats
- **-6 361 lines** of production + test code removed
- **666 tests passing**, 0 failures

---

## [2.10.1] — 2026-04-29  `feat/ocr-images-and-premium-gate`

### Added
- **`src/indexation/image_extractor.py`** — new `ImageExtractor` for raw image
  files (`.png`, `.jpg`, `.jpeg`, `.tiff`, `.tif`, `.bmp`, `.gif`, `.webp`,
  `.heic`, `.heif`). Returns `text=""` + `needs_ocr=True` so the indexer's
  Step 1b runs `OCRRouter` and indexes the OCR text in **both Meilisearch
  (full-text) and LanceDB (vector)**. EXIF metadata (camera, GPS, dimensions)
  is still extracted and merged into the document metadata via internal
  delegation to `EXIFExtractor`.
- **Premium gate on the OCR pipeline** — `src/ocr/router.py` now centrally
  enforces `LicenseManager.require_premium("ocr_advanced")` inside both
  `OCRRouter.extract()` and `OCRRouter.extract_sync()`. This is the SINGLE
  source of truth: PDF rasterisation (Step 1b), raw image OCR, MCP tool
  `aitao_ocr` and any future entry point are all gated by construction —
  no path can bypass it.
- **`tests/unit/test_ocr_pipeline.py`** — 7 new tests covering:
  premium gate (extract, extract_sync, allowed-when-premium), indexer
  graceful skip when premium check fails, `ImageExtractor` extension
  support and `needs_ocr` propagation, and full pipeline integration
  (image → OCR → Meilisearch + LanceDB).

### Changed
- **`src/indexation/text_extractor.py`** — `EXTRACTORS` registry now lists
  `ImageExtractor` BEFORE `EXIFExtractor`, so image files trigger the OCR
  pipeline instead of being limited to EXIF metadata extraction. EXIF data
  remains accessible via `ImageExtractor`'s internal delegation.
- **`src/indexation/indexer.py`** — Step 1b log message and branching now
  explicitly handle `PremiumFeatureError` (raised by the centralised gate):
  when an unlicensed user uploads a scanned PDF or an image, OCR is skipped
  with a clear `OCR skipped` warning and the document is still indexed
  (without text content). No silent failure.

### Security
- **Closes a Premium-feature bypass**: prior to v2.10.1, scanned PDFs
  uploaded via the worker / ingest API were silently OCR-ised and indexed
  on Core (free) installations because the premium check existed only on
  the MCP tool. v2.10.1 plugs this leak at the OCR engine level.

---

## [2.10.0] — 2026-04-29  `feat/ocr-pipeline`

### Added
- **Epic I — OCR Pipeline (US-I-a, US-I-c, US-I-e, US-I-f)** — modular OCR architecture
  replacing PaddleOCR with lightweight, platform-native backends:
  - `src/ocr/interfaces.py` — `OCRProvider` (abstract) + `OCRResult` (dataclass)
  - `src/ocr/providers/native_provider.py` — `MacOSVisionProvider` (Apple Vision / ocrmac,
    M-series Neural Engine) + `TesseractProvider` (Linux / cross-platform)
  - `src/ocr/providers/qwen_vl_provider.py` — `QwenVLProvider` (Ollama qwen3-vl:2b,
    for complex layouts / tables)
  - `src/ocr/router.py` — `OCRRouter` with priority chain, automatic fallback,
    `extract()` (async) + `extract_sync()` (for indexer); config keys
    `ocr.default_provider` and `ocr.providers`
  - `tests/unit/test_ocr_pipeline.py` — 19 unit tests, all green (US-I-f)

### Changed
- **`src/indexation/indexer.py`** — Step 1b added: when `needs_ocr=True` and
  text is empty (scanned PDF), `OCRRouter.extract_sync()` is called before
  Meilisearch + LanceDB indexing. Scanned PDFs are now fully indexed with
  their text content.
- **`src/mcp_server/tools/ocr.py`** — fixed broken import
  (`from processing.ocr_router` → `from ocr.router`); return value aligned
  with `OCRResult` dataclass.
- **`src/ocr/providers/native_provider.py`** — `MacOSVisionProvider`: added
  `_normalize_ocrmac_lang()` + `_OCRMAC_LANG_MAP` to translate BCP-47 tags
  not accepted by Apple Vision into their correct equivalents
  (`zh-TW` → `zh-Hant`, `zh-CN` → `zh-Hans`, `vi-VN` → `vi-VT`,
  `pt-PT` → `pt-BR`, `yue-HK/TW` → `yue-Hant`, etc.) before passing
  `language_preference` to ocrmac — fixes `ValueError: Invalid language
  preference` crash on Traditional Chinese and other regional variants.
- **`pyproject.toml`** — `[premium]` optional deps: PaddleOCR replaced by
  `pdf2image>=1.17.0` + `pytesseract>=0.3.13`; new `[ocr-macos]` group
  adds `ocrmac>=1.0.0` for Apple Vision support.

---

## [2.9.4] — 2026-03-25

### Fixed
- **Test suite: 49 additional test failures fixed** — 6 test files corrected,
  all 356+ tests now pass (0 failures, 1 skipped).
  - `test_models_api.py` (15/15) — removed `src.` import prefix, fixed mock data
    to use `OllamaModel` objects, updated virtual model names (`minicoder`, `vision`).
  - `test_chat_api.py` (22/22) — removed `src.` prefix, accounted for system
    message injection in multi-message tests.
  - `test_cli_chat.py` (16/16) — removed `src.` prefix, fixed `get_chat_history_dir()`
    mock to return real `tmp_path` instead of `MagicMock`.
  - `test_lifecycle_commands.py` (9/9) — complete rewrite to mock current lifecycle
    functions (`_start_api_server`, `_start_worker`, `ModelManager`, etc.).
  - `test_config_template.py` (9/9) — added missing config sections: `worker`, `ocr`,
    `translation`, `categories`, `resources`, `chunking`, `api.auth`, `api.rate_limit`,
    `llm.mlx`, `rag.include_metadata`, `search.meilisearch.*`.
  - `test_virtual_models_e2e.py` (14/14) — rewritten to match current virtual model
    router API and config structure.
- **`/api/tags` route bug** — `list_models_ollama()` returned `List[OllamaModel]`
  but the route called `.get("models", [])` on it; fixed to iterate the list directly.
- **`config.toml.template` completeness** — added all missing sections so template
  matches the full config schema.
- **MCP server version** — bumped `_SERVER_VERSION` from `2.9.0` to `2.9.4`.

---

## [2.9.3] — 2026-03-24

### Fixed
- **Unit test suite: 22 pre-existing failures fixed** — all 808 unit tests now pass
  (0 failures, 1 skipped).
  - **Root cause: dual-import namespace conflict** — test files imported from
    `src.X.Y` while runtime code imported from `X.Y`, creating incompatible
    class instances for Pydantic validation and `isinstance` checks.
  - Fixed imports in `test_health.py` (10), `test_api.py` (2),
    `test_backend_router.py` (2), `test_model_manager.py` (1),
    `test_pdf_extractor.py` (2), `test_text_extractor.py` (2),
    `test_portable_requirements_sync.py` (2), `test_hybrid_search.py` (1, v2.9.2).
  - Added `pythonpath = ["src"]` to `pyproject.toml` pytest config for consistent
    module resolution.
  - Scoped `sys.modules` mocks in `test_api.py` via `patch.dict` to prevent
    test pollution across files.

### Added
- **`DocumentIndexer.is_indexed()`** — public API method wrapping
  `_is_already_indexed()`, fixing `AttributeError` in the ingest route.
- **`fastmcp>=3.1.0`** added to both portable requirements files
  (`arm64` + `amd64`) to match `pyproject.toml` MCP server dependency (US-055).

---

## [2.9.2] — 2026-03-24

### Fixed
- **Hybrid search RRF fusion** — exact full-text matches (e.g. rare phrases like
  "Opaque larval") were buried by semantic results when the document's embedding
  did not align with the query. Root cause: 60/40 semantic/fulltext weight bias
  combined with no compensation for top-ranked fulltext-only results.
  - Rebalanced RRF weights to **50/50** (semantic / fulltext)
  - Added **top-rank fulltext boost** (×1.5) for top-3 Meilisearch results absent
    from semantic results, preventing needle-in-haystack documents from being
    pushed out of the RAG context window
  - Updated unit test `test_default_weights` to match new defaults

---

## [2.9.1] — 2026-03-24

### Added
- **CLI smoke test suite** (`tests/unit/test_cli_smoke.py`) — 56 tests verifying all
  root commands, 14 command groups, subcommand registration, `--help` / `help`
  for every group, and `aitao.sh` `_GROUPS` completeness

### Fixed
- **`aitao.sh`** — `mcp` was missing from `_GROUPS` variable, so `./aitao.sh mcp help`
  did not redirect to `--help` correctly

---

## [2.9.0] — 2026-03-23

### Added — Epic 19: MCP Server (Sprint 8)
- **`src/mcp/` — MCP Server FastMCP** (`server.py`, `tools/`)
  - Transport stdio (Claude Desktop, VS Code Copilot, Windsurf, Cursor)
  - Transport SSE (port 8201)
  - Transport Streamable HTTP (standard MCP 1.0)
  - Outils Free : `aitao_search`, `aitao_ingest`, `aitao_stats`
  - Outils Premium : `aitao_translate`, `aitao_ocr`, `aitao_extract`
- **`./aitao.sh mcp serve`** — commande CLI pour démarrer le serveur MCP
- **`./aitao.sh mcp status`** — état du serveur MCP (transport, PID, outils)
- **`GET /api/mcp/status`** — endpoint JSON état du MCP Server
- **`help` subcommand** on all 13 CLI command groups (`./aitao.sh <group> help`)
- **Virtual IDs column** in `./aitao.sh models status` — shows `alias-basic, alias-context` names
- **MCP test suite** — 60 tests: 37 unit (tools), 7 unit (server), 16 E2E (CLI)
- **`docs/MCP_SERVER.md`** — beginner-friendly step-by-step guide with client configs

### Fixed
- **Meilisearch double-start** — `lifecycle.py` vérifie `/health` avant d'appeler
  `brew services start` ; évite l'erreur `Bootstrap failed: I/O error` (#v2.8-bug2)
- **API `ModuleNotFoundError: No module named 'api'`** — uvicorn lancé
  avec `cwd=src/` et `api.main:app` (était `src.api.main:app`) (#v2.8-bug3)

### Changed
- **`aitao.sh` — uv obligatoire, venv hors cloud** — venv centralisé dans
  `~/.local/share/venvs/aitao` via `scripts/python-env.sh` (fork interne
  de frameworkPython.sh) ; suppression du `.venv` kDrive
- **Meilisearch** migré de 1.35.0 → 1.39.0 (dumpless upgrade)

---

## [2.8.1] — 2026-03-19

### Added
- **`POST /v1/embeddings` — endpoint embeddings compatible OpenAI** (`src/api/routes/embeddings.py`)
  - Expose la génération de vecteurs via Ollama à travers l'API OpenAI-compatible
  - Accepte une chaîne ou un tableau de chaînes en entrée (spec OpenAI standard)
  - Proxy vers Ollama `/api/embeddings` avec le modèle demandé par le client

### Fixed
- **Askimo RAG indexing failure** — `ModelNotFoundException: Embedding model not found` lors de l'indexation de projets RAG via le provider `OPENAI_COMPATIBLE`. L'endpoint `/v1/embeddings` était défini dans le registre mais non implémenté.

---

## [2.8.0] — 2026-03-16

### Added — Epic 18: Onboarding & Simplification UX (Sprint 7)

**Simplification déclaration des modèles LLM**
- `src/core/registry.py` : `ModelRole` réduit à 3 rôles (`chat`, `code`, `vision`)
- `src/core/model_config.py` : `infer_role()` déduit le rôle depuis le nom du modèle ; seul le champ `name` est obligatoire dans `[[llm.models]]`
- `src/llm/model_manager.py` : fallback sur `ollama list` si `[[llm.models]]` est absent du `config.toml`
- `src/api/virtual_models.py` : dérivation automatique des virtual models (`-basic` / `-context`) depuis les alias de modèles

**Template `config.toml` débutant**
- `config/config.toml.starter` : template minimal 3 sections avec explications en langage courant

**README onboarding — parcours 8 étapes non-technicien**
- `README.md` récrit (366 lignes) : pas de tableau de modèles statique, distinction claire `who_are_you` vs `include_paths`, aucun jargon technique (embedding, vector, chunk)

**Wizard `./aitao.sh init`**
- `src/cli/commands/init.py` : wizard interactif 3 étapes (dossiers, modèle, identité utilisateur)
- `src/cli/main.py` : commande `init` enregistrée

### Fixed
- **Dashboard** : affiche tous les modèles Ollama installés (● actif / ○ prêt) — `fix(dashboard)`
- **Status** : ajout d'un ping Ollama ; suppression des compteurs de documents (déjà dans le dashboard) — `fix(status)`

---

## [2.7.49] — 2026-03-16

### Fixed
- **Hotfix: `cryptography` missing from portable requirements** — `ModuleNotFoundError: No module named 'cryptography'` at startup on portable Mac/Windows installations. The `cryptography>=42.0.0` dependency (introduced by US-044 license validation in v2.7.44) was present in `pyproject.toml` but was never added to `portable/arm64/requirements-portable.txt` nor `portable/amd64/requirements-portable.txt`.

### Added
- `tests/unit/test_portable_requirements_sync.py` — automated test that detects any future drift between `pyproject.toml` core dependencies and the portable requirements files.

---

## [2.5.1] — 2026-03-10

### Changed
- **Config format: YAML → TOML** (US-045)
  - `config/config.yaml` and `config/config.yaml.template` replaced by
    `config/config.toml` and `config/config.toml.template`
  - `src/core/config.py` rewritten: layered TOML loader aligned with
    tao-init v1.0.0 (stdlib `tomllib`, env var overrides `APP__SECTION__KEY`)
  - `src/core/pathmanager.py`, `src/indexation/worker.py`,
    `src/indexation/scanner.py`, `src/indexation/text_extractor.py`,
    `src/cli/utils.py`, `src/cli/commands/config.py`,
    `src/search/meilisearch_client.py`, `install.sh` all updated
  - All unit tests and e2e tests migrated to TOML fixtures
- **Version scheme: custom → SemVer** (internal decision 2026-03-10)
  - Former scheme `Major.Sprint.US.patch` replaced by `MAJOR.MINOR.PATCH`
  - `pyproject.toml` version: `2.6.38.3` → `2.5.1`

### Fixed
- YAML `\&` escape error in `config/config.yaml` line 53
  (`${HOME}/pCloudSync/Commun_Tzu-Yin\&Phil/…` — now a non-issue in TOML)

---

## [2.5.0] — 2026-01-28  *(retroactive label — was "Epic 10 ARM64")*

### Added
- ARM64 (Apple Silicon) support for portable installation
- `aitao-Install-Windows/portable/arm64/` setup scripts

---

## [2.4.0] — 2026-01-15  *(retroactive label — was "Sprint 4")*

### Added
- RAG engine (`src/llm/rag_engine.py`)
- LanceDB vector store integration
- Hybrid search (keyword + vector)

---

## [2.3.0] — 2025-12-01  *(retroactive label — was "Sprint 3")*

### Added
- MeiliSearch integration (`src/search/meilisearch_client.py`)
- Background indexation worker (`src/indexation/worker.py`)
- Task queue system

---

## [2.2.0] — 2025-11-01  *(retroactive label — was "Sprint 2 + 2b")*

### Added
- File system scanner (`src/indexation/scanner.py`)
- Text extractor with OCR support (`src/indexation/text_extractor.py`)
- Virtual model routing (`src/api/virtual_models.py`)

---

## [2.1.0] — 2025-10-01  *(retroactive label — was "Sprint 1")*

### Added
- CLI interface (`src/cli/`)
- PathManager (`src/core/pathmanager.py`)
- ConfigManager YAML-based loader (now replaced in 2.5.1)
- Ollama client (`src/llm/ollama_client.py`)

---

## [2.0.0] — 2025-09-01  *(retroactive label — was "Sprint 0")*

### Added
- Initial project scaffold (AiTao v2)
- Docker support (`Dockerfile`)
- Installation script (`install.sh`)

