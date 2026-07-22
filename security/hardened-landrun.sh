#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LANDRUN="$ROOT/vendor/landrun/bin/landrun"
SECCOMP_LAUNCHER="$ROOT/.tools/seccomp-launcher"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "hardened Landrun requires Linux" >&2
  exit 126
fi
if [[ ! -x "$LANDRUN" || -L "$LANDRUN" ]]; then
  echo "pinned Landrun binary is missing or unsafe" >&2
  exit 126
fi
if [[ ! -x "$SECCOMP_LAUNCHER" || -L "$SECCOMP_LAUNCHER" ]]; then
  echo "seccomp/Landlock probe is missing or unsafe" >&2
  exit 126
fi
"$SECCOMP_LAUNCHER" --check-landlock

arguments=()
while (($#)); do
  case "$1" in
    --best-effort)
      # The launcher has already required Landlock ABI v4. Best-effort lets
      # Landrun use v4 on common Ubuntu kernels instead of demanding v5.
      shift
      arguments+=(--best-effort)
      ;;
    --ro)
      (($# >= 2)) || { echo "missing value for --ro" >&2; exit 64; }
      if [[ "$2" = / ]]; then
        # Comparator's default exposes the host's entire read-only filesystem.
        # The generated workspace, toolchain, and all pinned dependencies live
        # below ROOT. Dynamically linked Lean binaries also need the host's
        # loader and system libraries, but not home directories, mounts, or
        # /proc.
        arguments+=(--ro "$ROOT")
        for path in /usr/lib /usr/lib64; do
          [[ -e "$path" ]] && arguments+=(--rox "$path")
        done
        for path in /etc/ld.so.cache /etc/ld.so.conf /etc/ld.so.conf.d; do
          [[ -e "$path" ]] && arguments+=(--ro "$path")
        done
      else
        arguments+=("$1" "$2")
      fi
      shift 2
      ;;
    --rw)
      (($# >= 2)) || { echo "missing value for --rw" >&2; exit 64; }
      if [[ "$2" = /dev ]]; then
        for device in /dev/null /dev/zero /dev/random /dev/urandom; do
          [[ -e "$device" ]] && arguments+=(--rw "$device")
        done
      else
        arguments+=("$1" "$2")
      fi
      shift 2
      ;;
    --rwx)
      (($# >= 2)) || { echo "missing value for --rwx" >&2; exit 64; }
      # Comparator asks for an executable build directory, but theorem-library
      # compilation and export only need content reads and writes.  Removing
      # execute permission prevents generated native artifacts from running.
      arguments+=(--rw "$2")
      shift 2
      ;;
    *)
      arguments+=("$1")
      shift
      ;;
  esac
done

exec "$LANDRUN" "${arguments[@]}"
