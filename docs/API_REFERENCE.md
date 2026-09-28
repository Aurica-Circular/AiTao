# AiTao — API Reference

Complete reference for all native AiTao and OpenAI-compatible endpoints.

**Base URL:** `http://localhost:8200`

---

## Overview

AiTao exposes two API families:

- **`/api/*`** — native AiTao endpoints (search, ingest, stats, health)
- **`/v1/*`** — OpenAI-compatible endpoints (drop-in replacement for OpenAI SDK)

Both are on the same server. Pick whichever suits your client.

---

## Native AiTao API (`/api/*`)

### Health & Status

#### `GET /api/health`

Health check.

```bash
curl http://localhost:8200/api/health
```

Response:
```json
{
  "status": "ok",
  "services": {
    "api": "running",
    "worker": "running",
    "meilisearch": "running",
    "ollama": "running"
  }
}
```

---

#### `GET /api/stats`

Indexing statistics.

```bash
curl http://localhost:8200/api/stats
```

Response:
```json
{
  "total_documents": 42,
  "total_chunks": 1203,
  "indexed_at": "2026-06-15T14:32:00Z",
  "index_size_mb": 15.4,
  "queue_length": 3
}
```

---

### Search

#### `POST /api/search`

Hybrid search (full-text + semantic).

```bash
curl -X POST http://localhost:8200/api/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "meeting notes June",
    "limit": 10,
    "threshold": 0.5
  }'
```

Request body:
```json
{
  "query": "string — search text",
  "limit": "number — max results (default 10)",
  "threshold": "number — relevance threshold (0.0–1.0, default 0.5)"
}
```

Response:
```json
{
  "query": "meeting notes June",
  "results": [
    {
      "id": "doc-001-chunk-5",
      "document_title": "June_Meeting_Notes.md",
      "chunk_text": "...",
      "score": 0.87,
      "source_path": "/Users/.../June_Meeting_Notes.md"
    }
  ],
  "count": 3,
  "execution_time_ms": 145
}
```

---

### Indexing

#### `POST /api/index`

Index a single file.

```bash
curl -X POST http://localhost:8200/api/index \
  -H "Content-Type: application/json" \
  -d '{
    "file_path": "/Users/yourname/Documents/report.pdf"
  }'
```

Request body:
```json
{
  "file_path": "string — absolute path to document",
  "force": "boolean — re-index even if unchanged (default false)"
}
```

Response:
```json
{
  "status": "queued",
  "file_path": "/Users/yourname/Documents/report.pdf",
  "queue_position": 2
}
```

---

#### `POST /api/ingest`

Trigger a full scan of configured `include_paths` and queue new/changed files.

```bash
curl -X POST http://localhost:8200/api/ingest
```

Response:
```json
{
  "status": "scanning",
  "message": "Scanner started for configured paths"
}
```

---

#### `DELETE /api/documents/{document_id}`

Delete a document and all its chunks from indexes.

```bash
curl -X DELETE http://localhost:8200/api/documents/doc-001
```

Response:
```json
{
  "status": "deleted",
  "document_id": "doc-001",
  "chunks_removed": 28
}
```

---

### Chat (RAG)

#### `POST /api/chat`

Ask a question grounded in your indexed documents (RAG).

```bash
curl -X POST http://localhost:8200/api/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Summarize the Q2 revenue report",
    "context_limit": 2000
  }'
```

Request body:
```json
{
  "message": "string — user question",
  "context_limit": "number — max tokens for retrieval context (default 2000)"
}
```

Response:
```json
{
  "answer": "According to the Q2 report dated June 15...",
  "context": {
    "documents": [
      {
        "title": "Q2_Revenue_Report.pdf",
        "chunks_used": 2
      }
    ],
    "total_chunks": 2
  },
  "execution_time_ms": 523
}
```

**Note:** If no relevant documents are found, returns an explicit refusal:
```json
{
  "answer": "I couldn't find information about 'X' in your documents.",
  "context": {
    "documents": [],
    "total_chunks": 0
  }
}
```

---

## OpenAI-Compatible API (`/v1/*`)

Use any OpenAI SDK (Python, Node.js, etc.) against AiTao.

### Base Configuration

Point your OpenAI client to `http://localhost:8200`:

**Python:**
```python
from openai import OpenAI

client = OpenAI(
    api_key="not-needed",
    base_url="http://localhost:8200/v1"
)

response = client.chat.completions.create(
    model="default",
    messages=[
        {"role": "user", "content": "Summarize my documents"}
    ]
)
print(response.choices[0].message.content)
```

**Node.js / TypeScript:**
```typescript
import OpenAI from "openai";

const client = new OpenAI({
  apiKey: "not-needed",
  baseURL: "http://localhost:8200/v1",
});

const response = await client.chat.completions.create({
  model: "default",
  messages: [
    { role: "user", content: "Summarize my documents" },
  ],
});
console.log(response.choices[0].message.content);
```

---

### Endpoints

#### `POST /v1/chat/completions`

OpenAI-compatible chat completion. Supports streaming.

```bash
curl -X POST http://localhost:8200/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "default",
    "messages": [
      {"role": "user", "content": "What are my document titles?"}
    ],
    "temperature": 0.7,
    "stream": false
  }'
```

Request body (OpenAI format):
```json
{
  "model": "string — model name (ignored; uses configured default)",
  "messages": [
    {
      "role": "user|assistant|system",
      "content": "string"
    }
  ],
  "temperature": "number — 0.0–2.0 (default 0.7)",
  "max_tokens": "number — max completion length",
  "stream": "boolean — stream response (default false)"
}
```

Response (non-streaming):
```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1718460000,
  "model": "default",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Based on your documents, the titles are..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 150,
    "completion_tokens": 120,
    "total_tokens": 270
  }
}
```

**Streaming (SSE):**
Set `"stream": true`. Server sends `data: {"choices":[{"delta":{"content":"..."}}]}` events.

---

#### `GET /v1/models`

List available models.

```bash
curl http://localhost:8200/v1/models
```

Response:
```json
{
  "object": "list",
  "data": [
    {
      "id": "default",
      "object": "model",
      "owned_by": "aitao"
    }
  ]
}
```

---

#### `POST /v1/embeddings`

Generate embeddings for text (uses configured embedding model, `BAAI/bge-m3` by default).

```bash
curl -X POST http://localhost:8200/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{
    "model": "embedding",
    "input": "This is sample text"
  }'
```

Request body:
```json
{
  "model": "string — ignored",
  "input": "string or array of strings"
}
```

Response:
```json
{
  "object": "list",
  "data": [
    {
      "object": "embedding",
      "embedding": [0.123, -0.456, ...],
      "index": 0
    }
  ],
  "model": "BAAI/bge-m3",
  "usage": {
    "prompt_tokens": 5,
    "total_tokens": 5
  }
}
```

---

## Error Handling

All endpoints return standard HTTP status codes:

| Status | Meaning |
|--------|---------|
| `200` | Success |
| `400` | Bad request (invalid JSON, missing required field) |
| `404` | Not found (document not found) |
| `409` | Conflict (e.g., duplicate file in queue) |
| `429` | Rate limited |
| `500` | Server error (see logs) |
| `503` | Service unavailable (Meilisearch, Ollama down) |

Error response body:
```json
{
  "error": "human-readable message",
  "detail": "optional technical detail"
}
```

---

## Rate Limiting & Caching

- **No rate limits** on native AiTao API in local mode.
- **Search results** are cached in memory for 5 minutes (exact query match).
- **Indexing queue** is in-memory; survives restarts via persistence layer.

---

## Authentication

- **Local mode:** None required. Requests from localhost are accepted.
- **Remote mode** (if exposed): Set `[api] require_auth` to `true` in config; clients must pass `Authorization: Bearer <token>`.

---

## Related

- [QUICKSTART.md](QUICKSTART.md) — Get running in 5 minutes
- [ARCHITECTURE.md](ARCHITECTURE.md) — How each layer works
- [COMMANDS.md](COMMANDS.md) — CLI command reference
