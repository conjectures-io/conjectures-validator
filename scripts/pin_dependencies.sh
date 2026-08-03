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
  local destination="$4"
  if ! git -C "$destination" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    if [[ -e "$destination" ]]; then
      echo "$destination exists but is not a Git checkout for $name" >&2
      return 2
    fi
    git clone --filter=blob:none --no-checkout "$url" "$destination"
  fi
  git -C "$destination" remote set-url origin "$url"
  git -C "$destination" fetch --no-tags origin "$commit"
  git -C "$destination" checkout --detach "$commit"
  test "$(git -C "$destination" rev-parse HEAD)" = "$commit"
  test -z "$(git -C "$destination" status --porcelain --untracked-files=all)"
}

for dependency in formal_conjectures comparator lean4export landrun nanoda; do
  directory="${dependency//_/-}"
  pin_repo \
    "$directory" \
    "$(pin_field "$dependency" repository)" \
    "$(pin_field "$dependency" commit)" \
    "vendor/$directory"
done

pin_repo \
  tasks \
  "$(pin_field tasks repository)" \
  "$(pin_field tasks commit)" \
  tasks
find tasks/pool -type d -exec chmod 755 {} +
find tasks/pool -type f -exec chmod 644 {} +

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
