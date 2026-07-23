#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export ELAN_HOME="${ELAN_HOME:-$ROOT/.elan}"
export PATH="$ROOT/.venv/bin:$ELAN_HOME/bin:$PATH"
"$ROOT/.venv/bin/python" -m verifier task generate --catalog data/catalog.json \
  --theorem Arxiv.id2303_01089.conjecture_1_3 --mode formalized \
  --output tasks/furstenberg-formalized
