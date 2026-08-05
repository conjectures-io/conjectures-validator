#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TASKS_ROOT="${CONJECTURES_TASKS_ROOT:-$ROOT/../conjectures-tasks}"

# The build needs one file from the task repository — the audited Formal Conjectures patch —
# and that repository is a sibling of the build context, so a COPY cannot reach it. Passed as
# a named context instead. Nothing else from the task repository enters the image: the task
# bundles are mounted read-only at verification time, one directory per proof.
if [[ ! -d "$TASKS_ROOT" ]]; then
  echo "task repository is missing at $TASKS_ROOT" >&2
  echo "  just pin-tasks" >&2
  exit 2
fi

# Overridable so the production release build is this same script rather than a hand-typed
# `docker build` that can drift from it. deploy/worker/README.md uses :release.
TAG="${VERIFIER_IMAGE_TAG:-formal-conjectures-verifier:local}"

# A release image must be identifiable by commit, and Docker builds from the filesystem rather
# than from Git's index — so uncommitted or untracked files are in the image while
# VERIFIER_VERSION claims the commit alone. Refused for a release, allowed for :local, where
# building the working tree is the entire point.
if [[ "$TAG" != *:local ]] && [[ -n "$(git -C "$ROOT" status --porcelain=v1 --untracked-files=all)" ]]; then
  echo "error: refusing to build $TAG from a dirty tree." >&2
  git -C "$ROOT" status --short --untracked-files=all >&2
  echo "  Commit or stash first. Every verdict records this image's ID as what decided it," >&2
  echo "  and that has to mean a reviewable commit." >&2
  exit 2
fi

docker build --build-arg "ENABLE_NANODA=${ENABLE_NANODA:-0}" \
  --build-arg "LEAN_BUILD_THREADS=${FC_LEAN_BUILD_THREADS:-3}" \
  --build-context "tasks=$TASKS_ROOT" \
  --tag "$TAG" "$ROOT"

# The immutable local image ID, which is what the worker pins in
# VERIFIER_CONTAINER_DIGEST. Not the tag, and not a registry manifest digest.
docker image inspect --format '{{.Id}}' "$TAG"
