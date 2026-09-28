# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""RAG (Retrieval-Augmented Generation) Engine for AiTao.

Provides RAGEngine: search indexed chunks/documents, build enriched
prompts with context, return sources for transparency.
Models in rag_models.py; formatting in rag_context_formatter.py.
"""

from typing import Any, Dict, List, Optional
import time

from aitao.core.config import ConfigManager
from aitao.core.logger import StructuredLogger
from aitao.llm.rag_models import ContextChunk, ContextDocument, RAGResult
from aitao.llm.rag_context_formatter import (
    build_chunks_context_section,
    build_context_section,
    estimate_tokens,
    format_context_document,
    truncate_to_tokens,
)

# Backward-compatible re-exports
__all__ = ["RAGEngine", "RAGResult", "ContextDocument", "ContextChunk"]


class RAGEngine:
    """Retrieval-Augmented Generation Engine.

    Enriches prompts with relevant context from indexed documents
    using hybrid search (semantic + full-text).
    """
    
    # Default configuration values
    DEFAULT_MAX_CONTEXT_DOCS = 5
    DEFAULT_MAX_CONTEXT_CHUNKS = 5
    DEFAULT_CONTEXT_MAX_TOKENS = 2000
    DEFAULT_MIN_RELEVANCE_SCORE = 0.3
    DEFAULT_INCLUDE_METADATA = True
    DEFAULT_USE_CHUNKS = True
    
    def __init__(self, config: ConfigManager, logger: StructuredLogger):
        # RAG chat is a Core feature (PRD §5): searching and chatting over indexed
        # text documents is free. Premium gates the document *perimeter* (advanced
        # formats / OCR) at ingestion, not the chat engine. Freemium scope fix.
        self.config = config
        self.logger = logger
        
        rag = config.rag
        self.max_context_docs = rag.max_context_docs
        self.max_context_chunks = rag.max_context_chunks
        self.context_max_tokens = rag.context_max_tokens
        self.min_relevance_score = rag.min_relevance_score
        self.include_metadata = rag.include_metadata
        self.use_chunks = rag.use_chunks
        self.distill_query = rag.distill_query  # US-89-1
        self.distill_max_doc_ratio = rag.distill_max_doc_ratio
        self.pin_exact_token = rag.pin_exact_token  # US-89-2
        self.pin_max_docs = rag.pin_max_docs
        self.reliability_debug = rag.reliability_debug  # US-102, étude §6.4 5th invariant

        # Lazy-loaded search engine
        self._search_engine = None
        
        self.logger.info(
            "RAGEngine initialized",
            metadata={
                "max_context_docs": self.max_context_docs,
                "max_context_chunks": self.max_context_chunks,
                "context_max_tokens": self.context_max_tokens,
                "min_relevance_score": self.min_relevance_score,
                "use_chunks": self.use_chunks,
            }
        )
    
    @property
    def search_engine(self):
        """Lazy-load HybridSearchEngine."""
        if self._search_engine is None:
            try:
                from aitao.search.hybrid_engine import HybridSearchEngine
                self._search_engine = HybridSearchEngine()
            except Exception as e:
                self.logger.error(
                    "Failed to initialize HybridSearchEngine",
                    metadata={"error": str(e)}
                )
                raise
        return self._search_engine

    def embed_texts(self, texts: List[str]) -> Any:
        """Embed texts with the already-loaded retrieval model (US-076).

        Reuses bge-m3 (loaded for search) so the grounding check loads nothing.
        Returns a (len(texts), dim) numpy array. Raises if the embedding model
        is unavailable — callers run this inside a guarded try.
        """
        client = self.search_engine.lancedb_client
        if client is None or not hasattr(client, "embed_texts"):
            raise RuntimeError("Embedding model unavailable for grounding check")
        return client.embed_texts(texts)
    
    def search_context(
        self,
        query: str,
        max_docs: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[ContextDocument]:
        """Search for relevant documents to use as context."""
        max_docs = max_docs or self.max_context_docs
        
        self.logger.debug(
            "Searching for context",
            metadata={"query": query[:100], "max_docs": max_docs}
        )
        
        try:
            # Import SearchFilter for type construction
            from aitao.search.hybrid_engine import SearchFilter
            
            # Build search filter
            search_filter = None
            if filters:
                search_filter = SearchFilter(
                    path_contains=filters.get("path_contains"),
                    category=filters.get("category"),
                    language=filters.get("language"),
                )
            
            # Execute hybrid search
            response = self.search_engine.search_sync(
                query=query,
                limit=max_docs * 2,  # Get more to filter by score
                filters=search_filter,
            )
            
            # Convert to ContextDocument and filter by score
            context_docs = []
            for result in response.results:
                if result.score >= self.min_relevance_score:
                    context_docs.append(ContextDocument(
                        id=result.id,
                        path=result.path,
                        title=result.title,
                        content=result.content,
                        score=result.score,
                        category=result.category,
                        language=result.language,
                        metadata=result.metadata,
                    ))
            
            # Limit to max_docs
            context_docs = context_docs[:max_docs]
            
            self.logger.info(
                "Context search completed",
                metadata={
                    "query": query[:50],
                    "docs_found": len(context_docs),
                    "search_time_ms": response.search_time_ms,
                }
            )
            
            return context_docs
            
        except Exception as e:
            self.logger.error(
                "Context search failed",
                metadata={"error": str(e), "query": query[:50]}
            )
            # Return empty list on error - don't block the prompt
            return []
    
    @staticmethod
    def _is_quarantined_path(path: str) -> bool:
        """US-088: True if path is under the system temp dir (dead/test pollution).

        Defence-in-depth with the indexer guard: never feed a temp/dead path to the
        model. Scoped to the system temp dir on purpose, so it never hides legitimately
        deleted files kept by the trash feature (US-28).
        """
        if not path:
            return False
        import tempfile
        from pathlib import Path
        try:
            Path(path).resolve().relative_to(Path(tempfile.gettempdir()).resolve())
            return True
        except (ValueError, OSError):
            return False

    def search_chunks_context(
        self,
        query: str,
        max_chunks: Optional[int] = None,
    ) -> List[ContextChunk]:
        """Search for relevant chunks to use as context (US-023)."""
        max_chunks = max_chunks or self.max_context_chunks
        
        self.logger.debug(
            "Searching for chunk context",
            metadata={"query": query[:100], "max_chunks": max_chunks}
        )
        
        try:
            # Execute chunk search
            response = self.search_engine.search_chunks(
                query=query,
                limit=max_chunks,
                min_score=self.min_relevance_score,
            )
            
            # Convert to ContextChunk
            context_chunks = []
            for chunk in response.chunks:
                if self._is_quarantined_path(chunk.path):
                    continue  # US-088: never feed a system-temp (dead/test) path to the model
                context_chunks.append(ContextChunk(
                    chunk_id=chunk.chunk_id,
                    doc_id=chunk.doc_id,
                    path=chunk.path,
                    title=chunk.title,
                    content=chunk.content,
                    chunk_index=chunk.chunk_index,
                    total_chunks=chunk.total_chunks,
                    score=chunk.score,
                    metadata=chunk.metadata,
                ))
            
            self.logger.info(
                "Chunk context search completed",
                metadata={
                    "query": query[:50],
                    "chunks_found": len(context_chunks),
                    "unique_docs": response.unique_docs,
                    "search_time_ms": response.search_time_ms,
                }
            )
            
            return context_chunks
            
        except Exception as e:
            self.logger.warning(
                "Chunk search failed, falling back to document search",
                metadata={"error": str(e), "query": query[:50]}
            )
            # Return empty - caller should fallback to document search
            return []
    
    def enrich_prompt(
        self,
        prompt: str,
        max_context_docs: Optional[int] = None,
        max_context_tokens: Optional[int] = None,
        max_context_chunks: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
        system_instruction: Optional[str] = None,
        use_chunks: Optional[bool] = None,
        search_query: Optional[str] = None,
    ) -> RAGResult:
        """Enrich a user prompt with relevant context from indexed documents.

        ``search_query`` (US-12) is what we search the index with; ``prompt`` is
        what gets embedded into the enriched prompt. They differ only for
        multi-turn retrieval, where the search is widened with prior turns while
        the user's actual question stays intact. Defaults to ``prompt``.
        """
        start_time = time.time()

        # What we retrieve with (may include prior turns) vs. what we embed
        query = search_query or prompt

        # I-17 — meta-question shape reduction (étude US-106): "quels
        # documents parlent de X ?" style questions retrieve on scaffolding
        # words ("documents", "parlent", "quels"), drowning the very term
        # that would find the target, at BOTH the chunk and the document
        # stage below (same `query` feeds both). Narrow, deterministic, no
        # oracle needed — runs UNCONDITIONALLY, independent of the US-89-1
        # rarity gate right after it (which some deployments, and the golden
        # bench on a tiny corpus, turn off). Fail-open: unrecognised shapes
        # leave `query` untouched.
        query = self._reduce_meta_question(query)

        # US-89-1: distill the search query to its salient tokens (rare-in-index
        # + structured) so multilingual function words don't pollute the
        # embedding. The displayed prompt and exact-match pinning use the raw text.
        if self.distill_query:
            query = self._distill_query(query)

        # Determine retrieval mode
        should_use_chunks = use_chunks if use_chunks is not None else self.use_chunks
        
        self.logger.info(
            "Enriching prompt with RAG context",
            metadata={
                "prompt_length": len(prompt),
                "mode": "chunks" if should_use_chunks else "documents",
            }
        )
        
        context_docs: List[ContextDocument] = []
        context_chunks: List[ContextChunk] = []
        context_section = ""
        mode = "documents"
        
        # Try chunk-based retrieval first (US-023)
        if should_use_chunks:
            context_chunks = self.search_chunks_context(
                query=query,
                max_chunks=max_context_chunks or self.max_context_chunks,
            )
            
            if context_chunks:
                mode = "chunks"
                max_tok = max_context_tokens or self.context_max_tokens
                context_section = build_chunks_context_section(
                    context_chunks, max_tok, self.include_metadata,
                )
        
        # Fallback to document-based retrieval
        if not context_section:
            context_docs = self.search_context(
                query=query,
                max_docs=max_context_docs,
                filters=filters,
            )
            mode = "documents"
            max_tok = max_context_tokens or self.context_max_tokens
            context_section = build_context_section(
                context_docs, max_tok, self.include_metadata,
            )
        
        search_time_ms = (time.time() - start_time) * 1000
        
        # Build enriched prompt
        parts = []
        
        if system_instruction:
            parts.append(system_instruction)
            parts.append("")
        
        if context_section:
            parts.append(context_section)
        
        parts.append(prompt)
        
        enriched_prompt = "\n".join(parts)
        
        # Estimate total context tokens
        total_context_tokens = estimate_tokens(context_section)
        
        # Log with mode-specific info
        context_count = len(context_chunks) if mode == "chunks" else len(context_docs)
        self.logger.info(
            "Prompt enriched successfully",
            metadata={
                "mode": mode,
                "context_items": context_count,
                "context_tokens": total_context_tokens,
                "search_time_ms": round(search_time_ms, 2),
            }
        )
        
        return RAGResult(
            original_prompt=prompt,
            enriched_prompt=enriched_prompt,
            context_docs=context_docs,
            context_chunks=context_chunks,
            total_context_tokens=total_context_tokens,
            search_time_ms=search_time_ms,
            mode=mode,
        )
    
    def enrich_messages(
        self,
        messages: List[Dict[str, str]],
        max_context_docs: Optional[int] = None,
        max_context_tokens: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
        dossier_state: Optional[Dict[str, Any]] = None,
    ) -> tuple[List[Dict[str, str]], List[ContextDocument], float]:
        """Enrich chat messages with RAG context on the last user message.

        ``dossier_state`` (US-103): optional dict the conversation-dossier
        hook fills with this turn's dossier view (action, referent, candidate
        stack, ambiguity probe) for the upstream context gate — see
        conversation_dossier_hook.py for the key table. Left untouched when
        the turn never reaches the hook (no user message).
        """
        if not messages:
            return messages, [], 0.0
        
        # Find the last user message
        last_user_idx = None
        last_user_content = None
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                last_user_idx = i
                last_user_content = messages[i].get("content", "")
                break
        
        if last_user_idx is None or not last_user_content:
            return messages, [], 0.0

        # Pass 1 (US-12): search with the current question alone.
        result = self.enrich_prompt(
            prompt=last_user_content,
            max_context_docs=max_context_docs,
            max_context_tokens=max_context_tokens,
            filters=filters,
        )
        context = self._score_context(result, last_user_content)

        # Pass 2 (US-12): if pass 1 would not let the gate actually answer
        # (refusal/reformulation), widen the search with recent conversation
        # turns and retry — so a follow-up like "and from Giant?" reuses the
        # previous turn. The question itself is never changed (only what we
        # search with). A standalone question the gate can already answer never
        # pays this second pass.
        if self._pass_is_weak(last_user_content, context):
            history_query = self._recent_user_query(messages, last_user_idx)
            if history_query and history_query != last_user_content:
                result2 = self.enrich_prompt(
                    prompt=last_user_content,
                    max_context_docs=max_context_docs,
                    max_context_tokens=max_context_tokens,
                    filters=filters,
                    search_query=history_query,
                )
                context2 = self._score_context(result2, history_query)
                if not self._pass_is_weak(last_user_content, context2):
                    result, context = result2, context2

        # US-30 — full-text augmentation: a document the question names by
        # filename, or quotes verbatim, is pinned to the front of the context
        # (never replacing semantic results). Deterministic and high-precision,
        # so it cannot reintroduce hallucination on off-corpus questions. A
        # pinned doc already present (e.g. retrieved but scored 0 by anchoring)
        # is upgraded to its pinned score, not duplicated.
        pinned = self._keyword_pinned_docs(last_user_content)

        # US-102 — conversation dossier (ÉPIC-30 brique 1, I-11, étude §6.6):
        # a follow-up turn ("il", "ce document", a short question) HOLDS the
        # session's current referent; an ordinal reference ("le premier
        # document") RECALLS a specific past one — a full-session replay, not
        # the 3-turn window below (US-12's HISTORY_TURNS stays as-is for its
        # own narrower purpose: widening the search STRING on a weak pass).
        # Purely additive: adds at most one doc to `pinned` and, if it fires,
        # zeroes every OTHER candidate's anchoring score further down —
        # nothing is ever removed from `context`. Logic lives in
        # conversation_dossier_hook.py (thin pre-step, kept out of this
        # already-large file).
        from aitao.llm.conversation_dossier_hook import dossier_solo_anchor, pin_turn_referent

        dossier_doc, dossier_solo = pin_turn_referent(
            self, messages, last_user_idx, last_user_content, bool(pinned),
            state=dossier_state,
        )
        if dossier_doc is not None and dossier_doc.path not in {d.path for d in pinned}:
            pinned = [dossier_doc] + pinned

        # US-RAG-name volet C — a file named in a *recent* turn stays pinned:
        # "translate X.pdf" then "now the rest of the pages" must keep X.pdf in
        # the context even though the follow-up doesn't repeat the name. Only the
        # named-doc resolver widens to history (deterministic, coverage-guarded);
        # exact-token and verbatim-phrase pinning stay current-turn only, so a
        # stale quote from three turns ago cannot keep pinning documents.
        history_query = self._recent_user_query(messages, last_user_idx)
        if history_query and history_query != last_user_content:
            seen_paths = {d.path for d in pinned}
            pinned += [
                d for d in self._resolved_named_docs(history_query)
                if d.path not in seen_paths
            ]

        pinned_paths = {d.path for d in pinned}
        if pinned:
            context = pinned + [d for d in context if d.path not in pinned_paths]

        if dossier_solo and dossier_doc is not None:
            # A held/recalled referent is the ONLY document allowed to anchor
            # this turn (I-11: a literal word like "pages" must not anchor an
            # unrelated document just because the follow-up's own words match
            # it) — additive still: every other document stays IN `context`,
            # visible to the model, just not certified as grounding evidence.
            context = dossier_solo_anchor(context, dossier_doc.path)

        # US-90-3 — defensive dedup: identical content under two paths (a copied
        # file) must not appear twice in the context, the sources, or the notice.
        deduped = self._dedup_by_content(context)
        if pinned or len(deduped) < len(context):
            context = deduped
            enriched_prompt = self._rebuild_enriched_prompt(
                context, last_user_content, max_context_tokens,
                full_content_paths=pinned_paths,
            )
        else:
            enriched_prompt = result.enriched_prompt

        enriched_messages = messages.copy()
        enriched_messages[last_user_idx] = {
            "role": "user",
            "content": enriched_prompt,
        }
        return enriched_messages, context, result.search_time_ms

    # ------------------------------------------------------------------
    # Full-text augmentation: filename + exact-token + exact-phrase (US-30, US-89-2)
    # ------------------------------------------------------------------

    # A query span of at least this many consecutive words found verbatim in a
    # document counts as an exact-phrase hit (guards against generic matches).
    PHRASE_MIN_WORDS = 5

    def _keyword_pinned_docs(self, query: str) -> List[ContextDocument]:
        """Documents to pin to the context from full-text signals (US-30, US-89-2).

        Three high-precision sources, all deterministic:
          - filename: a salient term of the query equals an indexed file's stem;
          - exact token: a distinctive token (email, ID, quoted, CJK run) appears
            verbatim — pins EVERY document that contains it (US-89-2);
          - exact phrase: a run of >= PHRASE_MIN_WORDS query words appears
            verbatim in a document.
        Returns [] for an ordinary question, so off-corpus queries are
        unaffected. Deduplicated by path (earlier sources take precedence).
        """
        pinned: dict = {}
        for doc in (
            self._named_file_docs(query)
            + self._resolved_named_docs(query)  # US-RAG-name: multi-word filenames
            + self._exact_token_docs(query)  # US-89-2
            + self._verbatim_phrase_docs(query)
        ):
            if doc.path and doc.path not in pinned:
                pinned[doc.path] = doc
        return list(pinned.values())

    def _resolved_named_docs(self, query: str) -> List[ContextDocument]:
        """Docs the query names by a (possibly multi-word) filename/title.

        Complements ``_named_file_docs`` (which only matches a single-token stem):
        it resolves a title buried in a natural question via title-only search +
        token coverage, so "translate 20260701_Assurance Maladie taiwan.pdf" pins
        that document even though its stem contains spaces. Deterministic and
        high-precision (coverage threshold + distinctive-token guard), so an
        ordinary question pins nothing. (US-RAG-name.)
        """
        from aitao.llm.named_doc_resolver import resolve_named_documents

        client = getattr(self.search_engine, "meilisearch_client", None)
        if client is None:
            return []
        try:  # a pin source must never break chat (mirrors _meili_search)
            matches = resolve_named_documents(query, client)
        except Exception:
            return []
        return [doc for m in matches if (doc := self._fetch_raw_doc(m.path))]

    def _named_file_docs(self, query: str) -> List[ContextDocument]:
        """Docs whose filename stem exactly equals a salient term of the query."""
        from pathlib import PurePath

        from aitao.llm.query_terms import salient_terms

        terms = {t.lower() for t in salient_terms(query)}
        if not terms:
            return []
        matched: List[str] = []
        seen: set = set()
        for term in terms:
            for hit in self._meili_search(term, limit=5):
                path = str(hit.get("path", ""))
                if path and path not in seen and PurePath(path).stem.lower() == term:
                    seen.add(path)
                    matched.append(path)
        return [doc for p in matched if (doc := self._fetch_raw_doc(p))]

    def _verbatim_phrase_docs(self, query: str) -> List[ContextDocument]:
        """Docs containing a run of >= PHRASE_MIN_WORDS query words verbatim.

        Uses the raw stored content (``get_document``), not the search excerpt:
        the latter is highlighted and cropped, which breaks substring matching.
        """
        if len(query.split()) < self.PHRASE_MIN_WORDS:
            return []
        windows = self._word_windows(query, self.PHRASE_MIN_WORDS)
        out: List[ContextDocument] = []
        for hit in self._meili_search(query, limit=3):
            doc = self._fetch_raw_doc(str(hit.get("path", "")))
            if doc and any(w in doc.content.lower() for w in windows):
                out.append(doc)
        return out

    def _exact_token_docs(self, query: str) -> List[ContextDocument]:
        """Docs containing a distinctive token of the query verbatim (US-89-2).

        A single distinctive token — an email, code/ID, quoted span, or CJK run
        (>= 2 chars) — pins EVERY document that contains it verbatim, not only the
        top semantic hit. This covers the "where does this email/ID appear?" case
        (US-90): the same address can live in several documents and all of them
        are true sources. Distinctiveness replaces phrase pinning's >= N-words
        guard, so an ordinary question pins nothing. Capped at ``pin_max_docs``
        (config) to protect the context budget; hits are taken in Meilisearch
        relevance order and confirmed on the raw stored content (NFKC-folded),
        never the cropped/highlighted excerpt. Each pinned doc carries an excerpt
        centered on the token (US-90), so the model sees the token even in a long
        document the formatter would otherwise crop before it.
        """
        if not self.pin_exact_token:
            return []
        import unicodedata

        from aitao.llm.query_distiller import structured_tokens

        tokens = structured_tokens(query, min_nonlatin_len=2)
        if not tokens:
            return []
        out: List[ContextDocument] = []
        seen: set = set()
        for token in tokens:
            if len(out) >= self.pin_max_docs:
                break
            needle = unicodedata.normalize("NFKC", token).lower()
            for hit in self._meili_search(token, limit=self.pin_max_docs * 2):
                if len(out) >= self.pin_max_docs:
                    break
                path = str(hit.get("path", ""))
                if not path or path in seen:
                    continue
                doc = self._fetch_raw_doc(path)
                if doc and needle in unicodedata.normalize(
                    "NFKC", doc.content
                ).lower():
                    seen.add(path)
                    out.append(
                        doc.model_copy(
                            update={"content": self._excerpt_around(doc.content, token)}
                        )
                    )
        return out

    # How many candidates to confirm when counting a token's bearer documents.
    # Generous (a "where does X appear?" query is rare); beyond this the notice
    # says "at least N", which stays truthful.
    BEARER_COUNT_LIMIT = 60

    def count_token_bearers(self, token: str) -> int:
        """Number of indexed documents containing ``token`` verbatim (US-90-5).

        Unlike ``_exact_token_docs`` this is NOT capped at ``pin_max_docs``: it
        lets the multi-source notice state the true total when pinning limited the
        listed sources. Full-text search gathers candidates, each confirmed on the
        raw NFKC-folded content. Counts DISTINCT contents (US-90-3: a copied file
        under two paths is one source). Never raises (returns 0 on any error).
        """
        import unicodedata

        try:
            needle = unicodedata.normalize("NFKC", token).lower()
            seen_paths: set = set()
            seen_hashes: set = set()
            for hit in self._meili_search(token, limit=self.BEARER_COUNT_LIMIT):
                path = str(hit.get("path", ""))
                if not path or path in seen_paths:
                    continue
                seen_paths.add(path)
                doc = self._fetch_raw_doc(path)
                if doc and needle in unicodedata.normalize(
                    "NFKC", doc.content
                ).lower():
                    seen_hashes.add(self._content_hash(doc.content))
            return len(seen_hashes)
        except Exception:
            return 0

    @staticmethod
    def _content_hash(content: str) -> str:
        """Stable hash of a document's content (NFKC-folded) for dedup (US-90-3)."""
        import hashlib
        import unicodedata

        norm = unicodedata.normalize("NFKC", content or "").strip()
        return hashlib.sha256(norm.encode("utf-8")).hexdigest()

    @classmethod
    def _dedup_by_content(cls, context: List[ContextDocument]) -> List[ContextDocument]:
        """Drop documents whose content duplicates an earlier one (US-90-3).

        The same content under two paths (a copied file) is kept once — the first
        occurrence, so the pinned / most-relevant order is preserved. Documents
        with empty content are never dropped (nothing to compare). Defensive: a
        clean index has none, but a duplicated file must not appear twice in the
        context, the sources, or the multi-source notice.
        """
        seen: set = set()
        out: List[ContextDocument] = []
        for doc in context:
            content = (getattr(doc, "content", "") or "").strip()
            if content:
                key = cls._content_hash(content)
                if key in seen:
                    continue
                seen.add(key)
            out.append(doc)
        return out

    # Characters of context kept on each side of the token in a pinned excerpt
    # (US-90). Small enough that the formatter's per-doc crop can't hide the
    # token again, large enough to show it inside a sentence.
    EXCERPT_RADIUS = 200

    @staticmethod
    def _excerpt_around(content: str, token: str, radius: int = EXCERPT_RADIUS) -> str:
        """Window of ~radius chars on each side of the token's first occurrence.

        A long pinned document is otherwise cropped to its first 500 chars by the
        context formatter, which can hide the token and stop the model citing the
        doc (US-90, the "where does this email appear?" case). Centering the
        excerpt on the token guarantees the model sees it. NFKC-folded and
        case-insensitive; falls back to the document head if the token is not
        locatable (should not happen after verbatim confirmation).
        """
        import unicodedata

        norm = unicodedata.normalize("NFKC", content)
        idx = norm.lower().find(unicodedata.normalize("NFKC", token).lower())
        if idx < 0:
            return norm[: 2 * radius]
        start = max(0, idx - radius)
        end = min(len(norm), idx + len(token) + radius)
        prefix = "…" if start > 0 else ""
        suffix = "…" if end < len(norm) else ""
        return f"{prefix}{norm[start:end]}{suffix}"

    def _reduce_meta_question(self, query: str) -> str:
        """Reduce a meta-question ("quels documents parlent de X ?") to its
        bare subject X, verbatim (I-17, étude US-106 volet 2bis).

        Pure regex, no oracle, never raises. Traceable like every ÉPIC-30
        gate: logged at INFO when ``[rag] reliability_debug`` is on, DEBUG
        otherwise — same idiom as intent_router._log / context_gate._log.
        Fail-open: an unrecognised shape returns ``query`` unchanged.
        """
        from aitao.llm.query_meta_shape import reduce_meta_question

        result = reduce_meta_question(query)
        if result.matched:
            log = self.logger.info if self.reliability_debug else self.logger.debug
            log(
                "Meta-question shape reduced (I-17)",
                metadata={
                    "shape": result.shape,
                    "raw": query[:100],
                    "subject": result.subject[:100],
                },
            )
            return result.subject
        if result.near_miss:
            self.logger.debug(
                "Meta-question opener seen, no shape matched",
                metadata={"raw": query[:100], "reason": result.near_miss_reason},
            )
        return query

    def _distill_query(self, query: str) -> str:
        """Reduce a raw question to its salient search tokens (US-89-1).

        Uses Meilisearch document frequency as the rarity oracle ("salient =
        rare"). Never raises and never returns blank: any failure or an empty
        distillation falls back to the original query.
        """
        from aitao.llm.query_distiller import distill_query
        try:
            client = self.search_engine.meilisearch_client
            total = client.count("")
            if not total:
                return query
            distilled = distill_query(
                query, doc_freq=client.count, total_docs=total,
                max_doc_ratio=self.distill_max_doc_ratio,
            )
            if distilled != query:
                self.logger.info(
                    "Query distilled (US-89)",
                    metadata={"raw": query[:100], "distilled": distilled[:100]},
                )
            return distilled
        except Exception as e:
            self.logger.warning("Query distillation skipped", metadata={"error": str(e)})
            return query

    def _meili_search(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """Full-text search returning a list of hit dicts (never raises).

        The pinning augmentation must never break chat: any backend error or
        unexpected (non-list) return yields an empty result.
        """
        try:
            hits = self.search_engine.meilisearch_client.search(query, limit=limit)
        except Exception:
            return []
        return hits if isinstance(hits, list) else []

    @staticmethod
    def _word_windows(text: str, size: int) -> List[str]:
        """All lowercased sliding windows of ``size`` consecutive words."""
        words = text.lower().split()
        return [" ".join(words[i:i + size]) for i in range(len(words) - size + 1)]

    def _fetch_raw_doc(
        self, path: str, score: float = 1.0
    ) -> Optional[ContextDocument]:
        """Fetch a document's raw (un-highlighted, un-cropped) content by path."""
        if not path:
            return None
        from pathlib import PurePath

        from aitao.indexation.indexer_helpers import generate_doc_id

        try:
            doc = self.search_engine.meilisearch_client.get_document(
                generate_doc_id(path)
            )
        except Exception:
            return None
        if not doc:
            return None
        return ContextDocument(
            id=str(doc.get("id", "")),
            path=path,
            title=str(doc.get("title") or PurePath(path).name),
            content=str(doc.get("content", "") or ""),
            score=score,
            metadata=doc.get("metadata", {}) or {},
        )

    def _rebuild_enriched_prompt(
        self,
        context: List[ContextDocument],
        question: str,
        max_context_tokens: Optional[int],
        full_content_paths: Optional[set] = None,
    ) -> str:
        """Rebuild the enriched prompt's context section from doc-level context.

        ``full_content_paths`` (the pinned/named docs) are included with their
        full content so a "translate this file" request sees every page, not the
        500-char preview — bounded by the token budget.
        """
        from aitao.llm.rag_context_formatter import build_context_section

        section = build_context_section(
            context,
            max_context_tokens or self.context_max_tokens,
            self.include_metadata,
            full_content_paths=full_content_paths,
        )
        return f"{section}\n{question}"

    # ------------------------------------------------------------------
    # Multi-turn retrieval helpers (US-12)
    # ------------------------------------------------------------------

    # How many recent user turns (including the current one) feed the widened
    # fallback search. Small on purpose: enough for a follow-up, little noise.
    HISTORY_TURNS = 3

    def _score_context(
        self, result: RAGResult, anchor_query: str
    ) -> List[ContextDocument]:
        """Turn a RAGResult into scored ContextDocuments for the adequacy gate.

        Unifies the two retrieval modes. In chunk mode (the default)
        ``result.context_docs`` is empty — the context lives in
        ``result.context_chunks``. Chunk similarity scores are rank-based noise
        (~0.5-0.6 regardless of relevance), so the reliable signal is anchoring:
        a chunk keeps its score when its document matches ``anchor_query`` in
        full-text search, OR the chunk itself contains a salient term of it
        (semantic synonym bridging — "bail" finds "contrat de location").
        A chunk sharing nothing with the query is scored 0 (semantic noise).
        """
        if result.context_docs:
            return result.context_docs

        from aitao.llm.query_terms import anchor_terms, text_contains_term

        terms = anchor_terms(anchor_query)  # US-89-4: incl. CJK bigrams
        anchored = self._keyword_anchored_paths(anchor_query)

        # US-89-4: chunk similarity is rank-based noise (~0.5-0.6), so it can't be
        # compared to the weak-context threshold. The reliable signal is anchoring:
        #   - probe unusable (None, e.g. Meilisearch down) → keep raw scores (safety);
        #   - chunk grounded in the query's terms → STRONG (it must outrank the
        #     threshold so a genuinely relevant doc is never refused);
        #   - grounded in nothing → 0.0 (semantic noise → gate refuses).
        def _score(chunk) -> float:
            if anchored is None:
                return chunk.score
            grounded = chunk.path in anchored or text_contains_term(
                f"{chunk.path} {chunk.title} {chunk.content}", terms
            )
            return 1.0 if grounded else 0.0

        return [
            ContextDocument(
                id=chunk.chunk_id,
                path=chunk.path,
                title=chunk.title,
                content=chunk.content,
                score=_score(chunk),
                metadata=chunk.metadata,
            )
            for chunk in result.context_chunks
        ]

    @staticmethod
    def _pass_is_weak(message: str, context: List[ContextDocument]) -> bool:
        """True when the adequacy gate would not answer from this context.

        Aligned with the gate itself (``evaluate_refusal``): a retrieval pass
        is "weak" when it would lead to a refusal or a reformulation prompt —
        i.e. nothing relevant was found. Using the gate's own decision avoids
        false "success" when a chunk merely anchored on a common word.
        """
        from aitao.llm.context_adequacy import evaluate_refusal

        return evaluate_refusal(message, context) is not None

    def _recent_user_query(
        self, messages: List[Dict[str, str]], last_user_idx: int
    ) -> str:
        """Join the last ``HISTORY_TURNS`` user messages into one search query.

        Includes the current question (last) plus the prior user turns, so a
        terse follow-up inherits the topic of what came before.
        """
        user_texts = [
            messages[i].get("content", "")
            for i in range(last_user_idx + 1)
            if messages[i].get("role") == "user" and messages[i].get("content")
        ]
        return " ".join(user_texts[-self.HISTORY_TURNS:]).strip()

    def _keyword_anchored_paths(self, query: str) -> Optional[set]:
        """Paths matching ``query`` in full-text search (US-17c gate anchor).

        Returns None when the probe yields nothing usable (Meilisearch down,
        zero hits — e.g. a cross-lingual query) — callers then keep the raw
        chunk scores rather than wrongly flagging everything as weak.
        """
        try:
            from aitao.llm.query_terms import anchor_terms

            terms = anchor_terms(query)  # US-89-4: incl. CJK bigrams
            if not terms:
                return None
            hits = self.search_engine.meilisearch_client.search(
                " ".join(terms), limit=10
            )
            paths = {str(hit.get("path", "")) for hit in hits}
            paths.discard("")
            # Empty set (zero hits) is a real answer — it lets the gate refuse on
            # an absent term. None is reserved for "probe unusable" (error below),
            # where callers keep raw scores rather than refuse blindly. US-89-4.
            return paths
        except Exception as e:
            self.logger.warning(
                "Keyword anchoring probe failed, keeping chunk scores",
                metadata={"error": str(e)},
            )
            return None

    # ------------------------------------------------------------------
    # Backward compat: delegation methods for tests
    # ------------------------------------------------------------------

    def _format_context_document(self, doc, index, include_metadata=None):
        """Backward compat: delegate to rag_context_formatter."""
        if include_metadata is None:
            include_metadata = self.include_metadata
        return format_context_document(doc, index, include_metadata)

    @staticmethod
    def _estimate_tokens(text, chars_per_token=4):
        """Backward compat: delegate to rag_context_formatter."""
        return estimate_tokens(text, chars_per_token)

    @staticmethod
    def _truncate_to_tokens(text, max_tokens, chars_per_token=4):
        """Backward compat: delegate to rag_context_formatter."""
        return truncate_to_tokens(text, max_tokens, chars_per_token)

    def _build_context_section(self, context_docs, max_tokens=None, include_metadata=None):
        """Backward compat: delegate to rag_context_formatter."""
        if max_tokens is None:
            max_tokens = self.context_max_tokens
        if include_metadata is None:
            include_metadata = self.include_metadata
        return build_context_section(context_docs, max_tokens, include_metadata)
