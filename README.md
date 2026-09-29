# AiTao — Your Private, Local AI Assistant

> *"Your data stays yours. No cloud. No compromise."*

AiTao is a **free, open-source AI assistant that runs entirely on your computer**. It reads your files, remembers their content, and answers questions about them — without sending anything to the internet.

---

## Table of Contents — Your setup roadmap

| Step | What you'll do |
|------|----------------|
| [1. Before you start](#1-before-you-start) | Check software requirements |
| [2. Install prerequisites](#2-install-prerequisites) | LLM engine + Meilisearch |
| [3. Install AiTao](#3-install-aitao) | The installer does the heavy lifting |
| [4. Download an AI model](#4-download-an-ai-model) | The brain of your assistant |
| [5. Configure AiTao](#5-configure-aitao) | 3 fields — 5 minutes |
| [6. Start AiTao](#6-start-aitao-and-index-your-files) | Your files get read once |
| [7. Chat with your documents](#7-chat-with-your-documents) | CLI or a graphical interface |
| [8. Activate Premium (optional)](#8-activate-premium-optional) | Unlock advanced features |

---

## 1. Before you start

*"What do I need to have installed before running AiTao?"*

| Requirement | Minimum | Notes |
|-------------|---------|-------|
| **OS** | macOS 13+ / Windows 11 | Intel or Apple Silicon · Windows is **64-bit only** (x64 or ARM64 — no 32-bit) |
| **RAM** | 8 GB | 16 GB recommended for better models |
| **Disk** | 20 GB free | For AI models + your document index |

---

## 2. Install prerequisites

*"There are two tools AiTao needs that aren't bundled with it."*

### Ollama — the default AI engine

Ollama runs AI models on your machine; AiTao talks to it to answer your questions. It's the simplest engine to set up and the default — but **AiTao is engine-agnostic** and also works with **llama.cpp** (any OpenAI-compatible server). Ollama is recommended for first-time setup.

**macOS:**
```bash
brew install ollama
```

**Windows:**  
Download and run the installer from https://ollama.com/download

Verify it works:
```bash
ollama --version
```

---

### Meilisearch — the search engine

Meilisearch is a fast, local full-text search engine. It's what makes keyword searches instant.

**macOS:**
```bash
brew install meilisearch
```

**Windows:**  
Included automatically in the AiTao Windows portable setup — no manual step required.

---

### Tesseract & Poppler — the OCR engine (optional, Premium OCR only)

*Only needed if you want **Advanced OCR** (Premium) to read **scanned PDFs and images**. Skip this entirely if you only index text documents (Word, PDF with real text, Markdown…).*

AiTao's cross-platform OCR relies on two free tools: **Tesseract** (recognises the text) and **Poppler** (splits scanned PDFs into page images). On macOS, AiTao uses the built-in **Apple Vision** engine by default and needs neither — so this step mainly concerns **Windows**.

**macOS:**
```bash
brew install tesseract poppler
```
> Apple Silicon and Intel Macs use Apple Vision automatically; Tesseract is only a fallback. Poppler is still useful for scanned PDFs.

**Windows (64-bit only):**

1. **Tesseract** — download the **64-bit** installer from the UB Mannheim build:
   <https://github.com/UB-Mannheim/tesseract/wiki>
   During setup, open *Additional language data* and tick **French**, **English**, and **Chinese (Traditional)**.
   Let the installer tick *Add Tesseract to PATH* (or add `C:\Program Files\Tesseract-OCR` to your PATH manually).
2. **Poppler** — required for **scanned PDFs** (plain images don't need it).
   Download the latest release from <https://github.com/oschwartz10612/poppler-windows/releases>, unzip it, and add its `Library\bin` folder to your PATH.
3. **Restart your terminal**, then verify:
   ```bash
   tesseract --version
   tesseract --list-langs     # must list: fra  eng  chi_tra
   pdftoppm -h                # confirms Poppler is on your PATH
   ```

> **Prefer not to install Tesseract on Windows?** You can use **qwen_vl** instead — a vision AI model that runs through **Ollama** (already installed above), so there's no extra text-recognition binary to add. It handles tables especially well but is much slower (~40 s per page). Scanned PDFs still need Poppler; plain images need nothing extra. Enable it in your config:
> ```toml
> [ocr]
> engine_order = ["macos_vision", "tesseract", "qwen_vl"]
> ```

---

## 3. Install AiTao

*"I have the prerequisites. Now I install AiTao itself."*

### macOS

```bash
# Download AiTao
git clone https://github.com/Aurica-Circular/AiTao.git
cd aitao

# Run the installer (sets up the Python environment automatically)
./install.sh
```

### Windows

Unzip the downloaded archive, then double-click `setup.bat`. The installer handles everything.

---

### First-time setup wizard (recommended)

*"I just installed AiTao. The quickest way to configure it is the interactive wizard."*

```bash
./aitao.sh init
```

The wizard asks you 3 questions (folders to index, AI model to use, your name) and writes a minimal `config/config.toml` for you. Skip to [Step 6](#6-start-aitao-and-index-your-files) when done.

Want to configure manually instead? Keep reading Step 4 and 5.

---

## 4. Download an AI model

*"AiTao needs an AI model — it's like the brain of the assistant. I download it once and it stays on my computer."*

### Step 4a — Pull a model

```bash
# This downloads the model from the internet — do this once
ollama pull llama3.1:8b
```

**How to choose a model:**  
Browse https://ollama.com/models — filter by tags (general, code, vision…).  
> **RAM guideline:** 8 GB RAM → 7b models work well. 16 GB+ → 13b+ models give better answers.

---

### Step 4b — Find the exact model name for your config

```bash
# List models you have installed
ollama list
```

You'll see output like:

```
NAME                    ID              SIZE
llama3.1:8b             ...             4.7 GB
qwen2.5-coder:7b        ...             4.4 GB
```

**The `NAME` column is exactly what to put in your config file in Step 5.**

> **Alternative LLM engine:** AiTao also works with **llama.cpp** (`llama-server`) — any server that exposes an OpenAI-compatible API. Set `[llm] backend = "openai"` and `[llm.openai] base_url = "http://..."` in your config. Ollama remains the default and the easiest option.

---

## 5. Configure AiTao

*"I need to tell AiTao three things: who I am, where my files are, and which model to use."*

Open `config/config.toml.starter` (or edit `config/config.toml` directly).  
You only need to fill in **3 sections**:

```toml
# --- WHO ARE YOU? -------------------------------------------------------------
[identity]
who_are_you = "Marie, French graphic designer, 38 years old, Barcelona"
```

> **What this does:** AiTao injects this description into every conversation. It adapts its tone, language, and suggestions to you — like briefing a new assistant on day one.  
> This is about *you*, not about your documents.

```toml
# --- WHICH LANGUAGE SHOULD AiTao REPLY IN? (optional) -------------------------
[identity]
response_language = "English"   # e.g. "English", "français", "中文", "Deutsch"…
```

> **What this does:** AiTao always answers in this language, even when your
> documents are written in another one (e.g. an English contract summarised in
> French). Write the value in your own language.  
> **Leave it empty** (`response_language = ""`) to let AiTao reply in whatever
> language you wrote your question in.  
> You can always override it for a single message — just ask, e.g. *"Answer me
> in English this time."*

```toml
# --- WHICH FOLDERS TO INDEX? --------------------------------------------------
[indexing]
include_paths = [
    "/Users/marie/Documents",
    "/Users/marie/Desktop",
]
```

> **What this does (RAG):** AiTao reads these files once and permanently remembers their content. When you ask a question, it searches through what it has read before answering — so it talks about *your* documents, not generic internet knowledge.  
> `who_are_you` tells AiTao about *you*. `include_paths` tells AiTao about *your documents*. Both enrich answers, but differently.

```toml
# --- WHICH AI MODEL TO USE? ---------------------------------------------------
[llm]
default_model = "llama3.1:8b"   # Use the exact NAME shown by 'ollama list'
```

**That's it.** Save the file and move on.

> **Advanced options:** See `config/config.toml.template` for the full annotated config reference.

---

## 6. Start AiTao and index your files

*"Everything is configured. Now I start AiTao."*

```bash
./aitao.sh start
```

This starts the core services: Meilisearch, the background worker, and the AiTao API.
The MCP server is **not** started automatically — it is optional (see [step 9](#9-use-aitao-with-ai-assistants-mcp)).  
The first time, **indexing takes a while** — AiTao is reading all your files.

Check progress:

```bash
./aitao.sh dashboard
```

The dashboard shows how many documents have been indexed and whether there are any errors.  
Wait until the queue is empty before expecting full search results.

**Stop AiTao:**
```bash
./aitao.sh stop
```

---

## 7. Chat with your documents

*"AiTao is running. How do I actually talk to it?"*

### Option A — Terminal chat

```bash
./aitao.sh chat
```

Type your question and press Enter. AiTao searches your indexed documents and answers with context from them.

### Option B — Graphical interface (recommended for beginners)

AiTao exposes a standard API that most AI chat interfaces support.

**Open WebUI** (ChatGPT-like interface in your browser):
1. Connect Open WebUI to `http://localhost:8200/v1`
2. Chat normally — AiTao handles the document enrichment behind the scenes

**OnlyOffice AI** (if you use OnlyOffice for documents):
- Plugin: AI Agent sidebar (DD777)
- Provider type: **OpenAI Compatible** (not "OpenAI")
- URL: `http://127.0.0.1:8200/v1`
- API Key: `sk-local`

**Continue.dev** (VS Code extension for coding):
```json
{
  "models": [{
    "title": "AiTao",
    "provider": "openai",
    "model": "llama3.1:8b",
    "apiBase": "http://localhost:8200/v1"
  }]
}
```

See [docs/AITAO-WIKI.md](docs/AITAO-WIKI.md) for the full documentation index.

---

## 8. Activate Premium (optional)

*"What extra features do I get, and how do I unlock them?"*

Premium widens the *range of documents* AiTao can read. Searching and chatting over
your indexed documents is free, and stays free.

| Feature | Core (Free) | Premium |
|---------|-------------|---------|
| Index files + full-text & semantic search | ✓ | ✓ |
| Chat over your indexed documents (answers grounded in your files) | ✓ | ✓ |
| Works with Ollama or llama.cpp (any OpenAI-compatible engine) | ✓ | ✓ |
| Dashboard & CLI | ✓ | ✓ |
| Text formats: PDF with real text, `.txt`, `.md`, `.log`, `.json`, source code | ✓ | ✓ |
| Answer reliability check (fast by default, deep on request) | ✓ | ✓ |
| Office & e-book formats: `.docx`, `.xlsx`, `.pptx`, `.odt`, `.ods`, `.odp`, `.epub` | — | ✓ |
| Advanced OCR (scanned PDFs, images, tables, handwriting) | — | ✓ |
| Priority support | — | ✓ |

> Not sure? Start with Core — it covers 90% of personal use cases. Upgrade when your
> documents are Office files, scans or images.

**Activate your license key:**
```bash
./aitao.sh license activate YOUR-LICENSE-KEY
```

Pricing: https://auricacircular.com/en/products/aitao/ — request a licence key via the
contact form: https://auricacircular.com/en/contact/ (or support@auricacircular.com).

### Network access

AiTao Core never downloads anything on its own. The Premium module makes one
outbound request of its own: at most once every 24 hours, it checks a small
public, signed list of cancelled licence keys, so a leaked key can be
deactivated even though verification itself is fully offline. That check
sends nothing about you, your machine, or your documents — no identifying
header, no query string — and if the request fails (no network, the list is
unreachable), AiTao silently keeps working exactly as before. Offline use is
fully supported: Core and an already-activated Premium licence both keep
working with no network at all.

---

## 9. Use AiTao with AI assistants (MCP)

AiTao includes an MCP (*Model Context Protocol*) server that lets Claude Desktop, VS Code GitHub Copilot,
Cursor, Windsurf, and other compatible AI assistants call AiTao tools directly.

**Available tools:**

| Tool | Description | Tier |
|------|-------------|------|
| `aitao_search` | Search indexed documents | Free |
| `aitao_ingest` | Add a file to the knowledge base | Free |
| `aitao_stats` | Index statistics | Free |
| `aitao_ocr` | Run OCR on an image or scanned PDF | Premium |
| `aitao_extract` | Extract structured data from a document | Premium |

**Quick setup:**

```bash
# 1. Get the config snippet for your AI client
./aitao.sh mcp config --transport stdio

# 2. Paste the snippet into your client config, then reload the client

# 3. (Optional) Run as a daemon instead:
./aitao.sh mcp serve --transport sse --daemon
./aitao.sh mcp config --transport sse

# To see all available mcp sub-commands and options:
./aitao.sh mcp --help
```

See [docs/MCP_SERVER.md](docs/MCP_SERVER.md) for the full reference (all transports, client configs, CLI, security).

---

## Quick command reference

```bash
# Services
./aitao.sh start          # Start core services (Meilisearch, Worker, API)
./aitao.sh stop           # Stop core services
./aitao.sh restart        # Restart core services
./aitao.sh status         # Quick health check
./aitao.sh dashboard      # Full snapshot (services, models, index, errors)

# Documents
./aitao.sh scan run       # Scan folders for new files
./aitao.sh queue status   # Indexing queue progress

# Chat & Search
./aitao.sh chat           # Interactive chat
./aitao.sh search "your query"

# Models
./aitao.sh models list    # List configured models + Ollama presence
./aitao.sh models check   # Verify all declared models are available

# MCP Server (AI assistants integration)
./aitao.sh mcp serve      # Start MCP server (stdio, foreground)
./aitao.sh mcp serve --transport sse --daemon  # Start SSE daemon
./aitao.sh mcp stop       # Stop SSE/HTTP daemon
./aitao.sh mcp status     # Show daemon PID + HTTP probe
./aitao.sh mcp config     # Print config snippet for Claude Desktop / VS Code

# Setup
./aitao.sh init           # First-time configuration wizard
```

---

## Troubleshooting

**"Ollama is not running"**
```bash
ollama serve              # macOS / Linux
# Windows: start the Ollama app from the system tray
```

**"No models available"**
```bash
ollama pull llama3.1:8b   # Download a model first
```

**"Search returns no results"**
```bash
./aitao.sh scan run       # Re-scan your configured folders
./aitao.sh queue status   # Wait until queue is empty
```

**"Port 8200 already in use"** — Change `api.port` in `config/config.toml`.

For more help: [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) · [docs/AITAO-WIKI.md](docs/AITAO-WIKI.md) · [GitHub Issues](https://github.com/Aurica-Circular/AiTao/issues)

---

## Releases & architecture

Full release history: [CHANGELOG.md](CHANGELOG.md)

Technical architecture (layers, storage, plugin system, event bus): [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

---

## License

**AiTao Core** is free software under the **GNU Affero General Public License v3 (AGPL-3.0)**.

- ✓ Free to use, study, modify and redistribute — personal or commercial
- ✓ No data leaves your machine — ever
- ✗ Redistributing a modified version means releasing your source under the same license
- ✗ You cannot bundle AiTao in a closed-source product without a commercial license

**AiTao Premium Modules** are a separate, commercially licensed product, distributed only
to customers who hold a valid license key. They are activated by that key.

Full license text: [LICENSE](LICENSE) — what is free, what is not, and the additional
permission that makes the two legally combinable: [LICENSING.md](LICENSING.md) —
commercial inquiries: support@auricacircular.com

---

🧋 Enjoying AiTao? Buy the author a bubble tea: https://buymeacoffee.com/shamantao — a personal
tip to the author, not a purchase, and unrelated to AiTao Premium.

---

<p align="center"><strong>AiTao</strong> — Your Personal AI, Your Privacy, Your Planet 🌍</p>
