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

step_number=0
step() {
  step_number=$((step_number + 1))
  printf 'bootstrap.sh: step %d/7 -- %s\n' "$step_number" "$1"
}

step "check this host can finish the build"
python3 scripts/check_prerequisites.py "${profile[@]}"

step "clone the pinned dependencies -- quiet, and the largest download of the run"
./scripts/pin_dependencies.sh

step "install Elan and the pinned Lean toolchain -- another gigabyte, also quiet"
./scripts/install_elan.sh

export ELAN_HOME="${ELAN_HOME:-$ROOT/.elan}"
export PATH="$ELAN_HOME/bin:$PATH"

step "create the virtualenv"
python3 -m venv "$ROOT/.venv"

step "install the verifier package"
if [[ "$miner" = 1 ]]; then
  "$ROOT/.venv/bin/pip" install -e .
else
  "$ROOT/.venv/bin/pip" install \
    --constraint "$ROOT/requirements-service.lock" \
    -e '.[dev,service,subnet,db]'
fi
"$ROOT/.venv/bin/pip" check

step "build the trusted Lean cache -- most of the wall clock is here"
./scripts/build_trusted_cache.sh "${profile[@]}"

step "readiness check"
doctor=()
if [[ "$miner" = 1 ]]; then
  # Without this, doctor judges the host against production isolation it deliberately did not
  # build and reports `ready: false` over tools it has no use for.
  doctor=(--allow-insecure-development)
fi
"$ROOT/.venv/bin/python" -m verifier doctor "${doctor[@]}"
