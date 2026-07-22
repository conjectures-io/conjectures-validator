#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export ELAN_HOME="${ELAN_HOME:-$ROOT/.elan}"
export PATH="$ELAN_HOME/bin:$PATH"
export LEAN_NUM_THREADS="${FC_LEAN_BUILD_THREADS:-2}"

cd "$ROOT/vendor/formal-conjectures"
lake exe cache get
lake build FormalConjecturesAnswerPostpone extract_names

cd "$ROOT"
lake update
lake exe cache get
lake build VerifierLean TaskSupport TestFixtures catalog_extractor task_inspector

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
