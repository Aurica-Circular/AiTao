# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# meilisearch_settings.py — US-094 ingestion-speed + multilingual relevance
# settings (ÉPIC-31, absorbed into US-111), applied to BOTH the document
# index (aitao_documents) and the excerpt index (aitao_chunks).
#
# Kept as its OWN module (rather than merged into meilisearch_admin.py's
# DEFAULT_SETTINGS, which predates this US) so any single key can be dropped
# in isolation without touching the unrelated 2023-era searchable/ranking
# config, per the golden-bench gate: a setting that regresses either golden
# suite (retrieval or conversations), in either engine mode (rrf/fusion),
# gets REMOVED here, not "fixed" by editing the test. Empirically, ALL keys
# below passed both golden suites in both engine modes on Meilisearch 1.49.0
# (see the US-111 delivery report) — nothing was dropped.

from typing import Any, Dict

#   proximityPrecision: "byAttribute" — faster indexing/search; documented by
#     Meilisearch as a near-zero relevance cost for documentary search (no
#     fine-grained word-distance ranking needed beyond "same attribute").
#   prefixSearch: "disabled" — AiTao has no search-as-you-type UI, so the
#     prefix database is pure overhead (indexing time + DB size).
#   searchCutoffMs: 150 — caps search latency as the corpus grows (value
#     fixed by the US-094 backlog note).
#   localizedAttributes — the corpus mixes fr/zh(/en) in the SAME
#     title/content/path fields (no per-language field split), so all three
#     locales are attached to those attributes; this is what lets Meilisearch
#     apply proper CJK segmentation to Chinese documents instead of treating
#     them as one long whitespace-free token. Locale codes are ISO 639-3
#     (fra/eng) and Meilisearch's own convention for Mandarin (cmn) —
#     confirmed accepted by a live 1.49.0 instance during this US.
US094_SETTINGS: Dict[str, Any] = {
    "proximityPrecision": "byAttribute",
    "prefixSearch": "disabled",
    "searchCutoffMs": 150,
    "localizedAttributes": [
        {
            "attributePatterns": ["title", "content", "path"],
            "locales": ["fra", "cmn", "eng"],
        },
    ],
}


class MeilisearchSettingsMixin:
    """Mixin adding ``apply_ingestion_settings()`` to a Meilisearch-backed
    client. Expects the host class to provide ``update_settings()`` (see
    ``MeilisearchAdminMixin``, always mixed in alongside this one)."""

    def apply_ingestion_settings(self) -> bool:
        """Apply the US-094 ingestion/relevance settings to THIS index —
        idempotent, safe to call on an ALREADY EXISTING index (unlike the
        creation-time path, which merges ``US094_SETTINGS`` into the initial
        ``update_settings`` call). Used by the reconstruire-à-côté tool
        (search/index_rebuild.py) and available standalone for ops/tests.
        """
        return self.update_settings(US094_SETTINGS)  # type: ignore[attr-defined]
