# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Shared pytest fixtures for AiTao tests.

This module provides session-scoped fixtures to optimize test performance.
Key optimization: Embedding models are loaded once per test session,
not once per test class, reducing test time by ~70%.
"""

import pytest
from pathlib import Path


# Set by the per-test ``_live_store_optout`` fixture: when True, the prod-store
# guard (US-088) is bypassed for the current test. Only read-only live-store
# smoke tests marked ``@pytest.mark.live_store`` may opt out.
_ALLOW_PROD_STORE = {"on": False}


# =============================================================================
# SESSION STARTUP MESSAGE
# =============================================================================

# Test modules that need heavy/native runtime (the AI embedding model,
# Meilisearch, OCR…). They run locally (full suite before every commit) but
# are skipped by the CI release gate, which runs on a clean machine without
# those installed: `pytest -m "not slow and not heavy"`.
HEAVY_TEST_MODULES = {
    "test_hybrid_search",
    "test_repository",
    "test_indexer",
    "test_chunker",
    "test_worker",
    "test_ocr_pipeline",
    "test_meilisearch_client",
    "test_mcp_tools",
    "test_api",
    "test_health",
}


def pytest_configure(config):
    """Register custom markers and show startup message."""
    # Register markers
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m not slow')"
    )
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests"
    )
    config.addinivalue_line(
        "markers", "requires_meilisearch: marks tests that need Meilisearch running"
    )
    config.addinivalue_line(
        "markers",
        "heavy: needs the AI model / LanceDB native / Meilisearch (skipped by CI gate)",
    )
    config.addinivalue_line(
        "markers",
        "live_store: read-only smoke test allowed to hit the PROD store "
        "(opts out of the US-088 prod-store guard)",
    )
    config.addinivalue_line(
        "markers",
        "stops_server: stops/restarts the REAL API server (I-14) — opt-in via "
        "AITAO_ALLOW_STOPS_SERVER=true, skipped by default even in a plain "
        "`pytest tests/e2e` run",
    )
    config.addinivalue_line(
        "markers",
        "golden_live: US-101 live golden bench — replays a scenario against "
        "the real running API/model, skips cleanly when the API is unreachable",
    )


def pytest_collection_modifyitems(config, items):
    """Auto-tag tests in heavy modules so the CI gate can deselect them."""
    for item in items:
        module = item.module.__name__.rsplit(".", 1)[-1] if item.module else ""
        if module in HEAVY_TEST_MODULES:
            item.add_marker(pytest.mark.heavy)


def pytest_sessionstart(session):
    """Show message at test session start."""
    print("\n" + "=" * 70)
    print("🧪 AiTao Test Suite")
    print("=" * 70)
    print("⏱️  Estimated time: ~3-4 minutes")
    print("📦 Loading embedding model (sentence-transformers)...")
    print("   This is normal - the model loads once for all tests.")
    print("=" * 70 + "\n")


# =============================================================================
# LOGGER CLEANUP (Prevent test pollution)
# =============================================================================

@pytest.fixture(autouse=True)
def cleanup_loggers():
    """
    Clean up logger cache and handlers after each test.
    
    This prevents state pollution between tests that use get_logger().
    Without this, loggers accumulate handlers and the cache persists
    across tests, causing intermittent failures when run as a suite.
    """
    import logging
    
    yield  # Run the test
    
    # Clean up after test
    try:
        from aitao.core import logger as logger_module
        # Clear the module-level logger cache
        if hasattr(logger_module, '_loggers'):
            logger_module._loggers.clear()
    except ImportError:
        pass
    
    # Also clean up any test loggers from Python's logging
    for name in list(logging.Logger.manager.loggerDict.keys()):
        if name.startswith(('test_', 'cached_', 'module', 'fallback', 'indexer')):
            logger = logging.getLogger(name)
            logger.handlers.clear()


@pytest.fixture(scope="session")
def embedding_model():
    """
    Load the embedding model once for the entire test session.
    
    This is the main optimization: loading sentence-transformers takes ~5s,
    so loading it once instead of 6+ times saves significant time.
    """
    from sentence_transformers import SentenceTransformer
    
    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    return model


@pytest.fixture(scope="session")
def embedding_dimension(embedding_model):
    """Get embedding dimension from the loaded model."""
    return embedding_model.get_sentence_embedding_dimension()


# =============================================================================
# MEILISEARCH FIXTURES
# =============================================================================

@pytest.fixture(scope="session")
def meilisearch_test_available():
    """Check if Meilisearch is available for integration tests."""
    try:
        import meilisearch
        client = meilisearch.Client("http://localhost:7700")
        client.health()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session", autouse=True)
def _purge_test_meilisearch_indexes():
    """Delete every ``test_*`` index created during the session (US-19).

    Tests run against the local Meilisearch (no test/prod separation), and
    per-test cleanup misses indexes left by crashed tests or renamed cases.
    This session-scoped guard removes any ``test_*`` index that appears during
    the run, leaving zero residue on the production server. Indexes that
    already existed before the run are left untouched (not ours to delete).
    """
    def _test_index_uids():
        try:
            import meilisearch
            client = meilisearch.Client("http://localhost:7700")
            client.health()
            raw = client.get_indexes()
            indexes = raw.get("results", raw) if isinstance(raw, dict) else raw
            return {idx.uid for idx in indexes if idx.uid.startswith("test_")}, client
        except Exception:
            return None, None

    pre_existing, _ = _test_index_uids()
    yield
    current, client = _test_index_uids()
    if current is None or client is None:
        return
    created = current - (pre_existing or set())
    for uid in created:
        try:
            client.delete_index(uid)
        except Exception:
            pass


# =============================================================================
# PROD-STORE GUARD (US-088 volet 4 — test/prod isolation)
# =============================================================================

@pytest.fixture(autouse=True)
def _live_store_optout(request):
    """Flip the prod-store guard off for tests marked ``live_store`` (read-only)."""
    _ALLOW_PROD_STORE["on"] = request.node.get_closest_marker("live_store") is not None
    try:
        yield
    finally:
        _ALLOW_PROD_STORE["on"] = False


@pytest.fixture(scope="session", autouse=True)
def _guard_prod_stores():
    """Fail any test that builds a real store client pointing at PROD.

    Tests must target ``test_*`` Meilisearch indexes (see
    ``_purge_test_meilisearch_indexes``). A test that constructs a
    ``MeilisearchClient`` on the production index (or with no override, which
    *resolves* to production), would silently pollute live data — the exact
    bug US-088 fixes. This guard patches ``__init__`` for the session and
    raises immediately on a prod target, so the offending test errors instead
    of writing.

    ÉPIC-31 (US-113): the LanceDB-specific guards (``LanceDBClient``,
    ``ChunkStore``) are gone along with those classes — v4.0 is fusion-only,
    Meilisearch is the only live store. The raw ``lancedb.connect()`` guard
    stays: ``search.migrate_v4_source`` still reads the legacy pre-4.0 store
    that way (lazy import, read-only), and a test pointing that at production
    must still fail exactly like it would for a wrapped client.

    It is a no-op outside the test session (the patch only lives for the run).
    """
    import os
    import inspect
    from pathlib import Path as _Path

    # Resolve the production targets ONCE, before any test runs.
    prod_lance = None
    try:
        from aitao.core.pathmanager import path_manager
        prod_lance = path_manager.get_vector_db_path().resolve()
    except Exception:
        pass

    prod_index = "aitao_documents"
    prod_chunks_index = "aitao_chunks"
    try:
        from aitao.core.config import get_config
        ms = get_config().search.meilisearch
        if ms and ms.index_name:
            prod_index = ms.index_name
        if ms and ms.chunks_index:
            prod_chunks_index = ms.chunks_index
    except Exception:
        pass

    import lancedb

    from aitao.search.meilisearch_client import MeilisearchClient
    from aitao.indexation.chunk_store_meili import MeiliChunkStore

    orig_meili = MeilisearchClient.__init__
    orig_meili_chunks = MeiliChunkStore.__init__
    orig_lancedb_connect = lancedb.connect
    sig_meili = inspect.signature(orig_meili)
    sig_meili_chunks = inspect.signature(orig_meili_chunks)

    def _node():
        return os.environ.get("PYTEST_CURRENT_TEST", "<unknown test>").split(" ")[0]

    def _check_lance_db_path(db_path, kind):
        """Raise if a vector-store db_path is missing (=> prod) or equals prod."""
        if prod_lance is None or _ALLOW_PROD_STORE["on"]:
            return
        target = _Path(db_path).resolve() if db_path else None
        if target is None:
            raise RuntimeError(
                f"PROD STORE GUARD (US-088): {_node()} built a {kind} with no db_path "
                f"-> resolves to the production store ({prod_lance}). "
                f"Pass a temp db_path."
            )
        if target == prod_lance:
            raise RuntimeError(
                f"PROD STORE GUARD (US-088): {_node()} built a {kind} on the production "
                f"path {prod_lance}. Use a temp db_path."
            )

    def guarded_meili(*args, **kwargs):
        if _ALLOW_PROD_STORE["on"]:
            return orig_meili(*args, **kwargs)
        index_name = sig_meili.bind_partial(*args, **kwargs).arguments.get("index_name")
        if index_name is None or index_name == prod_index:
            raise RuntimeError(
                f"PROD STORE GUARD (US-088): {_node()} built a MeilisearchClient on the "
                f"production index ({prod_index!r}). Pass index_name='test_...'."
            )
        return orig_meili(*args, **kwargs)

    def guarded_meili_chunks(*args, **kwargs):
        """ÉPIC-31 (US-110) — same pattern as guarded_meili, for the fusion
        engine's dedicated excerpt index (MeiliChunkStore)."""
        if _ALLOW_PROD_STORE["on"]:
            return orig_meili_chunks(*args, **kwargs)
        index_name = sig_meili_chunks.bind_partial(*args, **kwargs).arguments.get("index_name")
        if index_name is None or index_name == prod_chunks_index:
            raise RuntimeError(
                f"PROD STORE GUARD (US-088/US-110): {_node()} built a MeiliChunkStore on the "
                f"production chunks index ({prod_chunks_index!r}). Pass index_name='test_...'."
            )
        return orig_meili_chunks(*args, **kwargs)

    from aitao.search.migrate_v4 import MigrationV4Runner

    orig_migration_init = MigrationV4Runner.__init__
    sig_migration = inspect.signature(orig_migration_init)

    def guarded_migration_init(*args, **kwargs):
        """ÉPIC-31 (US-112) — MigrationV4Runner defaults every store-pointing
        parameter to the REAL production config when omitted (by design: the
        real migration IS supposed to target production). A test must
        therefore pass ALL THREE of docs_index_name/chunks_index_name/
        lancedb_path as explicit test_*/temp overrides — any one left at its
        implicit (None -> production) default fails here.
        """
        if not _ALLOW_PROD_STORE["on"]:
            bound = sig_migration.bind_partial(*args, **kwargs).arguments
            docs_index = bound.get("docs_index_name")
            chunks_index = bound.get("chunks_index_name")
            lance_path = bound.get("lancedb_path")
            lance_target = _Path(lance_path).resolve() if lance_path else None
            if (
                docs_index in (None, prod_index)
                or chunks_index in (None, prod_chunks_index)
                or lance_target is None
                or (prod_lance is not None and lance_target == prod_lance)
            ):
                raise RuntimeError(
                    f"PROD STORE GUARD (US-112): {_node()} built a MigrationV4Runner "
                    "without fully overriding docs_index_name/chunks_index_name/"
                    "lancedb_path with test_*/temp values -> would target production."
                )
        return orig_migration_init(*args, **kwargs)

    def guarded_lancedb_connect(uri, *args, **kwargs):
        """ÉPIC-31 (US-112/US-113) — the migration tool reads the legacy
        pre-4.0 LanceDB ``chunks``/``aitao_embeddings`` tables via a RAW
        ``lancedb.connect()`` (mirroring the US-110 gate study's proven read
        method); with ``LanceDBClient``/``ChunkStore`` removed, this is the
        ONLY remaining LanceDB touch point left in the whole codebase. A test
        that points the migration's source reading at the real production
        LanceDB directory must still fail exactly like a wrapped client would.
        """
        _check_lance_db_path(uri, "lancedb.connect")
        return orig_lancedb_connect(uri, *args, **kwargs)

    MeilisearchClient.__init__ = guarded_meili
    MeiliChunkStore.__init__ = guarded_meili_chunks
    MigrationV4Runner.__init__ = guarded_migration_init
    lancedb.connect = guarded_lancedb_connect
    try:
        yield
    finally:
        MeilisearchClient.__init__ = orig_meili
        MeiliChunkStore.__init__ = orig_meili_chunks
        MigrationV4Runner.__init__ = orig_migration_init
        lancedb.connect = orig_lancedb_connect


# =============================================================================
# PATH FIXTURES
# =============================================================================

@pytest.fixture(scope="session")
def project_root():
    """Get the project root directory."""
    return Path(__file__).parent.parent


@pytest.fixture(scope="session")
def src_path(project_root):
    """Get the source code path."""
    return project_root / "src"


@pytest.fixture(scope="session")
def test_data_path(project_root):
    """Get the test data path (if exists)."""
    test_data = project_root / "tests" / "data"
    test_data.mkdir(exist_ok=True)
    return test_data
