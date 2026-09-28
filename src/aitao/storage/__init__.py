# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""Storage layer: the backend-agnostic document repository contract (US-24).

ÉPIC-31 (US-113): the LanceDB-backed factories (make_lancedb_client,
make_lancedb_repository) are removed — v4.0 is fusion-only, Meilisearch is
the only live store.
"""

from aitao.storage.repository import (
    DocumentRepository,
    make_meilisearch_client,
    make_meilisearch_repository,
)

__all__ = [
    "DocumentRepository",
    "make_meilisearch_client",
    "make_meilisearch_repository",
]
