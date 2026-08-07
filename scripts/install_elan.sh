#!/usr/bin/env bash
set -euo pipefail

# Installs the Elan release recorded in pins.lock.json, accepted only against the sha256 recorded
# there. Shared by scripts/bootstrap.sh and the Dockerfile: two copies drifted once already, with
# the version hardcoded in one and read from the pin file in the other.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export ELAN_HOME="${ELAN_HOME:-$ROOT/.elan}"
if [[ -x "$ELAN_HOME/bin/elan" ]]; then
  exit 0
fi

case "$(uname -m)-$(uname -s)" in
  arm64-Darwin) platform="aarch64-apple-darwin" ;;
  x86_64-Darwin) platform="x86_64-apple-darwin" ;;
  aarch64-Linux|arm64-Linux) platform="aarch64-unknown-linux-gnu" ;;
  x86_64-Linux) platform="x86_64-unknown-linux-gnu" ;;
  *) echo "unsupported Elan platform: $(uname -m)-$(uname -s)" >&2; exit 2 ;;
esac

pins="$(python3 - "$platform" <<'PY'
import json
import sys
from pathlib import Path

platform = sys.argv[1]
pins = json.loads(Path("pins.lock.json").read_text(encoding="utf-8"))
digest = pins["elan"]["assets"].get(platform)
if digest is None:
    raise SystemExit(f"pins.lock.json records no Elan asset for {platform}")
print(pins["elan"]["version"], digest, pins["lean"]["toolchain"])
PY
)"
read -r version digest toolchain <<<"$pins"

mkdir -p "$ROOT/.tools"
archive="$ROOT/.tools/elan-$platform.tar.gz"
curl -fsSL \
  "https://github.com/leanprover/elan/releases/download/v$version/elan-$platform.tar.gz" \
  -o "$archive"
# Not `sha256sum --check`, which says only FAILED. What a mismatch here means is that the release
# GitHub served is not the one that was pinned, and both digests belong in the message.
python3 - "$archive" "$digest" <<'PY'
import hashlib
import sys
from pathlib import Path

path, expected = sys.argv[1], sys.argv[2]
actual = hashlib.sha256(Path(path).read_bytes()).hexdigest()
if actual != expected:
    raise SystemExit(f"Elan archive hash mismatch: expected {expected}, got {actual}")
PY

tar -xzf "$archive" -C "$ROOT/.tools" elan-init
"$ROOT/.tools/elan-init" -y --default-toolchain "$toolchain"
rm -f "$archive" "$ROOT/.tools/elan-init"
