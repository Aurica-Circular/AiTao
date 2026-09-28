# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# mcp/tools/ocr.py — MCP tool: aitao_ocr (US-055, Premium)
#
# Responsibilities:
#   - Expose aitao_ocr tool to MCP clients
#   - Gate behind Premium license check
#   - Wrap AiTao's OCRRouter (tesseract / LLM vision fallback)

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from aitao.core.logger import get_logger
from aitao.mcp_server.tools._premium import require_premium

logger = get_logger("mcp.tools.ocr")

_OCR_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp", ".gif", ".webp"}


def register_ocr(mcp: FastMCP) -> None:
    """Register aitao_ocr (Premium) tool on the given FastMCP instance."""

    @mcp.tool()
    async def aitao_ocr(
        file_path: str,
        lang: str = "auto",
        output_format: str = "text",
    ) -> dict[str, Any]:
        """Extract text from an image or scanned PDF using OCR.
        Requires a Premium license.

        Args:
            file_path:     Absolute path to image or PDF file.
            lang:          OCR language hint — ISO code (e.g. "fr", "en")
                           or "auto" for automatic detection.
            output_format: "text" (default) or "markdown".

        Returns:
            Dictionary with 'text', 'page_count', 'lang_detected'.
        """
        require_premium("ocr")

        resolved = Path(file_path).expanduser().resolve()

        if not resolved.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        if resolved.suffix.lower() not in _OCR_EXTENSIONS:
            raise ValueError(
                f"Unsupported file type '{resolved.suffix}'. Supported: {', '.join(sorted(_OCR_EXTENSIONS))}",
            )

        valid_formats = {"text", "markdown"}
        if output_format not in valid_formats:
            output_format = "text"

        try:
            from aitao.ocr.router import OCRRouter
            router = OCRRouter()
            ocr_result = await router.extract(
                file_path=resolved,
                lang=None if lang == "auto" else lang,
            )
        except Exception as e:
            logger.error("OCR failed", metadata={"path": str(resolved), "error": str(e)})
            raise RuntimeError(f"OCR error: {e}") from e

        logger.info("aitao_ocr success", metadata={"path": str(resolved)})
        return {
            "text": ocr_result.text,
            "page_count": ocr_result.page_count,
            "lang_detected": ocr_result.lang_detected or lang,
        }
