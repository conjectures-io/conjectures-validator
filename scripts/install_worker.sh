#!/usr/bin/env bash
# Install or update the production verification worker as a systemd service.
#
# Idempotent: safe to re-run after a release, an image rebuild, or a task repin. It installs
# nothing it cannot first verify, and it does not start the service — a worker that starts
# claiming submissions is the operator's decision, not this script's.
#
#   sudo scripts/install_worker.sh
#
# The worker runs on the host rather than in a container because it launches one verifier
# container per proof and names host paths for that container's read-only mounts. Containerising
# it would mean giving a container the Docker socket, which is host root. See deploy/worker/README.md.
set -euo pipefail

RELEASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TASKS_ROOT="${CONJECTURES_TASKS_ROOT:-$RELEASE/../conjectures-tasks}"
IMAGE_TAG="${VERIFIER_IMAGE_TAG:-formal-conjectures-verifier:release}"
ENV_FILE=/etc/conjectures/verification-worker.env
UNIT=/etc/systemd/system/conjectures-verification-worker.service
ACCOUNT=conjectures-worker

die() { echo "error: $*" >&2; exit 2; }
step() { echo "==> $*"; }

[[ $EUID -eq 0 ]] || die "run with sudo: it creates a system account and installs a unit"

# --- the release location ----------------------------------------------------------------
#
# The unit sets ProtectHome=true, which makes systemd present /root and /home as empty to the
# service. A release under either is unreadable to the worker no matter what the mode bits say,
# and no chmod can fix it — so refuse here instead of at first start.
case "$RELEASE" in
  /root/*|/root|/home/*) die "the release is at $RELEASE, under a path the unit's ProtectHome=true
  hides from the service. Move it and the task checkout together, keeping them siblings:
      systemctl stop conjectures-verification-worker 2>/dev/null || true
      install -d -m 755 /opt/conjectures-validator
      mv $RELEASE /opt/conjectures-validator/current
      mv $TASKS_ROOT /opt/conjectures-validator/conjectures-tasks
  then re-run this from /opt/conjectures-validator/current." ;;
esac

# --- the task release --------------------------------------------------------------------
step "task release"
[[ -d "$TASKS_ROOT" ]] || die "no task checkout at $TASKS_ROOT; run: just pin-tasks"
TASKS_ROOT="$(cd "$TASKS_ROOT" && pwd)"
want_tasks="$(python3 -c 'import json;print(json.load(open("pins.lock.json"))["tasks"]["commit"])')"
have_tasks="$(git -C "$TASKS_ROOT" rev-parse HEAD 2>/dev/null || echo none)"
[[ "$have_tasks" == "$want_tasks" ]] || die "$TASKS_ROOT is at $have_tasks but pins.lock.json
  pins $want_tasks. The pin is hash-exact; run: just pin-tasks"
[[ -z "$(git -C "$TASKS_ROOT" status --porcelain --untracked-files=all)" ]] \
  || die "$TASKS_ROOT has local modifications; the pin requires a clean tree"

# The worker runs as a non-root account, so the same mode trap that catches the API applies
# here: correct bytes behind a directory the service cannot search read as an absent allowlist.
closed=$(
  find "$TASKS_ROOT" -maxdepth 0 ! -perm -o=rx
  find "$TASKS_ROOT/allowlist.json" -maxdepth 0 ! -perm -o=r
  find "$TASKS_ROOT/pool" "$TASKS_ROOT/tiers" \
    \( -type d ! -perm -o=rx \) -o \( -type f ! -perm -o=r \) 2>/dev/null
)
[[ -z "$closed" ]] || die "$ACCOUNT cannot read these task paths:
$(printf '  %s\n' $closed)
  The bytes are correct; the modes are not. Run: just pin-tasks"

# --- the verifier image ------------------------------------------------------------------
step "verifier image"
command -v docker >/dev/null || die "docker is not installed"
IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$IMAGE_TAG" 2>/dev/null || true)"
[[ -n "$IMAGE_ID" ]] || die "the image $IMAGE_TAG does not exist locally. Build it from a clean
  tree, which takes an hour and needs ~72 GiB:
      VERIFIER_IMAGE_TAG=$IMAGE_TAG scripts/build_image.sh"
# Pinned by immutable local image ID, never by tag: a tag can be moved onto other bytes, and
# every verdict this worker writes names the image that produced it.
RELEASE_COMMIT="$(git -C "$RELEASE" rev-parse HEAD)"
if [[ -n "$(git -C "$RELEASE" status --porcelain --untracked-files=all)" ]]; then
  echo "warning: $RELEASE is not clean, so VERIFIER_VERSION=validator-$RELEASE_COMMIT does not" >&2
  echo "         fully identify what is deployed. Commit or stash before a real release." >&2
fi

# --- the service account -----------------------------------------------------------------
step "service account"
if ! id "$ACCOUNT" >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/conjectures-worker --create-home \
    --shell /usr/sbin/nologin "$ACCOUNT"
fi
getent group docker >/dev/null || die "there is no docker group; is Docker Engine installed?"
# Docker group membership is privileged host access. It belongs to this account only, and no
# network-facing service may run as it.
id -nG "$ACCOUNT" | tr ' ' '\n' | grep -qx docker || usermod --append --groups docker "$ACCOUNT"

# --- the python environment --------------------------------------------------------------
step "python environment"
if [[ ! -x "$RELEASE/.venv/bin/python" ]]; then
  python3 -m venv "$RELEASE/.venv"
fi
# Root-owned and only these three: the worker orchestrates and talks to Postgres. It has no
# Lean, no bittensor, and nothing that could compile or execute a submitted proof.
"$RELEASE/.venv/bin/pip" install --quiet --constraint "$RELEASE/requirements-service.lock" \
  SQLAlchemy greenlet 'psycopg[binary]'
chown -R root:root "$RELEASE/.venv"

# --- the environment file ----------------------------------------------------------------
step "environment file"
install -d -o root -g "$ACCOUNT" -m 0750 /etc/conjectures
if [[ ! -f "$ENV_FILE" ]]; then
  install -o root -g "$ACCOUNT" -m 0640 \
    "$RELEASE/deploy/worker/verification-worker.env.example" "$ENV_FILE"
  # Resolved from this host rather than left as the example's defaults. The example names
  # /opt/conjectures-tasks, but the API mounts the task repository as a sibling of the release,
  # and the two must agree or the API and the worker verify against different pools.
  python3 - "$ENV_FILE" "$TASKS_ROOT" "$(hostname)" <<'PY'
import pathlib, sys
path, tasks, host = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
replace = {
    "CONJECTURES_TASKS_ROOT": tasks,
    "TASK_ALLOWLIST_PATH": f"{tasks}/allowlist.json",
    "TASK_POOL_ROOT": f"{tasks}/pool",
    "VERIFICATION_WORKER_ID": f"{host}/production-worker-1",
}
lines = []
for line in path.read_text().splitlines():
    key = line.split("=", 1)[0]
    lines.append(f"{key}={replace[key]}" if key in replace else line)
path.write_text("\n".join(lines) + "\n")
PY
  echo "    installed $ENV_FILE from the template"
fi

# VERIFIER_VERSION and the image digest are derived, not chosen, and a stale digest is the
# likeliest operational error after a rebuild. Refreshed in place; secrets are never touched.
python3 - "$ENV_FILE" "$IMAGE_ID" "validator-$RELEASE_COMMIT" <<'PY'
import pathlib, sys
path, image_id, version = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
derived = {"VERIFIER_CONTAINER_DIGEST": image_id, "VERIFIER_VERSION": version}
lines, changed = [], []
for line in path.read_text().splitlines():
    key = line.split("=", 1)[0]
    if key in derived and line != f"{key}={derived[key]}":
        changed.append(key)
        line = f"{key}={derived[key]}"
    lines.append(line)
path.write_text("\n".join(lines) + "\n")
for key in changed:
    print(f"    refreshed {key}")
PY

# A placeholder left in place would otherwise surface as an opaque connection or settings
# failure at first start, one layer away from the line that needs editing.
if grep -nE 'replace-me|<[a-z0-9-]+>' "$ENV_FILE" >/dev/null; then
  echo ""
  echo "$ENV_FILE still has placeholders:" >&2
  grep -nE 'replace-me|<[a-z0-9-]+>' "$ENV_FILE" >&2
  die "fill them in with 'sudoedit $ENV_FILE', then re-run this script"
fi

# --- the unit ----------------------------------------------------------------------------
step "systemd unit"
# The shipped unit names /opt/conjectures-validator/current. Substituted rather than required,
# so a host with a different release path gets a correct unit instead of a silent mismatch.
sed "s|/opt/conjectures-validator/current|$RELEASE|g" \
  "$RELEASE/deploy/worker/conjectures-verification-worker.service" > "$UNIT.new"
install -o root -g root -m 0644 "$UNIT.new" "$UNIT"
rm -f "$UNIT.new"
systemctl daemon-reload

# --- preflight ---------------------------------------------------------------------------
# The worker's own six-point check: production settings, pool against allowlist, image tag
# still resolving to the pinned ID, that image's Landlock/seccomp/non-root probes, and the
# database. Claims nothing. Run as the service account with the real environment file; the
# unit's full sandbox is not applied here, so a sandbox fault can still appear at first start.
step "preflight (claims nothing)"
if systemd-run --quiet --pipe --wait --collect \
  -p User="$ACCOUNT" -p Group="$ACCOUNT" -p SupplementaryGroups=docker \
  -p WorkingDirectory="$RELEASE" -p EnvironmentFile="$ENV_FILE" \
  "$RELEASE/.venv/bin/python" -m verification_worker --check; then
  cat <<EOF

Installed and preflight-clean. Start it when you are ready:

    sudo systemctl start conjectures-verification-worker
    sudo journalctl -u conjectures-verification-worker -f
    sudo systemctl enable conjectures-verification-worker   # after the first proof completes
EOF
else
  cat >&2 <<EOF

The preflight failed. The output above is the reason; nothing was started and no submission
was claimed. The unit is installed, so once the cause is fixed re-run this script.
EOF
  exit 1
fi
