# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# tests/golden/phase.py — single source of truth for the ÉPIC-30 delivery phase.
#
# ETUDE-FIABILITE.md §7 plans four phases (0: golden bench, 1: dossier courant
# US-102, 2: gate amont US-103, 3: lecteur de reponse US-104). A scenario whose
# `phase` exceeds DELIVERED_PHASE encodes a TARGET behaviour that does not exist
# yet (I-11, I-05, I-10) and is run as xfail(strict=False): it stays visible
# (and flips to a real pass the day its phase ships) without failing the suite.
# Bump this constant — and only this constant — when a phase is delivered.

DELIVERED_PHASE = 3
