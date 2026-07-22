#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

./scripts/pin_dependencies.sh

export ELAN_HOME="${ELAN_HOME:-$ROOT/.elan}"
if [[ ! -x "$ELAN_HOME/bin/elan" ]]; then
  mkdir -p "$ROOT/.tools"
  curl -fsSL https://raw.githubusercontent.com/leanprover/elan/464c9d28395000a2a0128e07081e4956d50eced2/elan-init.sh \
    -o "$ROOT/.tools/elan-init.sh"
  sh "$ROOT/.tools/elan-init.sh" -y --default-toolchain leanprover/lean4:v4.27.0
fi
export PATH="$ELAN_HOME/bin:$PATH"

python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install -e '.[dev]'
./scripts/build_trusted_cache.sh
"$ROOT/.venv/bin/python" -m verifier doctor
