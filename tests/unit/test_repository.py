# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Unit tests for the DocumentRepository storage contract (US-24).

Proves two things:
- the real storage clients fulfil the contract (so they are valid repositories);
- a lightweight in-memory double also fulfils it, so business layers can be
  tested by swapping in a fake backend — the US-24 acceptance criterion.
"""

from aitao.core.models import Document
from aitao.storage.repository import DocumentRepository


class InMemoryRepository:
    """A tiny in-memory DocumentRepository — a swappable test double."""

    def __init__(self):
        self._docs: dict[str, Document] = {}

    def index_document(self, document: Document) -> str:
        self._docs[document.id] = document
        return document.id

    def search(self, query, limit=10, filter_category=None, filter_language=None):
        hits = [
            {"id": d.id, "path": d.path, "title": d.title}
            for d in self._docs.values()
        ]
        return hits[:limit]

    def get_document(self, doc_id):
        doc = self._docs.get(doc_id)
        return doc.model_dump() if doc else None

    def delete(self, doc_id):
        return self._docs.pop(doc_id, None) is not None

    def delete_by_path(self, path):
        before = len(self._docs)
        self._docs = {k: v for k, v in self._docs.items() if v.path != path}
        return len(self._docs) < before

    def get_stats(self):
        return {"total_documents": len(self._docs)}

    def all_doc_ids(self):
        return set(self._docs.keys())


def test_in_memory_double_satisfies_contract():
    assert isinstance(InMemoryRepository(), DocumentRepository)


def test_incomplete_class_does_not_satisfy_contract():
    class Partial:
        def index_document(self, document):  # missing the other operations
            return document.id

    assert not isinstance(Partial(), DocumentRepository)


def test_in_memory_double_round_trips():
    repo = InMemoryRepository()
    doc = Document(id="abc", path="/x/f.txt", title="f", content="hello")

    assert repo.index_document(doc) == "abc"
    assert repo.get_document("abc")["path"] == "/x/f.txt"
    assert repo.search("hello")[0]["id"] == "abc"
    assert repo.get_stats()["total_documents"] == 1
    assert repo.delete("abc") is True
    assert repo.get_document("abc") is None


def test_real_clients_fulfil_contract():
    """MeilisearchClient is the ONLY live DocumentRepository since v4.0
    (ÉPIC-31, US-113 — fusion-only, decision D1); LanceDBClient was removed."""
    from aitao.search.meilisearch_client import MeilisearchClient

    required = (
        "index_document", "search", "get_document",
        "delete", "delete_by_path", "get_stats",
    )
    missing = [m for m in required if not callable(getattr(MeilisearchClient, m, None))]
    assert not missing, f"MeilisearchClient missing repository methods: {missing}"
