# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# mcp/__init__.py — AiTao MCP Server package (US-055)
#
# Exposes AiTao capabilities to MCP clients (Claude Desktop, VS Code Copilot,
# Windsurf, Cursor, OnlyOffice, askimo) via three transports:
#   - stdio      : subprocess-based (Claude Desktop, Copilot, Windsurf, Cursor)
#   - SSE        : HTTP Server-Sent Events (port 8201)
#   - Streamable HTTP : MCP 1.0 standard transport

from aitao.mcp_server.server import create_mcp_server

__all__ = ["create_mcp_server"]
