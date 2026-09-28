# Connecting OpenClaw to AiTao

This guide explains how to configure [OpenClaw](https://docs.openclaw.ai) to use
AiTao as its LLM backend, replacing cloud providers with your local, private,
document-aware AI.

## How it works

AiTao exposes an **OpenAI-compatible REST API** (`/v1/chat/completions`,
`/v1/models`) on `http://127.0.0.1:8200`. OpenClaw supports any server that
speaks the OpenAI completions protocol — so AiTao can be added as a custom
provider using the `openai-completions` API type.

```
OpenClaw gateway  →  AiTao API (:8200)  →  Ollama / MLX  →  your local model
                                        ↗
                              LanceDB + Meilisearch  (RAG index)
```

OpenClaw sends chat requests to AiTao. AiTao optionally enriches them with
context retrieved from your indexed documents (RAG), then forwards the augmented
prompt to Ollama or MLX for generation.

---

## Prerequisites

| Requirement | Check |
|---|---|
| AiTao running | `curl http://127.0.0.1:8200/v1/models` returns a JSON list |
| OpenClaw installed | `openclaw --version` |
| Ollama running | `ollama list` shows at least one model |

Start AiTao if needed:
```bash
cd /path/to/aitao
./aitao.sh start   # or: python aitao_cli.py start
```

---

## Step 1 — Discover your AiTao model IDs

AiTao exposes *virtual models* whose IDs are derived from the `alias` values you
defined in `config/config.toml`. Each alias produces **two virtual models**:

| Suffix | Behaviour |
|---|---|
| `<alias>-context` | Sends the query through the RAG pipeline — documents are retrieved and injected as context |
| `<alias>-basic` | Bypasses RAG — the prompt goes directly to the model, faster but without document context |

**Retrieve the actual IDs** exposed by your running AiTao instance:

```bash
curl http://127.0.0.1:8200/v1/models | python3 -m json.tool
```

The response lists every available model ID. Note them down — you will need them
in the next step.

---

## Step 2 — Add the AiTao provider to `models.json`

File: `~/.openclaw/agents/main/agent/models.json`

Open the file and add the `aitao` block inside the top-level `providers` object,
alongside the existing `ollama` entry. **Replace `<alias>` with the actual model
IDs returned by `curl /v1/models` in step 1.**

```json5
{
  "providers": {
    // ... existing providers (ollama, etc.) ...

    "aitao": {
      "baseUrl": "http://127.0.0.1:8200/v1",
      "api": "openai-completions",
      "apiKey": "aitao-local",
      "models": [
        {
          "id": "<alias>-context",
          "name": "<alias> + RAG documents (aitao)",
          "reasoning": false,
          "input": ["text"],
          "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 },
          "contextWindow": 131072,
          "maxTokens": 2048
        },
        {
          "id": "<alias>-basic",
          "name": "<alias> — no RAG (aitao)",
          "reasoning": false,
          "input": ["text"],
          "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 },
          "contextWindow": 131072,
          "maxTokens": 2048
        }
        // Add one pair per alias defined in your config/config.toml
      ]
    }
  }
}
```

> **`contextWindow` and `maxTokens`** depend on the underlying Ollama model.
> Check the model's documentation or use conservative defaults (131072 / 2048).
> These values affect OpenClaw's context truncation logic, not AiTao itself.

---

## Step 3 — Register the auth profile in `auth-profiles.json`

File: `~/.openclaw/agents/main/agent/auth-profiles.json`

AiTao's API authentication is disabled by default, so any non-empty string works
as the key. Add the `aitao:default` profile:

```json
{
  "version": 1,
  "profiles": {
    "ollama:default": {
      "type": "api_key",
      "provider": "ollama",
      "key": "ollama-local"
    },
    "aitao:default": {
      "type": "api_key",
      "provider": "aitao",
      "key": "aitao-local"
    }
  }
}
```

> If you later enable API key auth in AiTao (`config/config.toml` → `[api.auth]`),
> replace `"aitao-local"` with the real key here.

---

## Step 4 — Set AiTao as the default model

```bash
openclaw models set aitao/<alias>-context
```

Replace `<alias>` with one of the IDs you found in step 1. To switch models at any time:

```bash
openclaw models set aitao/<alias>-context   # with RAG — recommended for document queries
openclaw models set aitao/<alias>-basic     # without RAG — faster, no document retrieval
```

---

## Step 5 — Restart the gateway

The gateway process must be restarted to reload the configuration files:

```bash
openclaw gateway restart
```

Verify everything is working:

```bash
openclaw models status        # should list aitao/* models with Auth=yes
openclaw gateway status       # Runtime: running
```

---

## Available models

AiTao's virtual models follow a deterministic naming convention based on the
`alias` field in `config/config.toml`:

| OpenClaw model ID | RAG | Best for |
|---|---|---|
| `aitao/<alias>-context` | ✅ enabled | Questions over your indexed documents |
| `aitao/<alias>-basic` | ❌ disabled | Fast chat without document retrieval |

You will have **one pair per model alias** configured in AiTao. For example, if
you have aliases `assistant` and `coder` in your `config/config.toml`, the IDs
will be `assistant-context`, `assistant-basic`, `coder-context`, `coder-basic`.

Adding or renaming an alias in `config/config.toml` and restarting AiTao
automatically updates the IDs returned by `/v1/models`.

---

## Usage examples

### Example 1 — Ask about your indexed documents

```bash
# First, make sure a *-context model is active
openclaw models set aitao/<alias>-context

# Then ask your question
openclaw agent --agent main -m "Which directories are indexed in our context? List them."
```

Expected: AiTao's RAG engine retrieves the `indexing.include_paths` from its
config, injects the relevant excerpts as system context, and the model answers
based on your actual documents.

---

### Example 2 — Direct question without document retrieval

```bash
# Switch to the *-basic variant to skip RAG (faster)
openclaw models set aitao/<alias>-basic

# Ask anything — no document context will be injected
openclaw agent --agent main -m "Explain the difference between RAG and fine-tuning."
```

The `-basic` models are useful when you want a quick answer that does not depend
on your indexed documents, or when you want to compare responses with and without
RAG context.

---

## Troubleshooting

### `No API key found for provider "aitao"`
The gateway has cached the old config. Run:
```bash
openclaw gateway restart
```

### Empty responses (`"content": ""`)
The default model (`qwen3.5`) uses a **thinking mode** — the first phase of
generation produces internal reasoning tokens with empty `content`. Full output
arrives after the thinking phase. This is normal. Avoid setting `max_tokens`
below ~200 to ensure the thinking phase can complete.

### Verify the AiTao API directly
```bash
# List models
curl http://127.0.0.1:8200/v1/models

# Send a test message (non-streaming)
curl -s -X POST http://127.0.0.1:8200/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen-basic","messages":[{"role":"user","content":"Hello"}],"stream":false}' \
  | python3 -m json.tool
```
