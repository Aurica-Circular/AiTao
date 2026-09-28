# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# mcp/tools/extract.py — MCP tool: aitao_extract (US-055, Premium)
#
# Responsibilities:
#   - Expose aitao_extract tool to MCP clients
#   - Gate behind Premium license check
#   - Extract named entities, key phrases, or summaries from text

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from aitao.core.logger import get_logger
from aitao.mcp_server.tools._premium import require_premium

logger = get_logger("mcp.tools.extract")

_EXTRACT_TYPES = {
    "summary": "Generate a concise summary of the text.",
    "entities": "Extract named entities (persons, organisations, dates, locations).",
    "keywords": "Extract key terms and phrases.",
    "topics": "Identify main topics discussed.",
}


def register_extract(mcp: FastMCP) -> None:
    """Register aitao_extract (Premium) tool on the given FastMCP instance."""

    @mcp.tool()
    async def aitao_extract(
        text: str,
        extract_type: str = "summary",
        lang: str = "auto",
    ) -> dict[str, Any]:
        """Extract structured information from text using AiTao's NLP pipeline.
        Requires a Premium license.

        Args:
            text:         Source text to process (max 20 000 characters).
            extract_type: Type of extraction — "summary", "entities",
                          "keywords", or "topics".
            lang:         Language of the source text (ISO code or "auto").

        Returns:
            Dictionary with 'result', 'extract_type', 'input_chars'.
        """
        require_premium("extraction")

        if not text.strip():
            raise ValueError("text must not be empty")

        if len(text) > 20_000:
            raise ValueError("text exceeds 20 000 character limit")

        if extract_type not in _EXTRACT_TYPES:
            raise ValueError(
                f"Unknown extract_type '{extract_type}'. "
                f"Valid: {', '.join(_EXTRACT_TYPES)}",
            )

        try:
            from processing.extractor import TextExtractor  # type: ignore
            extractor = TextExtractor()
            result = await extractor.extract(
                text=text,
                extract_type=extract_type,
                lang=None if lang == "auto" else lang,
            )
        except Exception as e:
            logger.error("Extraction failed", metadata={"type": extract_type, "error": str(e)})
            raise RuntimeError(f"Extraction error: {e}") from e

        payload = result if isinstance(result, (str, list, dict)) else str(result)

        logger.info("aitao_extract success", metadata={"type": extract_type, "chars": len(text)})
        return {
            "result": payload,
            "extract_type": extract_type,
            "input_chars": len(text),
        }
