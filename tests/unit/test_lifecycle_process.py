# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
Unit tests for AiTao service process management (port-authoritative).

Covers the v3.1 fixes to the start/stop lifecycle that prevent orphaned API
servers:
- PID files live in a stable, config-derived directory (never $TMPDIR)
- stop reaps whatever LISTENS on the API port, not just the recorded PID
- start confirms readiness via /api/health instead of a blind sleep

These are pure unit tests — every OS / network side effect is mocked.
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

# Add src directory to path
src_path = Path(__file__).parent.parent.parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))

from aitao.cli.commands import _lifecycle_services as svc  # noqa: E402
from aitao.cli.commands import _lifecycle_utils as utils  # noqa: E402


class TestStablePidLocation:
    """Root-cause guard: PID files must not depend on $TMPDIR."""

    def test_api_pid_file_not_in_tmpdir(self):
        tmp = Path(tempfile.gettempdir()).resolve()
        resolved = utils.API_PID_FILE.resolve()
        assert tmp not in resolved.parents
        assert utils.API_PID_FILE.name == "api.pid"

    def test_api_and_worker_share_stable_dir(self):
        # Aligns with the worker daemon's own <storage_root>/worker.pid
        assert utils.API_PID_FILE.parent == utils.WORKER_PID_FILE.parent
        assert utils.WORKER_PID_FILE.name == "worker.pid"


class TestPidHelpers:
    def test_read_pid_file_valid(self, tmp_path):
        f = tmp_path / "x.pid"
        f.write_text("4242")
        assert utils._read_pid_file(f) == 4242

    def test_read_pid_file_missing(self, tmp_path):
        assert utils._read_pid_file(tmp_path / "nope.pid") is None

    def test_read_pid_file_malformed(self, tmp_path):
        f = tmp_path / "x.pid"
        f.write_text("not-a-pid")
        assert utils._read_pid_file(f) is None

    def test_pid_alive_true(self):
        with patch("aitao.cli.commands._lifecycle_utils.os.kill", return_value=None):
            assert utils._pid_alive(123) is True

    def test_pid_alive_dead(self):
        with patch("aitao.cli.commands._lifecycle_utils.os.kill", side_effect=ProcessLookupError):
            assert utils._pid_alive(123) is False

    def test_pid_alive_none(self):
        assert utils._pid_alive(None) is False


class TestPidsOnPort:
    def test_uses_psutil_listen_match(self):
        conn = MagicMock()
        conn.laddr = MagicMock(port=8200)
        conn.status = "LISTEN"
        conn.pid = 999
        fake_psutil = MagicMock()
        fake_psutil.CONN_LISTEN = "LISTEN"
        fake_psutil.net_connections.return_value = [conn]
        with patch.dict("sys.modules", {"psutil": fake_psutil}):
            assert utils._pids_on_port(8200) == [999]

    def test_ignores_other_ports(self):
        conn = MagicMock()
        conn.laddr = MagicMock(port=1234)
        conn.status = "LISTEN"
        conn.pid = 999
        fake_psutil = MagicMock()
        fake_psutil.CONN_LISTEN = "LISTEN"
        fake_psutil.net_connections.return_value = [conn]
        with patch.dict("sys.modules", {"psutil": fake_psutil}):
            assert utils._pids_on_port(8200) == []


class TestStopApiServer:
    """The regression that orphaned the user's server."""

    def test_reaps_orphan_listening_on_port(self):
        """Stale/missing PID file must not leave an orphan: stop reaps the
        process that still LISTENS on the port."""
        with patch.object(svc, "_read_pid_file", return_value=None), \
             patch.object(svc, "_pid_alive", return_value=False), \
             patch.object(svc, "_pids_on_port", side_effect=[[5982], []]), \
             patch.object(svc, "_is_api_responding", return_value=False), \
             patch.object(svc, "_terminate_pid", return_value=True) as term, \
             patch.object(svc, "API_PID_FILE") as pidf, \
             patch.object(svc, "_get_api_port", return_value=8200):
            result = svc._stop_api_server()

        assert result is True
        term.assert_called_once_with(5982)
        pidf.unlink.assert_called_once()

    def test_terminates_recorded_pid(self):
        with patch.object(svc, "_read_pid_file", return_value=4242), \
             patch.object(svc, "_pid_alive", return_value=True), \
             patch.object(svc, "_pids_on_port", return_value=[]), \
             patch.object(svc, "_is_api_responding", return_value=False), \
             patch.object(svc, "_terminate_pid", return_value=True) as term, \
             patch.object(svc, "API_PID_FILE"), \
             patch.object(svc, "_get_api_port", return_value=8200):
            assert svc._stop_api_server() is True

        term.assert_any_call(4242)

    def test_returns_false_when_port_still_busy(self):
        with patch.object(svc, "_read_pid_file", return_value=None), \
             patch.object(svc, "_pid_alive", return_value=False), \
             patch.object(svc, "_pids_on_port", return_value=[7777]), \
             patch.object(svc, "_is_api_responding", return_value=False), \
             patch.object(svc, "_terminate_pid", return_value=False), \
             patch.object(svc, "API_PID_FILE"), \
             patch.object(svc, "_get_api_port", return_value=8200):
            assert svc._stop_api_server() is False

    def test_noop_when_nothing_running(self):
        with patch.object(svc, "_read_pid_file", return_value=None), \
             patch.object(svc, "_pid_alive", return_value=False), \
             patch.object(svc, "_pids_on_port", return_value=[]), \
             patch.object(svc, "_is_api_responding", return_value=False), \
             patch.object(svc, "_terminate_pid") as term, \
             patch.object(svc, "API_PID_FILE"), \
             patch.object(svc, "_get_api_port", return_value=8200):
            assert svc._stop_api_server() is True

        term.assert_not_called()


class TestStartApiServer:
    def test_short_circuits_when_already_healthy(self):
        """If the API already answers, don't spawn a second uvicorn."""
        with patch.object(svc, "_is_api_responding", return_value=True), \
             patch.object(svc, "_pids_on_port", return_value=[777]), \
             patch.object(svc, "API_PID_FILE") as pidf, \
             patch.object(svc, "_get_api_port", return_value=8200), \
             patch.object(svc, "_get_api_host", return_value="0.0.0.0"), \
             patch("aitao.cli.commands._lifecycle_services.subprocess.Popen") as popen:
            ok, pid = svc._start_api_server()

        assert ok is True
        assert pid == 777
        popen.assert_not_called()
        pidf.write_text.assert_called_once_with("777")

    def test_reports_failure_when_process_dies_early(self):
        """A uvicorn that exits (e.g. address already in use) is a failure,
        not a false-positive success."""
        proc = MagicMock()
        proc.poll.return_value = 1  # already exited
        with patch.object(svc, "_is_api_responding", return_value=False), \
             patch.object(svc, "_pids_on_port", return_value=[]), \
             patch.object(svc, "_get_api_port", return_value=8200), \
             patch.object(svc, "_get_api_host", return_value="0.0.0.0"), \
             patch.object(svc, "API_PID_FILE"), \
             patch("aitao.cli.commands._lifecycle_services.subprocess.Popen", return_value=proc), \
             patch("builtins.open", new_callable=mock_open), \
             patch.object(Path, "mkdir"):
            ok, pid = svc._start_api_server()

        assert ok is False
        assert pid is None
