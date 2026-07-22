#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

pin_field() {
  python3 - "$1" "$2" <<'PY'
import json
import sys

section, field = sys.argv[1:]
with open("pins.lock.json", encoding="utf-8") as handle:
    print(json.load(handle)[section][field])
PY
}

pin_repo() {
  local name="$1"
  local url="$2"
  local commit="$3"
  local destination="vendor/$name"
  if [[ ! -d "$destination/.git" ]]; then
    git clone --filter=blob:none --no-checkout "$url" "$destination"
  fi
  git -C "$destination" remote set-url origin "$url"
  git -C "$destination" fetch --no-tags origin "$commit"
  git -C "$destination" checkout --detach "$commit"
  test "$(git -C "$destination" rev-parse HEAD)" = "$commit"
  test -z "$(git -C "$destination" status --porcelain --untracked-files=no)"
}

for dependency in formal_conjectures comparator lean4export landrun nanoda; do
  directory="${dependency//_/-}"
  pin_repo "$directory" "$(pin_field "$dependency" repository)" "$(pin_field "$dependency" commit)"
done

python3 <<'PY'
import json
from pathlib import Path

pins = json.loads(Path("pins.lock.json").read_text(encoding="utf-8"))
manifest = json.loads(Path("vendor/formal-conjectures/lake-manifest.json").read_text(encoding="utf-8"))
mathlib = next(package for package in manifest["packages"] if package["name"] == "mathlib")
toolchain = Path("vendor/formal-conjectures/lean-toolchain").read_text(encoding="utf-8").strip()
assert mathlib["rev"] == pins["mathlib"]["commit"], "Formal Conjectures Mathlib pin drifted"
assert toolchain == pins["lean"]["toolchain"], "Formal Conjectures Lean toolchain pin drifted"
PY
