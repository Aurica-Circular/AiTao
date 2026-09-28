# AiTao Documentation

Welcome to AiTao documentation. Choose your path:

---

## 🚀 Getting Started

Start here if you're new to AiTao.

- **[QUICKSTART.md](QUICKSTART.md)** — Run AiTao in 5 minutes (install → config → index → chat)
- **[INSTALLATION.md](INSTALLATION.md)** — Detailed setup guide (Windows portable, macOS, Linux)

---

## 📚 Core Documentation

Understand how AiTao works and how to use it.

- **[ARCHITECTURE.md](ARCHITECTURE.md)** — System design, layers, components, and design principles
- **[API_REFERENCE.md](API_REFERENCE.md)** — All endpoints (native `/api/*` and OpenAI-compatible `/v1/*`) with examples
- **[COMMANDS.md](COMMANDS.md)** — Complete CLI command reference (auto-generated from `./aitao.sh docs`)

---

## 🔧 Integration & Extensions

Use AiTao as a service or integrate with other tools.

- **[MCP_SERVER.md](MCP_SERVER.md)** — Model Context Protocol integration (Claude, Cursor, VS Code)
- **[CONTINUE-INTEGRATION.md](CONTINUE-INTEGRATION.md)** — Use AiTao with Continue IDE extension
- **[ONLYOFFICE-INTEGRATION.md](ONLYOFFICE-INTEGRATION.md)** — Index LibreOffice / OnlyOffice documents
- **[OPENCLAW-INTEGRATION.md](OPENCLAW-INTEGRATION.md)** — Integration with OpenClaw framework

---

## 📋 Quick Reference

| Document | For | Contains |
|----------|-----|----------|
| **QUICKSTART.md** | Everyone | 5-minute setup walkthrough |
| **INSTALLATION.md** | New users | Portable Windows setup, troubleshooting |
| **ARCHITECTURE.md** | Contributors, architects | System design, layer breakdown, testing |
| **API_REFERENCE.md** | API users, integrators | Endpoint reference, curl examples, SDKs |
| **COMMANDS.md** | CLI users | All `./aitao.sh` commands |
| **MCP_SERVER.md** | Claude/IDE users | Setup and tool reference |

---

## ❓ FAQ

**Where do I start?**  
→ [QUICKSTART.md](QUICKSTART.md)

**How do I integrate AiTao into my app?**  
→ [API_REFERENCE.md](API_REFERENCE.md)

**I want to use it with Claude Desktop or Cursor.**  
→ [MCP_SERVER.md](MCP_SERVER.md)

**How does the indexing pipeline work?**  
→ [ARCHITECTURE.md](ARCHITECTURE.md)

**A specific command's options?**  
→ [COMMANDS.md](COMMANDS.md) or run `./aitao.sh <command> --help`

---

## 🏗️ Contributing

See [ARCHITECTURE.md](ARCHITECTURE.md) for:
- Code conventions (comments in English, code typed, files under 350 LOC)
- Testing setup (pytest, fixtures, integration tests)
- Package management (uv, no bare pip)
- File structure and design principles

---

**Last updated:** 2026-06-15  
**AiTao version:** See `./aitao.sh version`
