#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ELAN_HOME="${ELAN_HOME:-$ROOT/.elan}"
export PATH="$ELAN_HOME/bin:$PATH"
export LEAN_NUM_THREADS="${FC_LEAN_BUILD_THREADS:-2}"

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
lake exe cache get
lake build FormalConjectures
touch "$answer_mode_stamp"

cd "$ROOT"
lake update
lake exe cache get
# TestFixtures.Counterexample is named explicitly: the TestFixtures lean_lib has no globs, so
# building it compiles only the root module and its imports, and TestFixtures.lean does not import
# Counterexample. Without it the external counterexample task fixture cannot build its challenge,
# which tests/test_integration.py already depends on.
lake build VerifierLean TaskSupport TestFixtures TestFixtures.Counterexample \
  catalog_extractor task_inspector

cd "$ROOT/vendor/lean4export"
lake build lean4export

cd "$ROOT/vendor/comparator"
lake build comparator

if [[ "$(uname -s)" = Linux ]]; then
  mkdir -p "$ROOT/vendor/landrun/bin"
  cd "$ROOT/vendor/landrun"
  go build -trimpath -o bin/landrun ./cmd/landrun
  mkdir -p "$ROOT/.tools"
  cc -O2 -Wall -Wextra -Werror \
    -o "$ROOT/.tools/seccomp-launcher" "$ROOT/security/seccomp-launcher.c"
fi

if [[ "${ENABLE_NANODA:-0}" = 1 ]]; then
  cd "$ROOT/vendor/nanoda"
  cargo build --release --locked
fi
