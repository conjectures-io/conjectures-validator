#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
TASKS_ROOT="${CONJECTURES_TASKS_ROOT:-$ROOT/../conjectures-tasks}"

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

pin_patched_repo() {
  local name="$1"
  local url="$2"
  local base_commit="$3"
  local expected_commit="$4"
  local destination="$5"
  local patch_path="$6"
  local expected_patch_sha256="$7"
  pin_repo "$name" "$url" "$base_commit" "$destination"
  test "$(sha256sum "$patch_path" | awk '{print $1}')" = "$expected_patch_sha256"
  git -C "$destination" apply "$patch_path"
  git -C "$destination" add --all
  local source_tree
  source_tree="$(git -C "$destination" write-tree)"
  local derived_commit
  derived_commit="$(
    printf '%s\n' 'fix(ErdosProblems): correct audited candidate statements' |
      GIT_AUTHOR_NAME='Conjectures Pool Builder' \
      GIT_AUTHOR_EMAIL='pool@conjectures.io' \
      GIT_AUTHOR_DATE='2026-08-03T00:00:00Z' \
      GIT_COMMITTER_NAME='Conjectures Pool Builder' \
      GIT_COMMITTER_EMAIL='pool@conjectures.io' \
      GIT_COMMITTER_DATE='2026-08-03T00:00:00Z' \
      git -C "$destination" commit-tree "$source_tree" -p "$base_commit"
  )"
  test "$derived_commit" = "$expected_commit"
  git -C "$destination" checkout --detach "$derived_commit"
  test -z "$(git -C "$destination" status --porcelain --untracked-files=all)"
}

if ! git -C "$TASKS_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "task repository is missing at $TASKS_ROOT; clone conjectures-tasks separately" >&2
  exit 2
fi
test "$(git -C "$TASKS_ROOT" rev-parse HEAD)" = "$(pin_field tasks commit)"
test -z "$(git -C "$TASKS_ROOT" status --porcelain --untracked-files=all)"

pin_patched_repo \
  formal-conjectures \
  "$(pin_field formal_conjectures repository)" \
  "$(pin_field formal_conjectures base_commit)" \
  "$(pin_field formal_conjectures commit)" \
  vendor/formal-conjectures \
  "$TASKS_ROOT/source-patches/formal-conjectures-audit-fixes.patch" \
  "$(pin_field formal_conjectures patch_sha256)"

for dependency in comparator lean4export landrun nanoda; do
  directory="${dependency//_/-}"
  pin_repo \
    "$directory" \
    "$(pin_field "$dependency" repository)" \
    "$(pin_field "$dependency" commit)" \
    "vendor/$directory"
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
