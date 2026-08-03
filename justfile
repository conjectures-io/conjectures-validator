# The one entrypoint for the operational stack: Postgres, Flyway, the API, and
# optionally the verification worker.
#
#   just up            # db -> migrate -> api
#   just up-worker     # ... and the verification worker
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
    {{compose}} up -d --build
    @{{compose}} ps

# Build and start the whole stack including the verification worker.
up-worker: _preflight
    @echo "==> starting: db -> migrate -> api -> worker (as {{uid}}:{{gid}})"
    DOCKER_UID={{uid}} DOCKER_GID={{gid}} {{compose_worker}} up -d --build
    @DOCKER_UID={{uid}} DOCKER_GID={{gid}} {{compose_worker}} ps

# Stop and remove containers. The database volume SURVIVES.
down:
    DOCKER_UID={{uid}} DOCKER_GID={{gid}} {{compose_worker}} down

# Recreate containers without rebuilding images.
restart:
    DOCKER_UID={{uid}} DOCKER_GID={{gid}} {{compose_worker}} up -d --force-recreate

# Build images without starting anything.
build:
    DOCKER_UID={{uid}} DOCKER_GID={{gid}} {{compose_worker}} build

# Destroy the database volume and bring the stack back up empty.
[confirm("This DESTROYS the database volume and every row in it. Continue?")]
reset: _check-env
    @echo "==> removing containers and the database volume"
    DOCKER_UID={{uid}} DOCKER_GID={{gid}} {{compose_worker}} down -v --remove-orphans
    @echo "==> starting clean — every migration re-applies from V001"
    {{compose}} up -d --build
    @{{compose}} ps

# --- inspection --------------------------------------------------------------

# Show what is running.
ps:
    @DOCKER_UID={{uid}} DOCKER_GID={{gid}} {{compose_worker}} ps

# Follow logs, all services or one: `just logs api`
logs *service:
    DOCKER_UID={{uid}} DOCKER_GID={{gid}} {{compose_worker}} logs -f --tail=200 "$@"

# Apply pending migrations only, then exit.
migrate: _preflight
    {{compose}} up --exit-code-from migrate migrate

# Open a psql shell on the database.
psql:
    docker exec -it conjectures_db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'

# Hit the API's liveness and readiness endpoints.
health:
    @curl -fsS "http://127.0.0.1:${API_PORT:-8000}/healthz" && echo " <- healthz"
    @curl -fsS "http://127.0.0.1:${API_PORT:-8000}/readyz"  && echo " <- readyz"

# Run the preflight checks and report, changing nothing.
doctor: _preflight
    @echo "==> project: {{project}}   volume: {{project}}_pgdata"
    @echo "==> worker would run as {{uid}}:{{gid}}"
    @echo "==> all checks passed"

# --- private ------------------------------------------------------------------

_preflight: _check-docker _check-env _check-legacy

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
      for var in PUBLIC_CURSOR_SECRET PUBLIC_ACTIVITY_SALT WEBSITE_BASE_URL MAIL_SENDER; do
        grep -qE "^${var}=.+" .env || missing+=("$var")
      done
    fi
    if (( ${#missing[@]} )); then
      echo "error: unset or empty in .env: ${missing[*]}" >&2
      echo "  See .env.example for what each one is and how to generate it." >&2
      exit 1
    fi

# Containers left over from a bare `docker compose -f docker-compose.db.yml up`.
# container_name is a global Docker name, not project-scoped, so conjectures_db
# and conjectures_migrate from that project block this one from creating its own,
# and Compose fails with a name conflict that does not say why.
_check-legacy:
    #!/usr/bin/env bash
    set -euo pipefail
    stale=$(docker ps -a --filter "label=com.docker.compose.project={{legacy}}" \
              --format '{{{{.Names}}' 2>/dev/null || true)
    [[ -n "$stale" ]] || exit 0
    echo "error: containers from the old '{{legacy}}' project still exist:" >&2
    echo "    $(echo "$stale" | tr '\n' ' ')" >&2
    echo "  They hold the container names this stack needs, and their volume" >&2
    echo "  ({{legacy}}_pgdata) is a SEPARATE database from this one. Remove them:" >&2
    echo "" >&2
    echo "    docker compose -f docker-compose.db.yml down -v --remove-orphans" >&2
    exit 1
