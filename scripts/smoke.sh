#!/usr/bin/env bash
# Copyright (C) 2025-2026 Philippe BERTIERI
# SPDX-License-Identifier: AGPL-3.0-only
# smoke.sh — Quick CLI smoke test. Run before any commit that touches src/.
# Exit code 0 = all good. Exit code 1 = something crashed.
#
# Usage: ./scripts/smoke.sh
# Requires: ./aitao.sh start must succeed first (or services already running)

set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AITAO="$SCRIPT_DIR/../aitao.sh"

echo "🔥 AiTao smoke test"
echo

# 1. Start (or verify running)
echo "→ aitao start..."
"$AITAO" start --skip-scan
echo

# 2. Status
echo "→ aitao status..."
"$AITAO" status
echo

# 3. Models status
echo "→ aitao models status..."
"$AITAO" models status
echo

# 4. Health check
echo "→ API health check..."
curl -sf http://localhost:8200/api/health | python3 -c "import sys,json; d=json.load(sys.stdin); print('  health:', d.get('status','?'))"
echo

# 5. Models list (OpenAI endpoint)
echo "→ GET /v1/models..."
curl -sf http://localhost:8200/v1/models | python3 -c "import sys,json; d=json.load(sys.stdin); print('  models:', [m['id'] for m in d.get('data',[])][:3], '...')"
echo

# 6. Search
echo "→ aitao search test..."
"$AITAO" search "test" 2>&1 | head -5
echo

echo "✅ Smoke test passed"
