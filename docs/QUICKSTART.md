# AiTao — Quickstart (5 minutes)

Get AiTao running and chat with your documents in 5 minutes.

---

## 1. Install

### Windows (Portable)

Download the latest release from [Releases](https://github.com/Aurica-Circular/AiTao/releases):

```powershell
.\setup-portable.ps1
```

See [INSTALLATION.md](INSTALLATION.md) for details.

### macOS / Linux (from source)

```bash
git clone https://github.com/Aurica-Circular/AiTao.git
cd aitao
uv pip install -e ".[dev]"
./aitao.sh init
```

---

## 2. Configure

Edit `config/config.toml` and set the folders you want to index:

```toml
[indexing]
include_paths = [
  "/Users/yourname/Documents",
  "/Users/yourname/Desktop",
]
```

Save. That's it.

---

## 3. Index your documents

Start the background worker and scanner:

```bash
./aitao.sh start
```

This launches:
- **Scanner** — walks your folders and discovers new/changed files
- **Worker** — processes the queue, extracts text, and indexes documents
- **API** — listens on `http://localhost:8200`
- **Meilisearch** — full-text search backend
- **Ollama** — LLM provider (if not already running)

Check progress:

```bash
./aitao.sh status
./aitao.sh queue list
```

---

## 4. Chat

Open http://localhost:8200 in your browser and start asking questions about your documents.

Example:
- *"Summarize my meeting notes from June"*
- *"What's the license key format?"*
- *"Extract all email addresses from contracts"*

AiTao searches your local documents and grounds the answer in what it finds. If there's no relevant match, it says so explicitly (no hallucinations).

---

## 5. Stop

```bash
./aitao.sh stop
```

---

## Next Steps

- **API Integration** → See [API_REFERENCE.md](API_REFERENCE.md) for native + OpenAI-compatible endpoints
- **Architecture** → See [ARCHITECTURE.md](ARCHITECTURE.md) for how the system works
- **Commands** → See [COMMANDS.md](COMMANDS.md) for all CLI commands
- **Troubleshooting** → See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) if something breaks

---

## What's Inside

| Component | Purpose |
|-----------|---------|
| **Scanner** | Discovers files in your configured folders |
| **Indexer** | Extracts text, chunks it, and stores in LanceDB + Meilisearch |
| **Hybrid Search** | Full-text (Meilisearch) + semantic (LanceDB) in parallel |
| **RAG Engine** | Retrieves relevant chunks and enriches the LLM prompt |
| **Chat API** | Exposes native AiTao + OpenAI-compatible endpoints |

---

## FAQ

**Q: Does my data leave my computer?**  
A: No. Everything runs locally on your machine. No cloud. No API calls (unless you configure an external LLM).

**Q: Which file types are supported?**  
A: Free (Core): `.txt`, `.md`, `.pdf` with real text, source code, and more. Premium: Office
& e-book formats (`.docx`, `.xlsx`, `.pptx`, `.odt`, `.epub`…) and OCR for scanned PDFs and
images (`.png`, `.jpg`). See [ARCHITECTURE.md](ARCHITECTURE.md#2-ingestion-pipeline-srcindexation).

**Q: Can I use OpenAI / Claude instead of Ollama?**  
A: Yes. Edit `config/config.toml`:
```toml
[llm]
backend = "openai"
[llm.openai]
api_key = "sk-..."
model = "gpt-4o-mini"
```

**Q: How do I add more documents?**  
A: Just drop them in the folders configured in `include_paths`. The scanner picks them up automatically. Or use:
```bash
./aitao.sh index --file /path/to/document.pdf
```

**Q: What about OCR for scanned PDFs?**  
A: OCR (Tesseract, Apple Vision on macOS, and Qwen-VL) is provided by the AiTao Premium
module — install it and activate a licence to read scanned PDFs and images. See
[ARCHITECTURE.md](ARCHITECTURE.md#2-ingestion-pipeline-srcindexation).
