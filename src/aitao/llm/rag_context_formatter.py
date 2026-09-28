# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
RAG context formatting for AiTao.

Handles rendering of retrieved documents and chunks into formatted
text sections suitable for inclusion in LLM prompts. Provides:
- format_context_document / format_context_chunk: single-item formatting
- build_context_section / build_chunks_context_section: full section builders
- Token estimation and truncation utilities
"""

from typing import List, Optional, Set

from aitao.llm.rag_models import ContextChunk, ContextDocument


# Approximate chars per token (for estimation)
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str, chars_per_token: int = CHARS_PER_TOKEN) -> int:
    """Estimate token count from text length."""
    return len(text) // chars_per_token


def truncate_to_tokens(
    text: str,
    max_tokens: int,
    chars_per_token: int = CHARS_PER_TOKEN,
) -> str:
    """Truncate text to approximately max_tokens."""
    max_chars = max_tokens * chars_per_token
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


def _trash_notice(path: str) -> str:
    """US-28a — flag for a source file that no longer exists on disk."""
    try:
        from aitao.indexation.trash import get_trash_registry

        if path and get_trash_registry().is_trashed(path):
            return (
                "    ⚠️ DELETED FILE: this source no longer exists on disk "
                "(in trash, pending purge). If you use it, tell the user the "
                "file has been deleted."
            )
    except Exception:
        pass
    return ""


def format_context_document(
    doc: ContextDocument,
    index: int,
    include_metadata: bool = True,
    max_content_chars: Optional[int] = 500,
) -> str:
    """
    Format a single context document for inclusion in prompt.

    Args:
        doc: The context document
        index: 1-based index for reference
        include_metadata: Whether to include path and category

    Returns:
        Formatted string representation

    Note (C-02, US-103): the retrieval score is deliberately NOT rendered.
    RRF scores are rank-based (top result ~1.0 regardless of relevance,
    I-12), so a "Relevance: N%" line manufactured unfounded confidence — the
    model echoed it and users read it as a relevance measure. Structural
    labels (title, path, category) stay.
    """
    lines = [f"[{index}] {doc.title}"]
    notice = _trash_notice(doc.path or "")
    if notice:
        lines.append(notice)

    if include_metadata:
        if doc.path:
            lines.append(f"    Path: {doc.path}")
        if doc.category:
            lines.append(f"    Category: {doc.category}")

    # Add content excerpt. ``max_content_chars=None`` includes the full content
    # (used for a document the user named explicitly — "translate this file" —
    # so the model sees every page, not just the first 500 chars). The overall
    # token budget in build_context_section still bounds the total.
    if doc.content:
        content_preview = (
            doc.content if max_content_chars is None else doc.content[:max_content_chars]
        )
        lines.append(f"    Content: {content_preview}")

    return "\n".join(lines)


def format_context_chunk(
    chunk: ContextChunk,
    index: int,
    include_metadata: bool = True,
) -> str:
    """
    Format a single context chunk for inclusion in prompt.

    Args:
        chunk: The context chunk
        index: 1-based index for reference
        include_metadata: Whether to include the source path

    Returns:
        Formatted string representation

    Note (C-02, US-103): the retrieval score is deliberately NOT rendered —
    see format_context_document.
    """
    # Show chunk position within document
    position = f"[Part {chunk.chunk_index + 1}/{chunk.total_chunks}]"
    lines = [f"[{index}] {chunk.title} {position}"]
    notice = _trash_notice(chunk.path or "")
    if notice:
        lines.append(notice)

    if include_metadata:
        if chunk.path:
            lines.append(f"    Source: {chunk.path}")

    # Add full chunk content (chunks are already sized appropriately)
    if chunk.content:
        lines.append(f"    Content: {chunk.content}")

    return "\n".join(lines)


# Grounding rules appended to every context section (US-17c). They target the
# observed failure: asked about year 2031, the model extrapolated from 2021
# figures and added general knowledge instead of saying the fact was absent.
GROUNDING_RULES = (
    "RULES FOR YOUR ANSWER (strict):\n"
    "- Use ONLY facts stated in the context above.\n"
    "- If the exact fact requested (a year, an amount, a date, a name…) is "
    "not in the context, say so explicitly, then mention the closest "
    "information the context actually contains. NEVER extrapolate from one "
    "year, period, or document to another.\n"
    "- Do NOT add general knowledge, typical values, or advice that is not "
    "in the context.\n"
    "- Translating, summarizing, or reformatting the context above IS allowed "
    "and expected when the user asks for it: a faithful translation of context "
    "content is grounded in the context, not an extrapolation.\n"
    "- Cite sources only from the context above, by their exact file name "
    "or path. Never mention any other source.\n"
    "- If SEVERAL documents in the context contain the requested information "
    "(the same email, name, reference or fact appears in more than one), cite "
    "ALL of them. Never imply a single source when several documents apply."
)


def build_chunks_context_section(
    context_chunks: List[ContextChunk],
    max_tokens: int,
    include_metadata: bool = True,
) -> str:
    """
    Build the context section from chunks.

    Args:
        context_chunks: Chunks to include as context
        max_tokens: Max tokens for context section
        include_metadata: Whether to include metadata in formatting

    Returns:
        Formatted context section string
    """
    if not context_chunks:
        return ""

    # Count unique documents
    unique_docs = len(set(c.doc_id for c in context_chunks))

    lines = [
        "=" * 60,
        "CONTEXT FROM YOUR DOCUMENTS",
        f"Found {len(context_chunks)} relevant passages from {unique_docs} document(s):",
        "=" * 60,
        "",
    ]

    tokens_used = estimate_tokens("\n".join(lines))
    chunks_included = 0

    for i, chunk in enumerate(context_chunks, 1):
        chunk_text = format_context_chunk(chunk, i, include_metadata)
        chunk_tokens = estimate_tokens(chunk_text)

        if tokens_used + chunk_tokens > max_tokens:
            # Truncate this chunk to fit
            remaining_tokens = max_tokens - tokens_used - 50  # Buffer
            if remaining_tokens > 100:
                truncated = truncate_to_tokens(chunk_text, remaining_tokens)
                lines.append(truncated)
                chunks_included += 1
            break

        lines.append(chunk_text)
        lines.append("")  # Blank line between chunks
        tokens_used += chunk_tokens
        chunks_included += 1

    lines.extend([
        "",
        "=" * 60,
        f"END OF CONTEXT ({chunks_included} passages)",
        "=" * 60,
        GROUNDING_RULES,
        "",
    ])

    return "\n".join(lines)


def build_context_section(
    context_docs: List[ContextDocument],
    max_tokens: int,
    include_metadata: bool = True,
    full_content_paths: Optional[Set[str]] = None,
) -> str:
    """
    Build the context section to prepend to the prompt.

    Args:
        context_docs: Documents to include as context
        max_tokens: Max tokens for context section
        include_metadata: Whether to include metadata in formatting

    Returns:
        Formatted context section string
    """
    if not context_docs:
        return ""

    lines = [
        "=" * 60,
        "CONTEXT FROM YOUR DOCUMENTS",
        "The following documents from your local files may be relevant:",
        "=" * 60,
        "",
    ]

    tokens_used = estimate_tokens("\n".join(lines))
    docs_included = 0

    for i, doc in enumerate(context_docs, 1):
        # A document the user named explicitly is included in full (bounded by the
        # token budget below); the rest keep the 500-char preview.
        full = bool(full_content_paths and doc.path in full_content_paths)
        doc_text = format_context_document(
            doc, i, include_metadata, max_content_chars=None if full else 500
        )
        doc_tokens = estimate_tokens(doc_text)

        if tokens_used + doc_tokens > max_tokens:
            # Truncate this document to fit
            remaining_tokens = max_tokens - tokens_used - 50  # Buffer
            if remaining_tokens > 100:
                truncated = truncate_to_tokens(doc_text, remaining_tokens)
                lines.append(truncated)
                docs_included += 1
            break

        lines.append(doc_text)
        lines.append("")  # Blank line between docs
        tokens_used += doc_tokens
        docs_included += 1

    lines.extend([
        "",
        "=" * 60,
        f"END OF CONTEXT ({docs_included} documents)",
        "=" * 60,
        GROUNDING_RULES,
        "",
    ])

    return "\n".join(lines)
