#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export ELAN_HOME="${ELAN_HOME:-$ROOT/.elan}"
export PATH="$ROOT/.venv/bin:$ELAN_HOME/bin:$PATH"
"$ROOT/scripts/generate_catalog.sh"
FC_RUN_INTEGRATION=1 "$ROOT/.venv/bin/python" -m pytest -m integration
