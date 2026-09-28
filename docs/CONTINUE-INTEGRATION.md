# Connecting Continue (VS Code) to AiTao

This guide explains how to configure the [Continue](https://www.continue.dev)
VS Code extension to use AiTao as its LLM backend, giving you local,
document-aware AI assistance directly inside your editor.

## How it works

AiTao exposes an **OpenAI-compatible REST API** (`/v1/chat/completions`,
`/v1/models`) on `http://localhost:8200`. Continue supports any server that
speaks the OpenAI protocol — so AiTao is simply registered with
`provider: openai` and a custom `apiBase`.

```
VS Code (Continue)  →  AiTao API (:8200)  →  Ollama / llama.cpp  →  your local model
                                          ↗
                               LanceDB + Meilisearch  (RAG index, opt-in)
```

Continue sends chat and code-completion requests to AiTao. By default AiTao
acts as a transparent proxy to the underlying model. Add the `aitao` extension
field to the request body to activate RAG context enrichment.

---

## Prerequisites

| Requirement | Check |
|---|---|
| AiTao running | `curl http://localhost:8200/v1/models` returns a JSON list |
| Continue extension installed | Search `Continue` in the VS Code Extensions panel |
| Ollama running | `ollama list` shows at least one model |

Start AiTao if needed:
```bash
cd /path/to/aitao
./aitao.sh start   # or: python aitao_cli.py start
```

---

## Step 1 — Discover your model IDs

AiTao exposes the real model IDs from your Ollama instance directly — there is
no additional naming layer.

```bash
curl http://localhost:8200/v1/models | python3 -m json.tool
```

Example output:
```json
{
  "data": [
    { "id": "qwen2.5:7b" },
    { "id": "qwen2.5-coder:7b" },
    { "id": "mistral:latest" }
  ]
}
```

Note the ID of the model you want to use in the next step.

---

## Step 2 — Edit `~/.continue/config.yaml`

Continue's main configuration lives at `~/.continue/config.yaml`.

Below are two setups. Replace every `<model-id>` placeholder with an actual
model ID from Step 1.

### Without RAG (fastest — pure proxy)

```yaml
name: AiTao Local
version: 1.0.0
schema: v1

models:
  - name: AiTao — <model-id>
    provider: openai
    model: <model-id>
    apiBase: http://127.0.0.1:8200/v1
    apiKey: aitao-local           # any non-empty string; AiTao auth is off by default
    roles:
      - chat
      - edit
      - apply

tabAutocompleteModel:
  provider: openai
  model: <model-id>
  apiBase: http://127.0.0.1:8200/v1
  apiKey: aitao-local

context:
  - provider: code
  - provider: docs
  - provider: diff
  - provider: terminal
  - provider: problems
  - provider: folder
  - provider: codebase
```

### With RAG — document-aware chat

Add a second model entry and pass `aitao.rag: true` via `extraBodyProperties`.
Continue injects this field into every chat request, telling AiTao to retrieve
relevant excerpts from your indexed documents before calling the model.

```yaml
name: AiTao Local
version: 1.0.0
schema: v1

models:
  # --- Chat model with RAG ---
  - name: AiTao — <model-id> with context
    provider: openai
    model: <model-id>
    apiBase: http://127.0.0.1:8200/v1
    apiKey: aitao-local
    requestOptions:
      extraBodyProperties:
        aitao:
          rag: true               # enable AiTao RAG enrichment for this model
    roles:
      - chat

  # --- Fast model without RAG (inline completions) ---
  - name: AiTao — <model-id>
    provider: openai
    model: <model-id>
    apiBase: http://127.0.0.1:8200/v1
    apiKey: aitao-local
    roles:
      - edit
      - apply

tabAutocompleteModel:
  provider: openai
  model: <model-id>              # RAG not set — keeps autocomplete fast
  apiBase: http://127.0.0.1:8200/v1
  apiKey: aitao-local

context:
  - provider: code
  - provider: docs
  - provider: diff
  - provider: terminal
  - provider: problems
  - provider: folder
  - provider: codebase
```

> **Tip:** You can add as many model entries as you like. Continue lets you
> switch between them from the chat panel. A common pattern is to have one
> model with `rag: true` for document-aware chat and one without RAG for fast
> inline editing and autocomplete.

---

## Step 3 — Reload Continue

After saving `config.yaml`, reload the extension:

1. Open the Continue panel (sidebar icon or `Cmd+Shift+P` → *Continue: Focus on Continue View*)
2. If the model list does not refresh automatically, run:
   `Cmd+Shift+P` → *Developer: Reload Window*

---

## Available configurations

| Configuration | RAG | Best for |
|---|---|---|
| Model ID only | ❌ disabled | Fast inline completions, quick chat |
| Model ID + `aitao.rag: true` | ✅ enabled | Chat grounded in your indexed documents |
| Direct Ollama (see below) | ❌ (bypasses AiTao) | Ultra-low-latency fill-in-the-middle |

---

## Usage examples

### Example 1 — Chat with document context

1. In the Continue panel, select the model configured with `aitao.rag: true`.
2. Type a question such as:
   > "What are the main modules of this project and how do they interact?"

AiTao's RAG engine retrieves relevant excerpts from your indexed documents,
injects them as system context, and the model answers grounded in your actual
codebase and docs.

---

### Example 2 — Tab autocomplete

With `tabAutocompleteModel` pointing to a plain model ID (no `rag: true`),
Continue sends partial code to AiTao for inline completion suggestions.
Leaving RAG disabled keeps autocomplete latency low.

To verify autocomplete is working: open any source file, type a function
signature, and pause — Continue should suggest a completion within a second or
two.

---

### Example 3 — Edit a selection with a custom instruction

1. Select a block of code in the editor.
2. Press `Cmd+I` (or the configured shortcut).
3. Type an instruction, e.g.:
   > "Add input validation and return early if the argument is null."

Continue sends the selection + instruction to the model configured with the
`edit` role.

---

## Tab autocomplete via Ollama directly (alternative)

If you prefer to connect tab autocomplete **directly to Ollama** (lower latency,
no AiTao middleware):

```yaml
tabAutocompleteModel:
  provider: ollama
  model: <ollama-model-id>
  apiBase: http://127.0.0.1:11434
```

This is useful for very small, fast fill-in-the-middle models (e.g.
`qwen2.5-coder:1.5b`). It bypasses AiTao entirely for completions while chat
still goes through AiTao.

---

## If you enable AiTao API authentication

AiTao's API auth is **disabled by default**. If you later enable it in
`config/config.toml` under `[api.auth]`, replace `aitao-local` with your real
API key in every `apiKey:` field of `config.yaml`.

---

## Troubleshooting

### Continue shows "No models available" or a connection error
- Confirm AiTao is running: `curl http://127.0.0.1:8200/v1/models`
- Check the `apiBase` URL in `config.yaml` matches AiTao's port (default `8200`)
- **Always use `127.0.0.1` instead of `localhost`** in `apiBase`. On macOS, Windows 10/11,
  and modern Linux, `localhost` resolves to `::1` (IPv6) first. If AiTao is not configured
  for dual-stack, Node.js-based clients (Continue, OpenClaw) get an immediate
  "Connection refused" without falling back to IPv4. Using `127.0.0.1` forces IPv4
  and avoids this entirely. *(AiTao can also be fixed on the server side by setting
  `host = "::"` in `config/config.toml` — this enables dual-stack and makes both
  `localhost` and `127.0.0.1` work.)*
- Reload the window: `Cmd+Shift+P` → *Developer: Reload Window*

### The model ID is rejected
- Run `curl http://localhost:8200/v1/models` to list valid IDs
- Ensure the `model:` value in `config.yaml` matches exactly (case-sensitive)

### Responses are empty or truncated
- If the underlying Ollama model uses a **thinking mode** (internal reasoning
  tokens), it produces an empty `content` field during the reasoning phase.
  Full output arrives after the thinking phase completes — this is normal.
  Avoid setting `maxTokens` below ~200 if you encounter this.

### Verify the AiTao API directly
```bash
# List models
curl http://127.0.0.1:8200/v1/models

# Send a test message (no RAG)
curl -s -X POST http://127.0.0.1:8200/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "<model-id>",
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": false
  }' | python3 -m json.tool

# Send a test message (with RAG)
curl -s -X POST http://127.0.0.1:8200/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "<model-id>",
    "messages": [{"role": "user", "content": "What documents are indexed?"}],
    "stream": false,
    "aitao": {"rag": true}
  }' | python3 -m json.tool
```
