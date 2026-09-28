# AiTao — Troubleshooting

Common issues and how to fix them.

---

## Services & Startup

### "AiTao won't start" or "Port 8200 already in use"

**Symptom:** `./aitao.sh start` fails or API doesn't respond.

**Checklist:**
1. Check what's using port 8200:
   ```bash
   lsof -i :8200          # macOS / Linux
   netstat -ano | grep 8200  # Windows
   ```
2. Either stop the conflicting app or change the port in `config/config.toml`:
   ```toml
   [api]
   port = 8201  # Use a different port
   ```
3. Try again:
   ```bash
   ./aitao.sh start
   ```

---

### "Ollama is not responding" or "Connection refused"

**Symptom:** Chat fails with "LLM backend unavailable" or similar.

**Checklist:**
1. Verify Ollama is running:
   ```bash
   ollama --version
   ```
   - **macOS:** `brew services list | grep ollama` (should show "started")
   - **Windows:** Check system tray or run `ollama serve` manually
   - **Linux:** `systemctl status ollama` or run `ollama serve` manually

2. Check Ollama is accessible at the configured URL:
   ```bash
   curl http://localhost:11434/api/tags
   ```
   Should return a JSON list. If it times out or refuses connection:

3. Verify the `[llm] ollama_url` in `config/config.toml` matches where Ollama is running:
   ```toml
   [llm]
   ollama_url = "http://localhost:11434"  # Default
   ```

4. If Ollama is on a different machine, update the URL:
   ```toml
   ollama_url = "http://192.168.1.100:11434"
   ```

---

### "Using a different LLM backend (llama.cpp, LM Studio, vLLM)"

**To use llama.cpp instead of Ollama:**

1. Start your LLM server (e.g., llama-server):
   ```bash
   llama-server -m model.gguf -ngl 33
   ```

2. Edit `config/config.toml`:
   ```toml
   [llm]
   backend = "openai"  # Switch to OpenAI-compatible mode

   [llm.openai]
   base_url = "http://localhost:8000"  # llama.cpp default
   api_key = "not-needed"
   model = "default"
   ```

3. Restart AiTao:
   ```bash
   ./aitao.sh restart
   ```

Same pattern applies for **LM Studio** (`http://localhost:1234/v1`) or **vLLM** (`http://localhost:8000/v1`).

---

## Indexing & Documents

### "Search returns empty results"

**Symptom:** Search query matches documents you know exist, but returns nothing.

**Checklist:**
1. **Documents may not be indexed yet.** Check queue status:
   ```bash
   ./aitao.sh queue status
   ```
   If queue length > 0, wait for it to drain.

2. **Run a fresh scan** to discover files:
   ```bash
   ./aitao.sh scan run
   ```

3. **Verify documents were indexed:**
   ```bash
   ./aitao.sh stats
   ```
   Should show `total_documents > 0`. If it's 0, no documents were indexed.

4. **Check if documents are in configured folders:**
   - Edit `config/config.toml` and verify `[indexing] include_paths` points to your documents
   - Try indexing a single file manually:
     ```bash
     ./aitao.sh index --file /path/to/document.pdf
     ```

5. **Check for indexing errors:**
   ```bash
   ./aitao.sh queue errors
   ```
   If there are errors (unsupported file type, extraction failed), you'll see them here.

---

### "File type not supported"

**Symptom:** Document appears in queue but fails to index with "extraction not supported" error.

**Supported types:**
- Text (Core, free): `.txt`, `.md`, `.log`, `.rst`, `.tex`
- Documents (Core, free): `.pdf` with a real text layer
- Code (Core, free): `.py`, `.js`, `.java`, etc. (treated as text)
- Office & e-book formats (**Premium**): `.docx`, `.xlsx`, `.pptx`, `.odt`, `.epub`
- Images and scanned PDFs (**Premium**, OCR): `.png`, `.jpg`, `.jpeg`

**If your file type is supported but fails:**
- **Office files (.docx, .xlsx) — Premium:** May fail if the file is corrupted or locked, or if the AiTao Premium module isn't installed/activated. Try opening it in LibreOffice first to verify it's valid.
- **PDFs:** Text extraction fails on some PDFs (very old, watermarked, encrypted). Try converting to PDF text-layer with another tool first. Scanned PDFs (no text layer) require the Premium OCR module.

---

### "Meilisearch is down" or "Search fails with 'connection refused'"

**Symptom:** Dashboard or search shows Meilisearch as "unavailable".

**Checklist:**
1. Verify Meilisearch is running:
   ```bash
   curl http://localhost:7700/health
   ```
   Should return `{"status":"available"}`.

2. If it's not running:
   - **macOS:** `brew services start meilisearch`
   - **Windows:** (included in AiTao portable, but can run manually: `./meilisearch --port 7700`)
   - **Linux:** `meilisearch --port 7700`

3. If the port is in use, change it in `config/config.toml`:
   ```toml
   [search.meilisearch]
   url = "http://localhost:7701"  # Use a different port
   ```

4. Restart AiTao:
   ```bash
   ./aitao.sh stop
   ./aitao.sh start
   ```

---

### "OnlyOffice documents fail to index" (Premium)

**Symptom:** `.docx` files index correctly, but OnlyOffice documents (`.odt`, or `.docx` edited in OnlyOffice) fail with extraction error.

**Known issue:** Some OnlyOffice-generated files have non-standard internal structure that the extractor doesn't handle. See [ONLYOFFICE-INTEGRATION.md](ONLYOFFICE-INTEGRATION.md) for workarounds and testing.

**Workaround:**
1. Save the document from OnlyOffice as `.pdf` instead
2. Or convert with LibreOffice CLI:
   ```bash
   libreoffice --headless --convert-to pdf document.odt
   ```

---

## Search & Retrieval

### "Search is slow"

**Checklist:**
1. **Indexes may be large.** Check size:
   ```bash
   ./aitao.sh stats
   ```
   If `index_size_mb` is very large, it's normal — searches will be slower.

2. **Meilisearch may need reindexing.** Run:
   ```bash
   ./aitao.sh ms rebuild
   ```

3. **Network latency** — if Meilisearch or Ollama are on different machines, latency adds up. Prefer localhost.

---

### "Results are not relevant"

**Symptom:** Search returns documents but they don't match the query.

**This is expected for:**
- Semantic search on very short queries (< 3 words)
- Queries in different language than documents
- Questions about topics not in your documents

**Improve relevance:**
1. Make queries more specific: "Q2 revenue report" instead of "revenue"
2. Use full-text search for exact phrases:
   ```bash
   ./aitao.sh search "exact phrase in quotes"
   ```

---

## Chat & RAG

### "Chat says 'not found in your documents'"

**Symptom:** A factual question returns an explicit refusal instead of an answer.

**Why:** AiTao uses RAG (Retrieval-Augmented Generation). If the search engine finds no relevant chunks, it refuses to answer rather than guess.

**Checklist:**
1. **Documents are indexed** (see "Search returns empty results" above)
2. **Try rephrasing the question** — the retrieval layer is sensitive to query wording
3. **Check if the information is in a supported file type** (see "File type not supported" above)

---

### "Chat takes too long to respond"

**Checklist:**
1. **Ollama inference is slow.** This is normal for larger models (13B+) on slower hardware. Use a smaller model:
   ```bash
   ollama pull mistral:7b
   ```

2. **Retrieval is slow.** See "Search is slow" above.

3. **Network latency.** If Ollama/Meilisearch are remote, RTT adds up. Prefer localhost.

---

## Configuration

### "Can't edit config.toml" or "Syntax error in config"

**Checklist:**
1. **Make a backup:**
   ```bash
   cp config/config.toml config/config.toml.backup
   ```

2. **Validate the syntax:**
   ```bash
   ./aitao.sh config validate
   ```
   Error message will point to the line number.

3. **Start fresh from template:**
   ```bash
   cp config/config.toml.starter config/config.toml
   ./aitao.sh init
   ```

---

### "Environment variable override not working"

**Syntax:** AiTao uses `APP__SECTION__KEY` format (double underscore, uppercase).

Example: to override `[llm] backend`:
```bash
export APP__LLM__BACKEND=openai
./aitao.sh start
```

Verify it took:
```bash
./aitao.sh config show | grep backend
```

---

## MCP Server

### "Claude Desktop can't find AiTao tools"

**Checklist:**
1. **MCP server is not running.** Start it:
   ```bash
   ./aitao.sh mcp serve
   ```

2. **Claude Desktop config is stale.** Get the current config:
   ```bash
   ./aitao.sh mcp config --transport stdio
   ```
   Paste it into your Claude Desktop `claude_desktop_config.json` and reload.

3. **Stdio transport is blocked.** If your OS/IDE doesn't support stdio, use SSE instead:
   ```bash
   ./aitao.sh mcp serve --transport sse --daemon
   ./aitao.sh mcp config --transport sse
   ```

See [MCP_SERVER.md](MCP_SERVER.md) for full setup.

---

## Logs & Debugging

### "I need to see what AiTao is doing"

**View live logs:**
```bash
./aitao.sh logs tail -f
```

**Set log level:**
```bash
export APP__LOG__LEVEL=DEBUG
./aitao.sh start
```

**Common log entries:**
- `[indexer] Processing document.pdf` → file being indexed
- `[search] Query took 145ms` → search latency
- `[llm] Ollama connection failed` → LLM backend issue
- `[ocr] Tesseract not found` → OCR disabled (Tesseract not installed)

---

## Getting Help

- **Check [docs/AITAO-WIKI.md](AITAO-WIKI.md)** for documentation index
- **Review [ARCHITECTURE.md](ARCHITECTURE.md)** to understand how components fit together
- **Post an issue** at https://github.com/Aurica-Circular/AiTao/issues with:
  - `./aitao.sh version`
  - `./aitao.sh status`
  - Relevant log lines (see above)
  - Steps to reproduce

---

**Last updated:** 2026-06-15
