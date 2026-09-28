# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only

"""
AiTao plugin packages (US-25).

Drop a self-registering module under a sub-package to add a component without
touching the core:

- ``plugins/llm/``        → LLM backends    (``@register("llm", name)``)
- ``plugins/extractors/`` → file extractors (``@register("extractor", name)``)

OCR providers (``@register("ocr", name)``) used to live under ``plugins/ocr/``
but moved to the separately distributed ``aitao-premium`` package (US-138-1):
this repository is published under AGPL v3 and cannot protect anything, so
the engines that actually read scanned documents live where a licence check
can be real. They still register into the exact same ``("ocr", name)`` keys,
discovered via the ``aitao.plugins`` entry-point group instead of this
built-in package — see ``core.plugin_registry.discover_plugins()``.

The Office-format extractors (DOCXExtractor, PPTXExtractor, XLSXExtractor,
ODFExtractor — ``@register("extractor", "docx"/"pptx"/"xlsx"/"odf")``) used to
live under ``plugins/extractors/office_extractors.py`` for the same reason
they no longer do: this repository cannot protect anything, so the code that
reads a user's Office documents lives in ``aitao-premium`` now (US-138-1).
They still register into the exact same ``("extractor", name)`` keys,
discovered the same entry-point way. What remains under
``plugins/extractors/`` here are the extractors for formats Core actually
indexes for free (PDF, plain text, code, images, EXIF).

``core.plugin_registry.discover_plugins()`` imports everything here at runtime so
the decorators run and the components become available by name.
"""
