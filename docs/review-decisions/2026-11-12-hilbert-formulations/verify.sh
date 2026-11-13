#!/usr/bin/env bash
set -euo pipefail
packet_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
verifier=verify.py
if [[ "${1:-}" == "--clean" && "$#" == 1 ]]; then
  verifier=verify_clean.py
elif [[ "$#" != 0 ]]; then
  echo "Usage: $0 [--clean]" >&2
  exit 2
fi
docker run --rm --user 0:0 --network none \
  --volume "$packet_dir:/packet" --entrypoint python3 \
  sha256:4da29ed9e4448435a979baeda82e934b091abde3754bd9976d8b1df93708fa90 \
  "/packet/$verifier"
