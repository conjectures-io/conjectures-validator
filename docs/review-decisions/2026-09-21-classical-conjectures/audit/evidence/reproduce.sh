#!/usr/bin/env bash
set -euo pipefail
# Set FC_SNAPSHOT to a checkout of the exact audited upstream commit.
audit_dir=$(cd "$(dirname "$0")/.." && pwd)
snapshot_dir=${FC_SNAPSHOT:-/tmp/fc-current}
verifier_image=sha256:b0b4929274e9f62d92749c814215ef21e546c9f882f20ba5c41d704996f5e5b3
python3 "$audit_dir/evidence/make_inspector.py"
docker run --rm --user 0 --network none --read-only --tmpfs /tmp \
  -v "$snapshot_dir:/snapshot:ro" -v "$audit_dir:/audit" \
  --entrypoint sh "$verifier_image" -c '
    set -eu
    python3 /audit/evidence/compare_sources.py
    python3 /audit/evidence/compare_definitions.py
    lake env python3 /audit/evidence/build_overlay.py
    lake env sh -c '\''
      export LEAN_PATH=/audit/evidence/build:$LEAN_PATH
      lean /audit/evidence/Inspect.lean > /audit/evidence/inspect.log 2>&1
      lean /audit/evidence/Bridges.lean > /audit/evidence/bridges.log 2>&1
    '\''
  '
python3 "$audit_dir/evidence/write_report.py"
