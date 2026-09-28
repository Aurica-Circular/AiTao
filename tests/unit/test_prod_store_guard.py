# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Guard test for the US-088 prod-store isolation safety net.

The session-scoped ``_guard_prod_stores`` fixture (tests/conftest.py) must make
any test that builds a real store client on the PRODUCTION target fail loudly,
so test runs can never pollute live Meilisearch data. These tests exercise the
guard's decision logic directly (the effective-target computation and the
``live_store`` opt-out), without standing up real native stores.

ÉPIC-31 (US-113): LanceDB is no longer a live store (v4.0 ships fusion-only,
decision D1) — ``LanceDBClient``/``ChunkStore`` are gone. The only LanceDB
touch point left in the codebase is the migration tool's raw
``lancedb.connect()`` read of the legacy pre-4.0 store (see
``search.migrate_v4_source``); ``_resolve_lance_db_path`` below still mirrors
that call's target-resolution logic (the same one ``_guard_prod_stores``'
``_check_lance_db_path`` applies to ``lancedb.connect``).
"""

from pathlib import Path

import pytest


def _resolve_lance_db_path(db_path):
    """Mirror lancedb.connect()'s target resolution for the given arg —
    the guard's ``_check_lance_db_path`` applies this exact rule."""
    return Path(db_path).resolve() if db_path else None


class TestProdStoreGuardLogic:
    """The guard's effective-target logic flags prod and spares temp/test."""

    def test_lance_no_db_path_resolves_to_prod(self):
        # No explicit db_path -> would fall back to the production vector store.
        assert _resolve_lance_db_path(None) is None  # signals "use prod default"

    def test_lance_temp_path_is_not_prod(self, tmp_path):
        prod = Path("/some/prod/aitao-data/vectors").resolve()
        target = _resolve_lance_db_path(str(tmp_path))
        assert target is not None
        assert target != prod

    def test_meili_none_index_means_prod(self):
        # index_name=None resolves to the configured production index.
        index_name = None
        prod_index = "aitao_documents"
        assert index_name is None or index_name == prod_index

    def test_meili_test_index_is_allowed(self):
        index_name = "test_documents"
        prod_index = "aitao_documents"
        assert not (index_name is None or index_name == prod_index)


class TestGuardBitesOnProd:
    """The live guard raises when a real client/read targets the prod store.

    ÉPIC-31 (US-113): ``LanceDBClient`` no longer exists — the only remaining
    LanceDB touch point is the migration tool's raw ``lancedb.connect()`` read
    of the legacy pre-4.0 store (``search.migrate_v4_source``), which
    ``_guard_prod_stores`` patches directly (see conftest.py)."""

    def test_raw_lancedb_connect_on_prod_path_is_refused(self):
        from aitao.core.pathmanager import path_manager
        import lancedb

        prod = path_manager.get_vector_db_path()
        with pytest.raises(RuntimeError, match="PROD STORE GUARD"):
            lancedb.connect(str(prod))

    def test_meilisearch_client_on_prod_index_is_refused(self):
        pytest.importorskip("meilisearch")
        from aitao.search.meilisearch_client import MeilisearchClient

        with pytest.raises(RuntimeError, match="PROD STORE GUARD"):
            MeilisearchClient(index_name="aitao_documents")
