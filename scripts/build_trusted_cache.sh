#!/usr/bin/env bash
set -euo pipefail

# usage: build_trusted_cache.sh [--stage vendor|root|all] [--miner]
#
# --stage exists for the Dockerfile, which builds the pinned vendor checkouts in a layer that a
# verifier-source edit must not invalidate, and the root Lean targets in a later one. Running the
# halves from here rather than inlining them is what keeps a locally built verifier and the image
# the same build; when they were two recipes, this one grew TestFixtures.Counterexample and the
# Dockerfile did not.
#
# --miner skips landrun and the seccomp launcher. Those exist to protect a validator from hostile
# proofs, and a host verifying its own proof under the development sandbox runs neither — dropping
# them takes golang-go and build-essential off the list of packages a miner has to install.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

stage=all
miner=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --stage) stage="${2:?--stage needs a value}"; shift 2 ;;
    --stage=*) stage="${1#*=}"; shift ;;
    --miner) miner=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
case "$stage" in
  vendor|root|all) ;;
  *) echo "unknown stage: $stage (expected vendor, root or all)" >&2; exit 2 ;;
esac

export ELAN_HOME="${ELAN_HOME:-$ROOT/.elan}"
export PATH="$ELAN_HOME/bin:$PATH"
export LEAN_NUM_THREADS="${FC_LEAN_BUILD_THREADS:-${LEAN_NUM_THREADS:-2}}"

sandbox_tools=1
if [[ "$miner" = 1 || "$(uname -s)" != Linux ]]; then
  sandbox_tools=0
fi

substep_total=0
if [[ "$stage" = vendor || "$stage" = all ]]; then
  substep_total=$((substep_total + 4))
  if [[ "$sandbox_tools" = 1 ]]; then substep_total=$((substep_total + 1)); fi
  if [[ "${ENABLE_NANODA:-0}" = 1 ]]; then substep_total=$((substep_total + 1)); fi
fi
if [[ "$stage" = root || "$stage" = all ]]; then
  substep_total=$((substep_total + 2))
  if [[ "$sandbox_tools" = 1 ]]; then substep_total=$((substep_total + 1)); fi
fi

substep_number=0
substep() {
  substep_number=$((substep_number + 1))
  printf 'build_trusted_cache.sh: step %d/%d -- %s\n' "$substep_number" "$substep_total" "$1"
}

if [[ "$stage" = vendor || "$stage" = all ]]; then
  cd "$ROOT/vendor/formal-conjectures"
  # `FormalConjectures` and `FormalConjecturesAnswerPostpone` emit the same module
  # names into the same Lake build directory. A postpone-mode build can therefore
  # leave source theorem types that disagree with the default-mode catalog. Clean
  # legacy/unmarked caches once, then keep the verifier cache exclusively in the
  # default `always_true` mode.
  answer_mode_stamp=".lake/build/.formal-conjectures-always-true-v1"
  if [[ ! -f "$answer_mode_stamp" ]]; then
    lake clean
  fi
  substep "fetch and unpack the Mathlib build cache -- about 5 GB, and the unpacking is silent"
  lake exe cache get

  substep "build Formal Conjectures -- around 8000 modules"
  lake build FormalConjectures
  touch "$answer_mode_stamp"

  substep "build lean4export"
  cd "$ROOT/vendor/lean4export"
  lake build lean4export

  substep "build the comparator -- installs a second Lean toolchain, about 3 GB"
  cd "$ROOT/vendor/comparator"
  lake build comparator

  if [[ "$sandbox_tools" = 1 ]]; then
    substep "build landrun"
    mkdir -p "$ROOT/vendor/landrun/bin"
    cd "$ROOT/vendor/landrun"
    go build -trimpath -o bin/landrun ./cmd/landrun
  fi

  if [[ "${ENABLE_NANODA:-0}" = 1 ]]; then
    substep "build nanoda"
    cd "$ROOT/vendor/nanoda"
    cargo build --release --locked
  fi
fi

if [[ "$stage" = root || "$stage" = all ]]; then
  cd "$ROOT"
  # Reuse Formal Conjectures' package checkouts rather than letting Lake materialise a second set:
  # Mathlib alone is 6.5 GB, and both manifests already fix it at the same revision. An entry that
  # exists as a real directory is left alone, so a checkout that predates this keeps working.
  mkdir -p .lake/packages
  for package in vendor/formal-conjectures/.lake/packages/*; do
    [[ -e "$package" ]] || continue
    link=".lake/packages/$(basename "$package")"
    # -L as well as -e, so a link left dangling by a moved checkout is not reported as "File exists".
    if [[ ! -e "$link" && ! -L "$link" ]]; then
      ln -s "../../$package" "$link"
    fi
  done
  substep "fetch and unpack the root build cache"
  lake exe cache get

  substep "build the verifier's own Lean targets"
  # TestFixtures.Counterexample is named explicitly: the TestFixtures lean_lib has no globs, so
  # building it compiles only the root module and its imports, and TestFixtures.lean does not import
  # Counterexample. Without it the external counterexample task fixture cannot build its challenge,
  # which tests/test_integration.py already depends on.
  lake build VerifierLean TaskSupport TestFixtures TestFixtures.Counterexample \
    catalog_extractor task_inspector

  if [[ "$sandbox_tools" = 1 ]]; then
    substep "build the seccomp launcher"
    mkdir -p "$ROOT/.tools"
    cc -O2 -Wall -Wextra -Werror \
      -o "$ROOT/.tools/seccomp-launcher" "$ROOT/security/seccomp-launcher.c"
  fi
fi
