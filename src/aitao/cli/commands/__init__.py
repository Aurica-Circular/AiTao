# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
CLI Commands module.

Each command is a separate module for maintainability and testability.
"""

from aitao.cli.commands import status
from aitao.cli.commands import meilisearch
from aitao.cli.commands import database
from aitao.cli.commands import config
from aitao.cli.commands import lifecycle

__all__ = ["status", "meilisearch", "database", "config", "lifecycle"]
