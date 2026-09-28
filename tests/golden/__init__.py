# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# tests/golden — shared helpers for the golden bench suites (US-89, US-101).
#
# Not itself a test module: hosts the scenario YAML loader (scenario_loader.py),
# the shared isolated-store corpus indexer (corpus_fixture.py), and the single
# source of truth for which ÉPIC-30 phase is actually delivered (phase.py),
# imported by both the deterministic runner (tests/integration/
# test_golden_conversations.py) and the live variant (tests/e2e/test_golden_live.py).
