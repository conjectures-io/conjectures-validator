#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
TASKS_ROOT="${CONJECTURES_TASKS_ROOT:-$ROOT/../conjectures-tasks}"

# Every precondition below used to be a bare `test`, which under `set -e` exits 1 and prints
# nothing. Each one of them is reachable by someone setting this up for the first time, and an
# unexplained exit 1 an hour into a build is the worst possible way to learn any of it.
fail() {
  printf '%s\n' "$@" >&2
  exit 2
}

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
  # Not `rev-parse --is-inside-work-tree`: every destination sits inside this
  # repository's own work tree, so for an absent or empty directory that answers
  # "yes" about THIS repository. The clone would then be skipped and every command
  # below would retarget the validator checkout — repointing its origin at the
  # dependency and detaching its HEAD. Require a repository rooted at exactly
  # "$destination". Docker creates these directories empty to satisfy a bind mount,
  # so an empty one is expected rather than an error.
  if [[ "$(git -C "$destination" rev-parse --show-toplevel 2>/dev/null || true)" != "$ROOT/$destination" ]]; then
    if [[ -e "$destination" ]]; then
      if [[ -n "$(ls -A "$destination" 2>/dev/null)" ]]; then
        echo "$destination exists but is not a Git checkout for $name" >&2
        return 2
      fi
      rmdir "$destination"
    fi
    git clone --filter=blob:none --no-checkout "$url" "$destination"
  fi
  test "$(git -C "$destination" rev-parse --show-toplevel)" = "$ROOT/$destination"
  git -C "$destination" remote set-url origin "$url"
  git -C "$destination" fetch --no-tags origin "$commit"
  git -C "$destination" checkout --detach "$commit"
  test "$(git -C "$destination" rev-parse HEAD)" = "$commit"
  if [[ -n "$(git -C "$destination" status --porcelain --untracked-files=all)" ]]; then
    fail "$destination is not clean at $commit" \
      "  a stray edit here changes what gets verified, and assert_dependency_pins would refuse" \
      "  every proof afterwards; inspect it with: git -C $destination status" \
      "  (do not clean with -x: that deletes the Lean build cache along with it)"
  fi
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
  local patch_sha256
  patch_sha256="$(sha256sum "$patch_path" | awk '{print $1}')"
  if [[ "$patch_sha256" != "$expected_patch_sha256" ]]; then
    fail "audit patch $patch_path does not match pins.lock.json" \
      "  expected $expected_patch_sha256" \
      "  found    $patch_sha256" \
      "  this patch is the one build input not fetched by commit, so a mismatch is refused" \
      "  rather than applied; a task checkout newer than the validator's pin is the usual cause"
  fi
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
  if [[ "$derived_commit" != "$expected_commit" ]]; then
    fail "applying the audit patch to $base_commit produced $derived_commit," \
      "  but pins.lock.json requires $expected_commit" \
      "  the checkout, the patch and the pin disagree; nothing was built"
  fi
  git -C "$destination" checkout --detach "$derived_commit"
  test -z "$(git -C "$destination" status --porcelain --untracked-files=all)"
}

# The audited Formal Conjectures fix-up patch, normally read from the pinned task repository
# because that is where it is audited. A Docker build cannot reach that checkout: it is a
# sibling of the build context, and .dockerignore excludes `tasks`. So the Dockerfile passes
# the single file in through a named build context and names it here.
#
# Naming the file directly skips the task-repository preconditions below, and nothing else.
# What makes this step trustworthy is identical on both paths and asserted in
# pin_patched_repo: the patch is accepted only if its sha256 matches pins.lock.json, and the
# commit derived from applying it must equal the pinned formal_conjectures commit.
AUDIT_PATCH="${FC_AUDIT_PATCH:-}"
if [[ -z "$AUDIT_PATCH" ]]; then
  if ! git -C "$TASKS_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    fail "task repository is missing at $TASKS_ROOT; clone conjectures-tasks separately" \
      "  (set CONJECTURES_TASKS_ROOT if it lives somewhere else, or FC_AUDIT_PATCH to the" \
      "  audited patch file, as the Docker build does)"
  fi
  pinned_tasks="$(pin_field tasks commit)"
  current_tasks="$(git -C "$TASKS_ROOT" rev-parse HEAD)"
  if [[ "$current_tasks" != "$pinned_tasks" ]]; then
    fail "task repository at $TASKS_ROOT is at $current_tasks," \
      "  but pins.lock.json requires $pinned_tasks" \
      "  git -C $TASKS_ROOT fetch --no-tags origin $pinned_tasks" \
      "  git -C $TASKS_ROOT checkout --detach $pinned_tasks"
  fi
  if [[ -n "$(git -C "$TASKS_ROOT" status --porcelain --untracked-files=all)" ]]; then
    fail "task repository at $TASKS_ROOT is not clean" \
      "  the audit patch is read from this checkout, so it has to be the audited one"
  fi
  AUDIT_PATCH="$TASKS_ROOT/tiers/tier-1/formal-conjectures-audit-fixes.patch"
fi
if [[ ! -f "$AUDIT_PATCH" ]]; then
  fail "audit patch not found at $AUDIT_PATCH"
fi
# `git -C <dir> apply` resolves the patch path against <dir>, so a relative one would break.
AUDIT_PATCH="$(cd "$(dirname "$AUDIT_PATCH")" && pwd)/$(basename "$AUDIT_PATCH")"

pin_patched_repo \
  formal-conjectures \
  "$(pin_field formal_conjectures repository)" \
  "$(pin_field formal_conjectures base_commit)" \
  "$(pin_field formal_conjectures commit)" \
  vendor/formal-conjectures \
  "$AUDIT_PATCH" \
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
