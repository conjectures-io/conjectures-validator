#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export ELAN_HOME="${ELAN_HOME:-$ROOT/.elan}"
export PATH="$ROOT/.venv/bin:$ELAN_HOME/bin:$PATH"
"$ROOT/.venv/bin/python" -m verifier catalog build \
  --repo-dir vendor/formal-conjectures --output data/catalog.json
