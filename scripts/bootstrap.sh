#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

./scripts/pin_dependencies.sh

export ELAN_HOME="${ELAN_HOME:-$ROOT/.elan}"
if [[ ! -x "$ELAN_HOME/bin/elan" ]]; then
  mkdir -p "$ROOT/.tools"
  case "$(uname -m)-$(uname -s)" in
    arm64-Darwin) platform="aarch64-apple-darwin" ;;
    x86_64-Darwin) platform="x86_64-apple-darwin" ;;
    aarch64-Linux|arm64-Linux) platform="aarch64-unknown-linux-gnu" ;;
    x86_64-Linux) platform="x86_64-unknown-linux-gnu" ;;
    *) echo "unsupported Elan platform: $(uname -m)-$(uname -s)" >&2; exit 2 ;;
  esac
  archive="$ROOT/.tools/elan-$platform.tar.gz"
  curl -fsSL "https://github.com/leanprover/elan/releases/download/v4.2.3/elan-$platform.tar.gz" \
    -o "$archive"
  python3 - "$archive" "$platform" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
platform = sys.argv[2]
expected = json.loads(Path("pins.lock.json").read_text(encoding="utf-8"))["elan"]["assets"][platform]
actual = hashlib.sha256(path.read_bytes()).hexdigest()
if actual != expected:
    raise SystemExit(f"Elan archive hash mismatch: expected {expected}, got {actual}")
PY
  tar -xzf "$archive" -C "$ROOT/.tools" elan-init
  "$ROOT/.tools/elan-init" -y --default-toolchain leanprover/lean4:v4.27.0
fi
export PATH="$ELAN_HOME/bin:$PATH"

python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install -e '.[dev]'
./scripts/build_trusted_cache.sh
"$ROOT/.venv/bin/python" -m verifier doctor
