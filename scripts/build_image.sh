#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
docker build --build-arg "ENABLE_NANODA=${ENABLE_NANODA:-0}" \
  --build-arg "LEAN_BUILD_THREADS=${FC_LEAN_BUILD_THREADS:-3}" \
  --tag formal-conjectures-verifier:local "$ROOT"
