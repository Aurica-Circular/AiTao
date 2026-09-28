# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# tests/unit/test_mcp_tools.py — Unit tests for MCP tools (US-055)
#
# Covers: aitao_search, aitao_ingest, aitao_stats,
#         aitao_ocr, aitao_extract, _premium guard
# Strategy: mock AiTao engines and license manager; test tool logic only.

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aitao.mcp_server.state import MCPServerState, set_state, get_state


# ============================================================================
# Helpers
# ============================================================================

def _run(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def _make_state(
    search_engine: Any = None,
    task_queue: Any = None,
    config: Any = None,
) -> MCPServerState:
    """Create and install a MCPServerState with the given mocks."""
    state = MCPServerState(
        search_engine=search_engine,
        task_queue=task_queue,
        config=config,
    )
    set_state(state)
    return state


def _get_tool_fn(mcp, tool_name: str):
    """Retrieve the raw async function of a registered FastMCP tool."""
    tool = _run(mcp.get_tool(tool_name))
    return tool.fn


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture(autouse=True)
def _clear_state():
    """Ensure state is cleared before and after each test."""
    set_state(None)
    yield
    set_state(None)


@pytest.fixture()
def mock_search_engine():
    """Mock HybridSearchEngine with async search()."""
    engine = MagicMock()
    engine.search = AsyncMock(return_value=[
        {
            "id": "doc-1",
            "title": "Test Document",
            "excerpt": "This is a test excerpt.",
            "_score": 0.95,
            "category": "notes",
            "path": "/tmp/test.md",
        },
        {
            "id": "doc-2",
            "title": "Second Doc",
            "content": "Fallback content field.",
            "score": 0.8,
            "category": "reports",
            "file_path": "/tmp/report.pdf",
        },
    ])
    engine.search_sync = MagicMock(return_value=[])
    return engine


@pytest.fixture()
def mock_task_queue():
    """Mock TaskQueue with async add_task()."""
    queue = MagicMock()
    queue.add_task = AsyncMock(return_value={"task_id": "task-001", "id": "task-001"})
    return queue


# ============================================================================
# Tests: state module
# ============================================================================

class TestMCPServerState:
    """Tests for mcp_server.state module."""

    def test_get_state_raises_when_not_initialised(self):
        with pytest.raises(RuntimeError, match="not initialised"):
            get_state()

    def test_set_and_get_state(self):
        state = _make_state(search_engine="engine", task_queue="queue")
        assert get_state() is state
        assert get_state().search_engine == "engine"

    def test_set_state_to_none_clears(self):
        _make_state(search_engine="engine")
        set_state(None)
        with pytest.raises(RuntimeError):
            get_state()


# ============================================================================
# Tests: _premium guard
# ============================================================================

class TestPremiumGuard:
    """Tests for mcp_server.tools._premium.require_premium."""

    def test_require_premium_passes_when_licensed(self):
        """No exception when LicenseManager.require_premium succeeds."""
        mock_lm = MagicMock()
        mock_lm.return_value.require_premium.return_value = None
        mock_module = MagicMock()
        mock_module.LicenseManager = mock_lm
        mock_module.PremiumFeatureError = Exception

        with patch.dict("sys.modules", {"aitao.core.license": mock_module}):
            import importlib
            import aitao.mcp_server.tools._premium as mod
            importlib.reload(mod)
            # Should not raise
            mod.require_premium("translation")

    def test_require_premium_raises_permission_error(self):
        """PermissionError raised when license check fails."""
        mock_lm = MagicMock()
        mock_lm.return_value.require_premium.side_effect = Exception("No license")
        mock_module = MagicMock()
        mock_module.LicenseManager = mock_lm
        mock_module.PremiumFeatureError = Exception

        with patch.dict("sys.modules", {"aitao.core.license": mock_module}):
            import importlib
            import aitao.mcp_server.tools._premium as mod
            importlib.reload(mod)
            with pytest.raises(PermissionError, match="Premium license"):
                mod.require_premium("ocr")

    def test_require_premium_fails_closed_on_import_error(self):
        """US-140: a broken install (core.license unimportable) must fail
        CLOSED — never silently unlock a Premium feature. This used to be a
        "dev mode" fallback that allowed it through; that was itself a
        licence bypass."""
        with patch.dict("sys.modules", {"aitao.core.license": None}):
            import importlib
            import aitao.mcp_server.tools._premium as mod
            importlib.reload(mod)
            with pytest.raises(PermissionError, match="Premium license"):
                mod.require_premium("test_feature")


# ============================================================================
# Tests: aitao_search
# ============================================================================

class TestAitaoSearch:
    """Tests for the aitao_search MCP tool."""

    def _get_tool(self):
        """Import and return the registered aitao_search tool function."""
        from fastmcp import FastMCP
        from aitao.mcp_server.tools.search import register_search
        mcp = FastMCP(name="test")
        register_search(mcp)
        return _get_tool_fn(mcp, "aitao_search")

    def test_search_returns_results(self, mock_search_engine):
        _make_state(search_engine=mock_search_engine)
        tool = self._get_tool()
        result = _run(tool(query="test query", limit=10))

        assert result["total"] == 2
        assert result["query"] == "test query"
        assert result["mode"] == "hybrid"
        assert result["results"][0]["title"] == "Test Document"
        assert result["results"][0]["score"] == 0.95
        mock_search_engine.search.assert_awaited_once()

    def test_search_clamps_limit(self, mock_search_engine):
        _make_state(search_engine=mock_search_engine)
        tool = self._get_tool()

        # Test upper clamp
        _run(tool(query="test", limit=100))
        call_args = mock_search_engine.search.call_args
        assert call_args.kwargs["limit"] == 50

    def test_search_invalid_mode_defaults_to_hybrid(self, mock_search_engine):
        _make_state(search_engine=mock_search_engine)
        tool = self._get_tool()
        result = _run(tool(query="test", mode="invalid"))
        assert result["mode"] == "hybrid"

    def test_search_engine_unavailable(self):
        _make_state(search_engine=None)
        tool = self._get_tool()
        result = _run(tool(query="test"))
        assert result["total"] == 0
        assert "error" in result
        assert "not available" in result["error"]

    def test_search_with_category_filter(self, mock_search_engine):
        _make_state(search_engine=mock_search_engine)
        tool = self._get_tool()
        _run(tool(query="test", category="contracts"))
        call_args = mock_search_engine.search.call_args
        assert call_args.kwargs["filters"] == {"category": "contracts"}

    def test_search_falls_back_to_sync(self, mock_search_engine):
        mock_search_engine.search = AsyncMock(side_effect=Exception("async fail"))
        mock_search_engine.search_sync = MagicMock(return_value=[
            {"id": "sync-1", "title": "sync result", "category": "", "path": ""}
        ])
        _make_state(search_engine=mock_search_engine)
        tool = self._get_tool()
        result = _run(tool(query="fallback test"))
        assert result["total"] == 1
        mock_search_engine.search_sync.assert_called_once()

    def test_normalise_results_dict_input(self, mock_search_engine):
        mock_search_engine.search = AsyncMock(return_value={
            "hits": [{"id": "h1", "title": "Hit 1"}]
        })
        _make_state(search_engine=mock_search_engine)
        tool = self._get_tool()
        result = _run(tool(query="dict test"))
        assert result["total"] == 1
        assert result["results"][0]["id"] == "h1"

    def test_normalise_results_hybrid_response_object(self, mock_search_engine):
        """Regression: engine returns HybridSearchResponse Pydantic object, not a dict/list."""
        from aitao.search.search_models import HybridSearchResponse, SearchResult
        sr = SearchResult(
            id="sr-1",
            path="/docs/report.pdf",
            title="Annual Report",
            content="Revenue grew 12% in Q4.",
            score=0.92,
        )
        response = HybridSearchResponse(
            query="revenue",
            results=[sr],
            total=1,
        )
        mock_search_engine.search = AsyncMock(return_value=response)
        _make_state(search_engine=mock_search_engine)
        tool = self._get_tool()
        result = _run(tool(query="revenue"))
        assert result["total"] == 1
        assert result["results"][0]["id"] == "sr-1"
        assert result["results"][0]["title"] == "Annual Report"
        assert result["results"][0]["score"] == 0.92
        assert result["results"][0]["path"] == "/docs/report.pdf"


# ============================================================================
# Tests: aitao_ingest
# ============================================================================

class TestAitaoIngest:
    """Tests for the aitao_ingest MCP tool."""

    def _get_tool(self):
        from fastmcp import FastMCP
        from aitao.mcp_server.tools.ingest import register_ingest
        mcp = FastMCP(name="test")
        register_ingest(mcp)
        return _get_tool_fn(mcp, "aitao_ingest")

    def test_ingest_file_success(self, mock_task_queue, tmp_path):
        test_file = tmp_path / "document.txt"
        test_file.write_text("Hello world")
        _make_state(task_queue=mock_task_queue)
        tool = self._get_tool()
        result = _run(tool(path=str(test_file)))

        assert result["success"] is True
        assert result["queued"] == 1
        assert len(result["task_ids"]) == 1
        assert result["task_ids"][0]["status"] == "queued"

    def test_ingest_path_not_found(self, mock_task_queue):
        _make_state(task_queue=mock_task_queue)
        tool = self._get_tool()
        result = _run(tool(path="/nonexistent/file.txt"))
        assert result["success"] is False
        assert "not found" in result["error"].lower()

    def test_ingest_queue_unavailable(self, tmp_path):
        test_file = tmp_path / "doc.txt"
        test_file.write_text("content")
        _make_state(task_queue=None)
        tool = self._get_tool()
        result = _run(tool(path=str(test_file)))
        assert result["success"] is False
        assert "not available" in result["error"]

    def test_ingest_directory_non_recursive(self, mock_task_queue, tmp_path):
        (tmp_path / "file1.txt").write_text("a")
        (tmp_path / "file2.pdf").write_text("b")
        (tmp_path / "image.jpg").write_text("c")  # Not in allowed extensions
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "nested.md").write_text("d")

        _make_state(task_queue=mock_task_queue)
        tool = self._get_tool()
        result = _run(tool(path=str(tmp_path), recursive=False))

        # Only top-level txt + pdf should be queued (not .jpg, not nested)
        assert result["queued"] == 2

    def test_ingest_directory_recursive(self, mock_task_queue, tmp_path):
        (tmp_path / "file1.txt").write_text("a")
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "nested.md").write_text("d")

        _make_state(task_queue=mock_task_queue)
        tool = self._get_tool()
        result = _run(tool(path=str(tmp_path), recursive=True))

        assert result["queued"] == 2

    def test_ingest_empty_directory(self, mock_task_queue, tmp_path):
        _make_state(task_queue=mock_task_queue)
        tool = self._get_tool()
        result = _run(tool(path=str(tmp_path)))
        assert result["success"] is False
        assert "no indexable files" in result["error"].lower()

    def test_ingest_invalid_priority_defaults(self, mock_task_queue, tmp_path):
        test_file = tmp_path / "doc.txt"
        test_file.write_text("test")
        _make_state(task_queue=mock_task_queue)
        tool = self._get_tool()
        result = _run(tool(path=str(test_file), priority="urgent"))
        # Should still succeed (defaults to "normal")
        assert result["success"] is True


# ============================================================================
# Tests: aitao_stats
# ============================================================================

class TestAitaoStats:
    """Tests for the aitao_stats MCP tool."""

    def _get_tool(self):
        from fastmcp import FastMCP
        from aitao.mcp_server.tools.stats import register_stats
        mcp = FastMCP(name="test")
        register_stats(mcp)
        return _get_tool_fn(mcp, "aitao_stats")

    def test_stats_full_success(self):
        """Stats returns meilisearch + vector_store data.

        ÉPIC-31 (US-113): ``vector_store`` now reads the Meilisearch excerpt/
        chunk index's stats via a RAW ``meilisearch.Client`` (LanceDB is gone
        from the live path) — both the "meilisearch" package import and
        ``core.config.get_config`` are mocked so this unit test never reaches
        the real, locally-running Meilisearch server.
        """
        _make_state(config=MagicMock())

        # Mock meilisearch import inside the tool
        mock_ms = MagicMock()
        mock_ms.get_stats.return_value = {
            "total_documents": 1234,
            "is_indexing": False,
            "index_name": "aitao-docs",
            "last_update": "2026-03-24T10:00:00Z",
        }

        mock_chunk_index = MagicMock()
        mock_chunk_index.get_stats.return_value = {"numberOfDocuments": 5678}
        mock_raw_client = MagicMock()
        mock_raw_client.index.return_value = mock_chunk_index
        mock_meilisearch_pkg = MagicMock()
        mock_meilisearch_pkg.Client.return_value = mock_raw_client

        fake_config = MagicMock()
        fake_config.search.meilisearch.url = "http://localhost:7700"
        fake_config.search.meilisearch.api_key = ""
        fake_config.search.meilisearch.chunks_index = "test_chunks"

        with patch.dict("sys.modules", {
            "aitao.search.meilisearch_client": MagicMock(
                MeilisearchClient=MagicMock(return_value=mock_ms),
            ),
            "meilisearch": mock_meilisearch_pkg,
        }), patch("aitao.core.config.get_config", return_value=fake_config), patch(
            "aitao.core.registry.StatsKeys",
            TOTAL_DOCUMENTS="total_documents",
            IS_INDEXING="is_indexing",
            INDEX_NAME="index_name",
            LAST_UPDATE="last_update",
            create=True,
        ):
            tool = self._get_tool()
            result = _run(tool())

        assert result["status"] == "ready"
        assert result["meilisearch"]["total_documents"] == 1234
        assert result["vector_store"]["vector_count"] == 5678
        mock_raw_client.index.assert_called_once_with("test_chunks")

    def test_stats_degraded_no_meilisearch(self):
        """Stats handles Meilisearch being unavailable."""
        _make_state(config=MagicMock())

        with patch.dict("sys.modules", {
            "aitao.search.meilisearch_client": None,  # Not importable
            "aitao.search.lancedb_client": None,
        }):
            tool = self._get_tool()
            result = _run(tool())

        assert result["status"] == "error"
        assert "error" in result["meilisearch"]


# ============================================================================
# Tests: aitao_ocr (Premium)
# ============================================================================

class TestAitaoOcr:
    """Tests for the aitao_ocr MCP tool."""

    def _get_tool(self):
        from fastmcp import FastMCP
        from aitao.mcp_server.tools.ocr import register_ocr
        mcp = FastMCP(name="test")
        register_ocr(mcp)
        return _get_tool_fn(mcp, "aitao_ocr")

    @patch("aitao.mcp_server.tools.ocr.require_premium")
    def test_ocr_success(self, mock_premium, tmp_path):
        _make_state()
        test_img = tmp_path / "scan.png"
        test_img.write_bytes(b"\x89PNG")

        # Build a mock OCRResult with proper attributes (not a plain dict)
        mock_result = MagicMock()
        mock_result.text = "Extracted text from image"
        mock_result.page_count = 1
        mock_result.lang_detected = "en"

        mock_router = MagicMock()
        mock_router.extract = AsyncMock(return_value=mock_result)

        # Patch OCRRouter in the real module (lazy import inside aitao_ocr function)
        with patch("aitao.ocr.router.OCRRouter", MagicMock(return_value=mock_router)):
            tool = self._get_tool()
            result = _run(tool(file_path=str(test_img)))

        assert result["text"] == "Extracted text from image"
        assert result["page_count"] == 1
        mock_premium.assert_called_once_with("ocr")

    @patch("aitao.mcp_server.tools.ocr.require_premium")
    def test_ocr_file_not_found(self, mock_premium):
        _make_state()
        tool = self._get_tool()
        with pytest.raises(FileNotFoundError):
            _run(tool(file_path="/nonexistent/image.png"))

    @patch("aitao.mcp_server.tools.ocr.require_premium")
    def test_ocr_unsupported_extension(self, mock_premium, tmp_path):
        _make_state()
        bad_file = tmp_path / "data.json"
        bad_file.write_text("{}")
        tool = self._get_tool()
        with pytest.raises(ValueError, match="Unsupported"):
            _run(tool(file_path=str(bad_file)))

    def test_ocr_requires_premium(self, tmp_path):
        _make_state()
        test_img = tmp_path / "scan.png"
        test_img.write_bytes(b"\x89PNG")
        with patch(
            "aitao.mcp_server.tools.ocr.require_premium",
            side_effect=PermissionError("Premium required"),
        ):
            tool = self._get_tool()
            with pytest.raises(PermissionError):
                _run(tool(file_path=str(test_img)))


# ============================================================================
# Tests: aitao_extract (Premium)
# ============================================================================

class TestAitaoExtract:
    """Tests for the aitao_extract MCP tool."""

    def _get_tool(self):
        from fastmcp import FastMCP
        from aitao.mcp_server.tools.extract import register_extract
        mcp = FastMCP(name="test")
        register_extract(mcp)
        return _get_tool_fn(mcp, "aitao_extract")

    @patch("aitao.mcp_server.tools.extract.require_premium")
    def test_extract_summary(self, mock_premium):
        _make_state()
        mock_extractor = MagicMock()
        mock_extractor.extract = AsyncMock(return_value="A concise summary.")

        with patch.dict("sys.modules", {
            "processing.extractor": MagicMock(
                TextExtractor=MagicMock(return_value=mock_extractor),
            ),
        }):
            tool = self._get_tool()
            result = _run(tool(text="Long document content here...", extract_type="summary"))

        assert result["result"] == "A concise summary."
        assert result["extract_type"] == "summary"
        mock_premium.assert_called_once_with("extraction")

    @patch("aitao.mcp_server.tools.extract.require_premium")
    def test_extract_entities(self, mock_premium):
        _make_state()
        mock_extractor = MagicMock()
        mock_extractor.extract = AsyncMock(return_value=[
            {"entity": "Paris", "type": "location"},
            {"entity": "2026", "type": "date"},
        ])

        with patch.dict("sys.modules", {
            "processing.extractor": MagicMock(
                TextExtractor=MagicMock(return_value=mock_extractor),
            ),
        }):
            tool = self._get_tool()
            result = _run(tool(text="Meeting in Paris in 2026", extract_type="entities"))

        assert isinstance(result["result"], list)
        assert len(result["result"]) == 2

    @patch("aitao.mcp_server.tools.extract.require_premium")
    def test_extract_empty_text_raises(self, mock_premium):
        _make_state()
        tool = self._get_tool()
        with pytest.raises(ValueError, match="empty"):
            _run(tool(text="   ", extract_type="summary"))

    @patch("aitao.mcp_server.tools.extract.require_premium")
    def test_extract_text_too_long(self, mock_premium):
        _make_state()
        tool = self._get_tool()
        with pytest.raises(ValueError, match="20 000"):
            _run(tool(text="x" * 20_001, extract_type="summary"))

    @patch("aitao.mcp_server.tools.extract.require_premium")
    def test_extract_invalid_type_raises(self, mock_premium):
        _make_state()
        tool = self._get_tool()
        with pytest.raises(ValueError, match="Unknown extract_type"):
            _run(tool(text="Some text", extract_type="feelings"))

    def test_extract_requires_premium(self):
        _make_state()
        with patch(
            "aitao.mcp_server.tools.extract.require_premium",
            side_effect=PermissionError("Premium required"),
        ):
            tool = self._get_tool()
            with pytest.raises(PermissionError):
                _run(tool(text="Some text", extract_type="summary"))
