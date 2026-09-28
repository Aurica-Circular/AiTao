# AiTao MCP Server — Setup Guide

> Connect your AI assistant (Claude Desktop, VS Code Copilot, Cursor, Windsurf…)
> so it can search your documents, ingest files, and more — without copy-pasting anything.

---

## Before you start

AiTao must be running:

```bash
./aitao.sh start
./aitao.sh status   # Check everything is OK
```

That's the only prerequisite. You don't need to start anything extra for MCP.

---

## The one thing to understand about MCP

There are two ways the AI client can talk to AiTao:

**`stdio` — the simple way (recommended)**

The AI client launches AiTao itself, talks to it, then closes it — automatically, every time you ask a
question. You never see it, you never start it manually. It just works.
→ No daemon. No port. Nothing to check.

**`SSE` — the server way (advanced, optional)**

A persistent server runs in the background on port `8201`. The AI client connects to it like a website.
→ Use this only if you have multiple AI clients, or if stdio doesn't work with your client.

**In short: start with `stdio`. Switch to `SSE` only if you have a specific reason to.**

---

## Table of Contents

1. [Get the config snippet](#1-get-the-config-snippet)
2. [Configure your AI client](#2-configure-your-ai-client)
   - [Claude Desktop](#claude-desktop)
   - [VS Code GitHub Copilot](#vs-code-github-copilot)
   - [Cursor](#cursor)
   - [Windsurf](#windsurf)
3. [Test that it works](#3-test-that-it-works)
4. [Available tools](#4-available-tools)
5. [SSE mode (advanced)](#5-sse-mode-advanced)
6. [Troubleshooting](#6-troubleshooting)

---

## 1. Get the config snippet

This command reads your installation and generates the exact JSON to paste into your AI client:

```bash
./aitao.sh mcp config --transport stdio
```

You'll get something like:

```json
{
  "mcpServers": {
    "aitao": {
      "command": "<$home>/aitao/aitao.sh",
      "args": ["mcp", "serve", "--transport", "stdio"]
    }
  }
}
```

Keep this output open — you'll paste it in the next step.

---

## 2. Configure your AI client

### Claude Desktop

**macOS:** Open `~/Library/Application Support/Claude/claude_desktop_config.json`  
**Windows:** Open `%APPDATA%\Claude\claude_desktop_config.json`

If the file doesn't exist yet, create it. Paste the snippet from step 1 inside it:

```json
{
  "mcpServers": {
    "aitao": {
      "command": "<$home>/aitao/aitao.sh",
      "args": ["mcp", "serve", "--transport", "stdio"]
    }
  }
}
```

Quit Claude Desktop completely (menu bar icon → Quit), then reopen it.
AiTao will appear in the tools list (hammer icon 🔨 at the bottom of the chat).

---

### VS Code GitHub Copilot

Open VS Code, press `Cmd+Shift+P` (macOS) or `Ctrl+Shift+P` (Windows), type:

```
MCP: Add Server
```

Choose **Command (stdio)**, then fill in:
- Command: the `command` value from your snippet (e.g. `/Users/you/aitao/aitao.sh`)
- Arguments: `mcp serve --transport stdio` (each word as a separate arg)

Or paste the snippet directly into `.vscode/mcp.json` at the root of your workspace:

```json
{
  "servers": {
    "aitao": {
      "type": "stdio",
      "command": "<$home>/aitao/aitao.sh",
      "args": ["mcp", "serve", "--transport", "stdio"]
    }
  }
}
```

Reload VS Code. A new chat mode **"Agent"** will appear — select it and AiTao tools are available.

---

### Cursor

Open `~/.cursor/mcp.json` (create it if it doesn't exist). Paste the snippet from step 1:

```json
{
  "mcpServers": {
    "aitao": {
      "command": "<$home>/aitao/aitao.sh",
      "args": ["mcp", "serve", "--transport", "stdio"]
    }
  }
}
```

Restart Cursor. AiTao tools appear automatically in the Agent panel.

---

### Windsurf

Open `~/.codeium/windsurf/mcp_config.json` (create it if it doesn't exist). Paste:

```json
{
  "mcpServers": {
    "aitao": {
      "command": "<$home>/aitao/aitao.sh",
      "args": ["mcp", "serve", "--transport", "stdio"]
    }
  }
}
```

Restart Windsurf (Cascade panel → refresh).

---

## 3. Test that it works

In your AI client, type:

> *"Use AiTao to search for [something in your documents]"*

The client will call `aitao_search` and show you results from your indexed files.
If it works, you're done.

---

## 4. Available tools

| Tool | What it does | Tier |
|------|-------------|------|
| `aitao_search` | Search your indexed documents by natural language | Free |
| `aitao_ingest` | Add a file to the knowledge base | Free |
| `aitao_stats` | Show how many documents are indexed | Free |
| `aitao_ocr` | Extract text from a scanned PDF or image | Premium |
| `aitao_extract` | Extract structured data from a document | Premium |

You don't need to call these tools manually — the AI client calls them automatically when relevant.

---

## 5. SSE mode (advanced)

Use this mode only if:
- You want multiple AI clients to share the same server simultaneously
- Your client doesn't support `stdio` (rare)

```bash
# Start a persistent server in the background
./aitao.sh mcp serve --transport sse --daemon

# Check it's running
./aitao.sh mcp status

# Get the config snippet for SSE
./aitao.sh mcp config --transport sse
```

The SSE server runs on port `8201`. The config snippet will look like:

```json
{
  "mcpServers": {
    "aitao": {
      "url": "http://127.0.0.1:8201/sse"
    }
  }
}
```

To stop it: `./aitao.sh mcp stop`

---

## 6. Troubleshooting

**"No tools appear in my AI client"**  
→ Check the paths in your config file are correct (no typo, file exists).  
→ Make sure you restarted the client after editing the config.  
→ Run `./aitao.sh status` to confirm AiTao itself is running.

**"Tool returns: Search engine not available"**  
→ AiTao was not started. Run `./aitao.sh start` first.

**"Tool returns: Premium feature required"**  
→ Activate your license: `./aitao.sh license activate YOUR-KEY`

**"I ran `./aitao.sh mcp serve` and it just hung"**  
→ That's normal for `stdio` mode — it waits for the AI client to talk to it.  
→ Don't run it manually. Let the AI client launch it (that's the whole point of stdio).

**SSE: client can't connect to `localhost:8201`**  
→ Verify the daemon is running: `./aitao.sh mcp status`  
→ If not: `./aitao.sh mcp serve --transport sse --daemon`

---

*Last updated: AiTao v2.9.0*
