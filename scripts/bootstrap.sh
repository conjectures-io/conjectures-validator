#!/usr/bin/env bash
set -euo pipefail

# usage: bootstrap.sh [--miner]
#
# --miner builds only what verifying a proof locally needs. The verifier package declares no
# dependencies, so the service, subnet and database extras are the validator's business; and the
# production sandbox is what protects a validator from hostile proofs rather than a miner from
# their own. Everything that decides a verdict is identical either way, which is the point:
# assert_dependency_pins refuses to produce one from a drifted environment on both paths.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

miner=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --miner) miner=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

profile=()
if [[ "$miner" = 1 ]]; then
  profile=(--miner)
fi

# First, and from here rather than from a README: a prerequisite check nobody runs is a
# prerequisite check that rots.
python3 scripts/check_prerequisites.py "${profile[@]}"

./scripts/pin_dependencies.sh
./scripts/install_elan.sh

export ELAN_HOME="${ELAN_HOME:-$ROOT/.elan}"
export PATH="$ELAN_HOME/bin:$PATH"

python3 -m venv "$ROOT/.venv"
if [[ "$miner" = 1 ]]; then
  "$ROOT/.venv/bin/pip" install -e .
else
  "$ROOT/.venv/bin/pip" install \
    --constraint "$ROOT/requirements-service.lock" \
    -e '.[dev,service,subnet,db]'
fi
"$ROOT/.venv/bin/pip" check

./scripts/build_trusted_cache.sh "${profile[@]}"

doctor=()
if [[ "$miner" = 1 ]]; then
  # Without this, doctor judges the host against production isolation it deliberately did not
  # build and reports `ready: false` over tools it has no use for.
  doctor=(--allow-insecure-development)
fi
"$ROOT/.venv/bin/python" -m verifier doctor "${doctor[@]}"
