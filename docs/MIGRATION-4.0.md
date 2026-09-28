# Migrating to AiTao 4.0 (fusion search engine)

AiTao 4.0 replaces the dual-backend retrieval architecture (LanceDB for
semantic search + Meilisearch for full-text, merged with Reciprocal Rank
Fusion) with a **single native Meilisearch hybrid call per retrieval stage**.
Your documents and — crucially — the embedding vectors already computed for
them are **reused as-is**: the migration copies them, it never re-embeds.

This guide covers the one-time migration of an existing 3.x installation.
A fresh 4.0 install needs none of this — just index your documents normally.

---

## What changes

| | 3.x (`rrf`) | 4.0 (`fusion`) |
|---|---|---|
| Document search (`/api/search`) | LanceDB + Meilisearch in parallel, RRF merge | One Meilisearch hybrid call on `aitao_documents` |
| Chat/RAG excerpt retrieval | LanceDB `chunks` table | One Meilisearch hybrid call on `aitao_chunks` |
| Embeddings | bge-m3, computed locally | Same model, same vectors — pushed to Meilisearch (`userProvided` embedder, dim 1024); Meilisearch never re-embeds |
| `/api/stats` `lancedb` field | populated | `null` (field kept, Optional — least breakage) |
| `~/.aitao/data/lancedb` | active store | kept on disk, **read-only**, as the rollback safety net |

## Prerequisites

1. **AiTao 4.0 code installed** (this repository at version 4.0.0).
2. **Meilisearch ≥ 1.49.0 running** (`brew services start meilisearch` or
   equivalent). Hybrid search with a `userProvided` embedder requires no
   experimental flag on 1.49.
3. **The worker stopped or idle** (`./aitao.sh stop`) — the migration reads a
   consistent snapshot of the stores; concurrent indexing would race it.
4. **Do not flip `[search] engine` yet.** Migrate first, switch the engine
   after — the migration works either way, but that order means the live
   system keeps serving exactly what it serves today until you decide
   otherwise.
5. Disk space: the migration builds full copies of both indices alongside the
   live ones (`aitao_documents_next`, `aitao_chunks_next`). Budget roughly the
   current Meilisearch data size again.

## Step 1 — Dry-run (safe, default)

```bash
./aitao.sh migrate-v4
```

This is the **default mode and the only one with zero effect on your live
indices**. It:

- reads the LanceDB `chunks` and `aitao_embeddings` tables (read-only) and
  the live `aitao_documents` Meilisearch index (read-only);
- builds `aitao_documents_next` and `aitao_chunks_next` **alongside** the live
  indices, with the 4.0 settings (US-094) and the `userProvided` embedder,
  and fills them with your documents/excerpts and their **existing vectors —
  zero re-embedding**;
- probes a sample of migrated documents (present? vector attached? does a
  hybrid search find them back?);
- prints the report. **It never swaps.**

You can re-run the dry-run as many times as you want; each run rebuilds the
`_next` indices from scratch.

## Step 2 — Read the report

```
Documents
  Total (live index):                          247
  Copied (vector reused, zero re-embed):       244
    ...without a source vector:                  9
  Needing reindex (CJK gluing changed content):  3
    ...requeued for reindex:                     0   (dry-run never requeues)
    ...source file missing (report only):        1
Excerpts (chunks)
  Total (source LanceDB):                     1559
  Copied (vector reused, zero re-embed):      1541
  Excluded (belong to a reindex-needed doc):    18
```

- **Copied** — the vast majority: content unchanged, stored vector
  trustworthy, copied verbatim.
- **Without a source vector** — documents that never had a doc-level vector
  in LanceDB (a pre-existing gap, not created by the migration). They are
  migrated lexical-only: full-text search finds them, the semantic leg of the
  hybrid search does not, until they are re-indexed.
- **Needing reindex** — documents whose stored content *changes* when the 4.0
  CJK gluing rule is applied (stray spaces between Chinese ideographs, an
  OCR/extraction artifact). Their stored vector was computed on the gapped
  text, so it is **not** copied. They are pushed into `_next` with the glued
  content and no vector (lexical-only), and their old excerpts are excluded.
  On `--swap`, every such document whose source file still exists is
  automatically queued for a full re-index (extraction → gluing → chunking →
  embedding) through the normal pipeline; start the worker and they heal.
- **Source file missing** — needing reindex, but the original file is gone.
  Listed so you can decide (restore the file and re-index, or accept
  lexical-only search for it).
- **Sample verification** — for each probed document: `present` (it is in
  `_next`), `vector` (a non-null vector is attached), `search_found` (a
  hybrid search on its own title ranks it in the top 5).

## Step 3 — Swap

```bash
./aitao.sh migrate-v4 --swap        # interactive confirmation
./aitao.sh migrate-v4 --swap --yes  # non-interactive
```

Rebuilds `_next` fresh (so it reflects the stores at swap time), shows the
report, asks for confirmation, then atomically swaps `aitao_documents` ↔
`aitao_documents_next` and `aitao_chunks` ↔ `aitao_chunks_next`
(Meilisearch `swapIndexes` — zero read/write downtime). Only **after** the
swap does it enqueue the CJK-affected files for re-indexing.

Then switch the engine and restart:

```toml
# config/config.toml
[search]
engine = "fusion"
```

```bash
./aitao.sh restart
```

## Rollback

```bash
./aitao.sh migrate-v4 --rollback
```

Swapping the same index pair twice restores the original assignment — your
pre-migration content is still sitting under the `_next` names after a swap,
untouched. Set `[search] engine` back to `rrf` (or leave it — `rrf` reads
LanceDB, which was never modified) and restart.

## What about `~/.aitao/data/lancedb`?

The migration **never writes to or deletes** the LanceDB directory. It stays
on disk as a read-only safety net:

- Keep it until you have used 4.0 in fusion mode long enough to trust it.
- After that, it is safe to delete (`rm -rf ~/.aitao/data/lancedb`) to
  reclaim disk space. Nothing in fusion mode reads it.
- If you delete it, `--rollback` still restores the pre-migration Meilisearch
  indices, but a return to the 3.x `rrf` engine would require a full
  re-index.

## Troubleshooting

- **"Meilisearch not available"** — start it first; the migration needs the
  live server for both reading (documents index) and writing (`_next`
  indices).
- **Sample check shows `search_found=False`** — Meilisearch may still be
  indexing the `_next` documents; re-run the dry-run. If it persists for many
  samples, do not swap; report the issue.
- **Counts differ between dry-runs** — the worker is probably running and
  indexing concurrently. Stop it (`./aitao.sh stop`) and re-run.
