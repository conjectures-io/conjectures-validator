# The one entrypoint for the operational stack: Postgres, Flyway, the API, and
# optionally the verification worker, deposit watcher, and emissions worker.
#
#   just up            # db -> migrate -> api
#   just up-worker     # ... and the development verification worker
#   just up-watcher    # ... and the deposit watcher
#   just up-emissions  # ... and the Subnet 66 epoch weight setter
#   just up-all        # ... and all three workers
#   just logs api
#   just reset         # destroy the database and start clean
#
# Why this exists rather than a longer README line: every service belongs to a
# single Compose project, and *which* project depends on the order of the `-f`
# flags. docker-compose.api.yml declares `name: conjectures-api` and `include`s
# the database stack, so it has to come first. Passing docker-compose.db.yml
# first instead yields the project `conjectures-db` with its own
# `conjectures-db_pgdata` volume — a second, empty database that the API never
# writes to and that the worker then polls forever. Nothing in either log says
# so. The order is not left to memory.
#
# Deliberately untouched: docker-compose.yml (the hostile-proof verifier sandbox,
# a different trust domain) and docker-compose.pytest-db.yml (throwaway, already
# its own project).

set shell := ["bash", "-euo", "pipefail", "-c"]
set dotenv-load := true
set positional-arguments := true

# docker-compose.api.yml MUST stay first. See the note above.

compose := "docker compose -f docker-compose.api.yml"
compose_worker := compose + " -f docker-compose.worker.yml"
compose_watcher := compose + " -f docker-compose.watcher.yml"
compose_emissions := compose + " -f docker-compose.emissions.yml"

# Every overlay at once. `down`, `ps`, `logs` and `reset` use this so a container
# started by any of the recipes above is visible to — and removable by — all of
# them. An overlay Compose does not know about is a container `down` leaves
# running against a database it has just deleted the volume of.

compose_all := compose_worker + " -f docker-compose.watcher.yml -f docker-compose.emissions.yml"
project := "conjectures-api"
legacy := "conjectures-db"

# The worker bind-mounts this checkout and establishes every vendored dependency
# with git, which refuses a repository owned by another uid. The image default is
# a guess at 1000; the owner of the checkout is the actual answer. Getting it
# wrong fails every submission with REPOSITORY_COMMIT_MISMATCH at LOAD_TASK.

uid := `stat -c '%u' . 2>/dev/null || stat -f '%u' .`
gid := `stat -c '%g' . 2>/dev/null || stat -f '%g' .`

# Show the available recipes.
default:
    @just --list --unsorted

# --- the stack ---------------------------------------------------------------

# Build and start db, migrate, then api.
up: _preflight
    @echo "==> starting: db -> migrate -> api"
    {{ compose }} up -d --build
    @{{ compose }} ps

# Build and start the development stack including the insecure in-process verification worker.
# Production uses deploy/worker/conjectures-verification-worker.service instead.
up-worker: _preflight
    @echo "==> starting: db -> migrate -> api -> worker (as {{ uid }}:{{ gid }})"
    DOCKER_UID={{ uid }} DOCKER_GID={{ gid }} {{ compose_worker }} up -d --build
    @DOCKER_UID={{ uid }} DOCKER_GID={{ gid }} {{ compose_worker }} ps

# The watcher needs the four DEPOSIT_WATCH_* values in .env, which _check-watch
# requires rather than defaulting: each one decides which transfers buy credits.
# Deliberately NOT started by `just up` — it reads mainnet and writes to the
# credit ledger, so switching it on is a decision, not a default.

# Build and start the whole stack including the deposit watcher.
up-watcher: _preflight _check-watch
    @echo "==> starting: db -> migrate -> api -> watcher"
    {{ compose_watcher }} up -d --build
    @{{ compose_watcher }} ps

# Build and start the epoch worker that sends all Subnet 66 weight to treasury UID 121.
up-emissions: _preflight _check-emissions
    @echo "==> starting: db -> migrate -> api -> emissions (as {{ uid }}:{{ gid }})"
    DOCKER_UID={{ uid }} DOCKER_GID={{ gid }} {{ compose_emissions }} up -d --build
    @DOCKER_UID={{ uid }} DOCKER_GID={{ gid }} {{ compose_emissions }} ps

# Build and start the complete development stack, including emissions. This is not a production
# launcher because its verification worker intentionally uses the insecure in-process sandbox path.
up-all: _preflight _check-watch _check-emissions
    @echo "==> starting: db -> migrate -> api -> worker -> watcher -> emissions (as {{ uid }}:{{ gid }})"
    DOCKER_UID={{ uid }} DOCKER_GID={{ gid }} {{ compose_all }} up -d --build
    @DOCKER_UID={{ uid }} DOCKER_GID={{ gid }} {{ compose_all }} ps

# The one dry run worth having: it performs the startup registration check and the
# timestamp-to-block bisection, so a wrong address or an unreachable chain is found
# here rather than in a restart loop. Brings up db and migrate, because the cursor
# it reports on lives in the database.
#
# NOT read-only end to end: a first run with no cursor row writes one, which is the
# point — the bisection result must never be recomputed. It records no transfers and
# credits nothing.

# Resolve the genesis timestamp and report the backlog, crediting nothing.
watcher-check: _preflight _check-watch
    {{ compose_watcher }} run --rm --build watcher \
      python -m deposit_watcher --dry-run

# --- production verification worker ------------------------------------------
#
# Not compose. The worker launches one verifier container per proof and names host
# paths for that container's read-only mounts, so it runs on the host under systemd;
# putting it in a container would mean handing a container the Docker socket, which is
# host root. docker-compose.worker.yml is the DEVELOPMENT worker and uses the insecure
# in-process runner instead — WorkerSettings refuses that when APP_MODE=PROD.
#
# Three commands, in this order:
#
#   just doctor-host       # seconds; the gates that make the rest pointless if unmet
#   just build-verifier    # ~1 hour, ~72 GiB
#   just install-worker    # account, venv, env file, unit, preflight

# Check the host can host a verifier at all, before anything expensive.
doctor-host:
    #!/usr/bin/env bash
    set -uo pipefail
    fail=0
    note() { printf '  %-26s %s\n' "$1" "$2"; }
    bad() { printf '  %-26s %s  <-- %s\n' "$1" "$2" "$3"; fail=1; }
    echo "==> host gates for the production verifier"
    # Landlock ABI 4 first appeared in Linux 6.7. The verifier's own probe is the
    # authority — `docker run <image>` runs the doctor — but that needs the image built,
    # and this catches the common case in a second rather than in an hour.
    release=$(uname -r); major=${release%%.*}; rest=${release#*.}; minor=${rest%%.*}
    if (( major > 6 || (major == 6 && minor >= 7) )); then
      note kernel "$release"
    else
      bad kernel "$release" "Landlock ABI 4 needs 6.7+; the verifier refuses to run"
    fi
    if grep -qw landlock /sys/kernel/security/lsm 2>/dev/null; then
      note landlock "enabled"
    else
      bad landlock "absent from /sys/kernel/security/lsm" "add lsm=...,landlock to the cmdline"
    fi
    cpus=$(nproc)
    if (( cpus >= 4 )); then note cpus "$cpus"; else bad cpus "$cpus" "one verifier wants 4"; fi
    # One verifier container is allowed 72g of memory and builds Lean into a tmpfs, so
    # both the image store and RAM+swap matter.
    avail=$(df -BG --output=avail /var/lib/docker 2>/dev/null | tail -1 | tr -dc '0-9')
    if (( ${avail:-0} >= 72 )); then
      note "docker disk" "${avail}G free"
    else
      bad "docker disk" "${avail:-?}G free" "72 GiB needed for one verification"
    fi
    if command -v docker >/dev/null && docker info >/dev/null 2>&1; then
      note docker "reachable"
    else
      bad docker "not reachable" "install Docker Engine, or add yourself to the docker group"
    fi
    if [[ -f /sys/fs/cgroup/cgroup.controllers ]]; then
      note cgroups "v2"
    else
      bad cgroups "v1" "the memory and pids limits the runner sets need v2"
    fi
    echo ""
    if (( fail )); then
      echo "This host cannot run the production verifier yet. Nothing above is optional:" >&2
      echo "the worker's preflight probes the real sandbox and exits 2 rather than" >&2
      echo "verifying a paid submission without isolation." >&2
      exit 1
    fi
    echo "All gates pass. Next: just build-verifier"

# Build the reviewed verifier image and print its immutable image ID.
build-verifier: _check-docker _check-tasks
    #!/usr/bin/env bash
    set -euo pipefail
    echo "==> building the verifier image (about an hour; Lean and Mathlib)"
    VERIFIER_IMAGE_TAG=formal-conjectures-verifier:release scripts/build_image.sh

# Install or update the production worker as a systemd service. Does not start it.
install-worker:
    #!/usr/bin/env bash
    set -euo pipefail
    exec sudo --preserve-env=CONJECTURES_TASKS_ROOT,VERIFIER_IMAGE_TAG \
      scripts/install_worker.sh

# Stop and remove containers, including all workers. The database volume SURVIVES.
down:
    DOCKER_UID={{ uid }} DOCKER_GID={{ gid }} {{ compose_all }} down

# Only the api stack: `down`, `ps` and `logs` name every overlay solely so a running
# worker or watcher is visible to them. `up -d` against those overlays would START
# them as a side effect of restarting the API, which is not what "restart" should
# mean. To recreate them too, run `just up-worker`, `just up-watcher` or `just up-all`.

# Recreate the api stack's containers without rebuilding images.
restart:
    {{ compose }} up -d --force-recreate

# Build the api stack's images without starting anything.
build:
    {{ compose }} build

# Build the verification worker image without starting anything.
build-worker:
    DOCKER_UID={{ uid }} DOCKER_GID={{ gid }} {{ compose_worker }} build worker

# Build the deposit watcher image without starting anything.
build-watcher:
    {{ compose_watcher }} build watcher

# Build the epoch weight setter image without starting anything.
build-emissions: _check-emissions
    DOCKER_UID={{ uid }} DOCKER_GID={{ gid }} {{ compose_emissions }} build emissions

# Destroy the database volume and bring the stack back up empty.
[confirm("This DESTROYS the database volume and every row in it. Continue?")]
reset: _check-env
    @echo "==> removing containers and the database volume"
    DOCKER_UID={{ uid }} DOCKER_GID={{ gid }} {{ compose_all }} down -v --remove-orphans
    @echo "==> starting clean — every migration re-applies from V001"
    {{ compose }} up -d --build
    @{{ compose }} ps

# --- inspection --------------------------------------------------------------

# Show what is running.
ps:
    @DOCKER_UID={{ uid }} DOCKER_GID={{ gid }} {{ compose_all }} ps

# Follow logs, all services or one: `just logs api`
logs *service:
    DOCKER_UID={{ uid }} DOCKER_GID={{ gid }} {{ compose_all }} logs -f --tail=200 "$@"

# Apply pending migrations only, then exit.
migrate: _preflight
    {{ compose }} up --exit-code-from migrate migrate

# The task bundles are a detached checkout of a separate repository, pinned in
# pins.lock.json and NOT stored in this repo. Its location comes from the compose
# file (see _tasks-paths), never from a path written down twice. Docker will
# happily create that path EMPTY to satisfy the bind mount, and the API then dies
# at startup with "task pool tier tier-1 is missing" on a restart loop.
#
# scripts/pin_dependencies.sh requires this checkout to already exist and refuses
# to create it; this recipe is the missing half. It pins the task repository only
# and skips the vendored Lean repositories, which the API and worker do not read.

# Clone or update the task-bundle checkout the compose file mounts, at its pinned commit.
pin-tasks: _check-docker _check-env
    #!/usr/bin/env bash
    set -euo pipefail
    url=$(python3 -c 'import json; print(json.load(open("pins.lock.json"))["tasks"]["repository"])')
    commit=$(python3 -c 'import json; print(json.load(open("pins.lock.json"))["tasks"]["commit"])')
    # Where the API actually reads tasks from, straight out of the compose file —
    # never a second guess at the path. Populating a directory the container does
    # not mount looks like success and still dies with "task pool tier is missing".
    read -r root pool_rel allow_rel _ < <(just _tasks-paths)
    # Require a repository rooted at exactly $root. `rev-parse --is-inside-work-tree`
    # would not do: for an absent or empty directory it answers about whichever
    # enclosing work tree it finds, and every git -C below would then retarget that
    # repository — repointing its origin and detaching its HEAD onto a foreign commit.
    if [[ "$(git -C "$root" rev-parse --show-toplevel 2>/dev/null || true)" != "$root" ]]; then
      if [[ -e "$root" ]]; then
        if [[ -n "$(ls -A "$root" 2>/dev/null)" ]]; then
          echo "error: $root exists, is not a git checkout, and is not empty." >&2
          echo "  Inspect it and remove it yourself — refusing to delete unknown content." >&2
          exit 1
        fi
        # The empty directory Docker creates when a bind mount has no source.
        rmdir "$root"
      fi
      git clone --filter=blob:none --no-checkout "$url" "$root"
    fi
    # Belt and braces: nothing below may run against another repository.
    test "$(git -C "$root" rev-parse --show-toplevel)" = "$root"
    git -C "$root" remote set-url origin "$url"
    git -C "$root" fetch --no-tags origin "$commit"
    git -C "$root" checkout --detach "$commit"
    test "$(git -C "$root" rev-parse HEAD)" = "$commit"
    # The clone root itself, not only the pool under it. The API and worker containers run as a
    # non-root uid, and the bind mount lands on this directory: without search permission here
    # every read fails with "task allowlist is unavailable" while the bytes sit there correct
    # and complete. A root umask of 077 — or an operator's earlier `mkdir` — leaves it 0700.
    chmod 755 "$root"
    chmod 644 "$root/$allow_rel"
    # `tiers` carries the selection audit, which the worker loads and the API does not.
    for tree in "$root/$pool_rel" "$root/tiers"; do
      [[ -d "$tree" ]] || continue
      find "$tree" -type d -exec chmod 755 {} +
      find "$tree" -type f -exec chmod 644 {} +
    done
    echo "==> $root pinned at $commit"

# Open a psql shell on the database.
psql:
    docker exec -it conjectures_db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'

# Hit the API's liveness and readiness endpoints.
health:
    @curl -fsS "http://127.0.0.1:${API_PORT:-8000}/healthz" && echo " <- healthz"
    @curl -fsS "http://127.0.0.1:${API_PORT:-8000}/readyz"  && echo " <- readyz"

# Run the preflight checks and report, changing nothing.
doctor: _preflight
    @echo "==> project: {{ project }}   volume: {{ project }}_pgdata"
    @echo "==> worker would run as {{ uid }}:{{ gid }}"
    @echo "==> all checks passed"

# --- private ------------------------------------------------------------------

_preflight: _check-docker _check-env _check-tasks _check-legacy

_check-docker:
    @command -v docker >/dev/null || { echo "error: docker is not on PATH" >&2; exit 1; }
    @docker compose version >/dev/null 2>&1 \
      || { echo "error: the docker compose plugin is missing (needs Compose v2)" >&2; exit 1; }
    @docker info >/dev/null 2>&1 \
      || { echo "error: cannot reach the Docker daemon — is it running, and are you in the docker group?" >&2; exit 1; }

_check-env:
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ ! -f .env ]]; then
      echo "error: .env is missing. Start from the template:" >&2
      echo "    cp .env.example .env" >&2
      echo "  then fill in the values marked REQUIRED IN PROD." >&2
      exit 1
    fi
    missing=()
    # Compose interpolates these, and the database role is created from them on
    # first init of an empty volume.
    for var in POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB PAYMENT_RECIPIENT_SS58; do
      grep -qE "^${var}=.+" .env || missing+=("$var")
    done
    mode=$(grep -E '^APP_MODE=' .env | tail -1 | cut -d= -f2- | tr '[:lower:]' '[:upper:]' || true)
    if [[ "$mode" == "PROD" ]]; then
      # submission_api settings refuses to start without these in production.
      # Dynamic pricing reads one live Subnet Alpha stake position. Development can reuse the
      # payment recipient as a dummy hotkey, but production must name the real stake hotkey.
      for var in PUBLIC_CURSOR_SECRET PUBLIC_ACTIVITY_SALT WEBSITE_BASE_URL MAIL_SENDER BOUNTY_WALLET_HOTKEY_SS58; do
        grep -qE "^${var}=.+" .env || missing+=("$var")
      done
    else
      # Outside production SUBMISSION_AUTHENTICATOR defaults to `development`, which
      # refuses to start unless DEVELOPMENT_HOTKEYS names at least one address.
      auth=$(grep -E '^SUBMISSION_AUTHENTICATOR=' .env | tail -1 | cut -d= -f2- || true)
      if [[ -z "$auth" || "$auth" == "development" ]]; then
        grep -qE '^DEVELOPMENT_HOTKEYS=.+' .env || missing+=("DEVELOPMENT_HOTKEYS")
      fi
    fi
    if (( ${#missing[@]} )); then
      echo "error: unset or empty in .env: ${missing[*]}" >&2
      echo "  See .env.example for what each one is and how to generate it." >&2
      exit 1
    fi

# The four values that decide which transfers buy credits, plus the one consistency
# rule between the watcher and the API. Not folded into _check-env: the watcher is
# opt-in, and `just up` must not start demanding chain configuration for a stack
# that does not read the chain.
_check-watch:
    #!/usr/bin/env bash
    set -euo pipefail
    missing=()
    for var in DEPOSIT_WATCH_RECIPIENT_SS58 DEPOSIT_WATCH_NETUID DEPOSIT_WATCH_UID DEPOSIT_WATCH_FROM; do
      grep -qE "^${var}=.+" .env || missing+=("$var")
    done
    if (( ${#missing[@]} )); then
      echo "error: the deposit watcher needs these in .env: ${missing[*]}" >&2
      echo "  Each one decides which transfers buy credits, so none of them has a default." >&2
      echo "  See .env.example under 'Deposit watcher'." >&2
      exit 1
    fi
    # DEPOSIT_WATCH_FROM must be an instant, not a wall clock. A bare local time would
    # start crediting hours early or late depending on the container's timezone, and the
    # watcher records the resolved block permanently — so the mistake is not undoable by
    # fixing the variable afterwards.
    from=$(grep -E '^DEPOSIT_WATCH_FROM=' .env | tail -1 | cut -d= -f2- | tr -d '"'"'"'')
    python3 - "$from" <<'PY'
    import datetime as dt, sys
    raw = sys.argv[1].strip()
    try:
        value = dt.datetime.fromisoformat(raw)
    except ValueError:
        sys.exit(f"error: DEPOSIT_WATCH_FROM is not an ISO-8601 timestamp: {raw!r}\n"
                 "  e.g. DEPOSIT_WATCH_FROM=2026-08-01T00:00:00Z")
    if value.tzinfo is None:
        sys.exit(f"error: DEPOSIT_WATCH_FROM has no timezone offset: {raw!r}\n"
                 "  A bare local time is not an instant. Use e.g. 2026-08-01T00:00:00Z.")
    PY
    # One price, two processes. The API quotes PAYMENT_AMOUNT_RAO to a miner and the
    # watcher credits at CREDIT_PRICE_RAO; if they disagree, a paid deposit buys a
    # different number of submissions than the website said it would.
    price=$(grep -E '^CREDIT_PRICE_RAO=' .env | tail -1 | cut -d= -f2- || true)
    quoted=$(grep -E '^PAYMENT_AMOUNT_RAO=' .env | tail -1 | cut -d= -f2- || true)
    price=${price:-500000000}
    quoted=${quoted:-500000000}
    if [[ "$price" != "$quoted" ]]; then
      echo "error: CREDIT_PRICE_RAO ($price) and PAYMENT_AMOUNT_RAO ($quoted) disagree." >&2
      echo "  They are the same price seen from two sides — the API quotes it, the" >&2
      echo "  watcher credits at it. Set them equal." >&2
      exit 1
    fi

# The only service that signs chain calls. Require one explicit host wallet and never infer it
# from the API or watcher configuration.
_check-emissions:
    #!/usr/bin/env bash
    set -euo pipefail
    missing=()
    for var in EMISSIONS_WALLET_HOST_PATH EMISSIONS_WALLET_NAME EMISSIONS_WALLET_HOTKEY; do
      grep -qE "^${var}=.+" .env || missing+=("$var")
    done
    if (( ${#missing[@]} )); then
      echo "error: the emissions worker needs these in .env: ${missing[*]}" >&2
      echo "  It signs SetWeights for Subnet 66; see .env.example under 'Treasury emissions'." >&2
      exit 1
    fi
    root=$(grep -E '^EMISSIONS_WALLET_HOST_PATH=' .env | tail -1 | cut -d= -f2-)
    name=$(grep -E '^EMISSIONS_WALLET_NAME=' .env | tail -1 | cut -d= -f2-)
    if [[ ! -d "$root/$name" ]]; then
      echo "error: emissions wallet directory is missing: $root/$name" >&2
      exit 1
    fi

_check-tasks:
    #!/usr/bin/env bash
    set -euo pipefail
    read -r root pool_rel allow_rel api_user < <(just _tasks-paths)
    pool="$root/$pool_rel"
    allow="$root/$allow_rel"
    fail() {
      echo "error: $1" >&2
      echo "  The task bundles are a pinned checkout of a separate repository, which" >&2
      echo "  docker-compose.api.yml mounts read-only from:" >&2
      echo "      $root" >&2
      echo "  Docker creates that path EMPTY when it is absent, so the API starts and" >&2
      echo "  then restart-loops on 'task pool tier ... is missing'." >&2
      echo "" >&2
      echo "    just pin-tasks" >&2
      exit 1
    }
    [[ -d "$pool" ]] || fail "no task pool at $pool"
    [[ -f "$allow" ]] || fail "no allowlist at $allow"
    # Bundle bytes are hash-verified against the allowlist at startup, so a checkout
    # that has drifted from pins.lock.json fails closed inside the container with a
    # digest mismatch. Cheaper to say so here.
    want=$(python3 -c 'import json; print(json.load(open("pins.lock.json"))["tasks"]["commit"])')
    have=$(git -C "$root" rev-parse HEAD 2>/dev/null || echo none)
    [[ "$have" == "$want" ]] || fail "$root is at $have, but pins.lock.json pins $want"
    tiers=$(python3 -c "import json; print(' '.join(json.load(open('$allow'))['tier_order']))")
    missing=()
    for tier in $tiers; do
      [[ -d "$pool/$tier" ]] || missing+=("$tier")
    done
    (( ${#missing[@]} == 0 )) || fail "task pool tier(s) missing under $pool: ${missing[*]}"
    # Everything above ran as root, for whom every one of these paths is readable. The container
    # runs as $api_user, and for it the deciding factor is search permission on the directory the
    # bind mount lands on. Get that wrong and the API dies with "task allowlist is unavailable"
    # having found a checkout that is present, pinned and complete — which reads like a missing
    # mount and is not one. Mode bits rather than a probe container, so the preflight stays
    # offline and needs no image built yet.
    closed=$(
      find "$root" -maxdepth 0 ! -perm -o=rx
      find "$allow" -maxdepth 0 ! -perm -o=r
      find "$pool" \( -type d ! -perm -o=rx \) -o \( -type f ! -perm -o=r \)
    )
    if [[ -n "$closed" ]]; then
      echo "error: the api container runs as $api_user and cannot read:" >&2
      printf '  %s\n' $closed >&2
      echo "  The bytes are fine; the modes are not. This is the one failure that looks" >&2
      echo "  exactly like an empty mount and is not one." >&2
      echo "" >&2
      echo "    just pin-tasks" >&2
      exit 1
    fi

# Where the API reads tasks from, derived from docker-compose.api.yml itself: the
# bind mount's host path, plus the pool and allowlist paths relative to its target.
# The compose file is the single source of truth, so this cannot drift from what
# the container sees — which is exactly how a populated checkout and a starving

# container end up coexisting.
_tasks-paths:
    #!/usr/bin/env bash
    set -euo pipefail
    {{ compose }} config --format json 2>/dev/null | python3 -c '
    import json, os, sys
    api = json.load(sys.stdin)["services"]["api"]
    env = api.get("environment") or {}
    pool, allow = env.get("TASK_POOL_ROOT"), env.get("TASK_ALLOWLIST_PATH")
    if not pool or not allow:
        sys.exit("TASK_POOL_ROOT/TASK_ALLOWLIST_PATH are not set on the api service")
    for v in api.get("volumes", []):
        target = (v.get("target") or "").rstrip("/")
        if v.get("type") == "bind" and target and (pool == target or pool.startswith(target + "/")):
            # The uid the container runs as comes last, so _check-tasks can test the paths the
            # way the container will see them instead of the way root sees them.
            print(v["source"], os.path.relpath(pool, target), os.path.relpath(allow, target),
                  api.get("user") or "-")
            break
    else:
        sys.exit(f"no bind mount on the api service contains {pool}")
    '

# Containers left over from a bare `docker compose -f docker-compose.db.yml up`.
# container_name is a global Docker name, not project-scoped, so conjectures_db
# and conjectures_migrate from that project block this one from creating its own,

# and Compose fails with a name conflict that does not say why.
_check-legacy:
    #!/usr/bin/env bash
    set -euo pipefail
    stale=$(docker ps -a --filter "label=com.docker.compose.project={{ legacy }}" \
              --format '{{{{.Names}}' 2>/dev/null || true)
    [[ -n "$stale" ]] || exit 0
    echo "error: containers from the old '{{ legacy }}' project still exist:" >&2
    echo "    $(echo "$stale" | tr '\n' ' ')" >&2
    echo "  They hold the container names this stack needs, and their volume" >&2
    echo "  ({{ legacy }}_pgdata) is a SEPARATE database from this one. Remove them:" >&2
    echo "" >&2
    echo "    docker compose -f docker-compose.db.yml down -v --remove-orphans" >&2
    exit 1
