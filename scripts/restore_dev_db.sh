#!/usr/bin/env bash
# =============================================================================
# restore_dev_db.sh — put a production dump onto THIS development database.
#
#   just restore-db                              # newest *.dump in the repo root
#   just restore-db conjecture_backup_...dump
#   scripts/restore_dev_db.sh path/to.dump --yes
#
# What goes wrong when this is done by hand, and what this script does instead:
#
#   1. `docker compose -f docker-compose.db.yml down -v` removes the volume of
#      the LEGACY `conjectures-db` project, not the one the API uses
#      (`conjectures-api_pgdata`). The old data is still there afterwards and
#      every symptom that prompted the reset survives. Nothing here removes a
#      volume at all: the database is dropped and recreated inside the cluster,
#      which is both faster and keeps the cluster-level roles (`monitor`,
#      `conjectures_reviewer`) that a dump's GRANTs need in order to restore.
#
#   2. `pg_restore` into a database that still has content dies on the first
#      object: `ERROR: schema "autoreview" already exists`. The target here is
#      always a freshly created, empty database.
#
#   3. The API, worker, watcher and notifier hold connections, so a DROP would
#      fail. They are stopped first and started again at the end — exactly the
#      set that was running, nothing more.
#
#   4. A Flyway checksum mismatch ("Applied to database: X / Resolved locally:
#      Y") blocks `just migrate` after the restore whenever production applied a
#      different text of a migration than this checkout holds. `flyway repair`
#      is the fix; it rewrites the history table's checksums and touches no
#      data. This runs it automatically and only on that specific failure.
#
# The database-level settings and extensions from deploy/db/00_init.sh are
# reapplied after the restore: they live on the database object, so dropping it
# drops them, and they run only on first cluster init otherwise. That script is
# idempotent, which is why it can simply be run again.
#
# Refuses to run when .env says APP_MODE=PROD. This destroys the target.
# =============================================================================
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# docker-compose.api.yml MUST stay first — it `include`s the database stack and
# owns the project name. Passing docker-compose.db.yml first instead addresses a
# different project with a different, empty volume. See the justfile header.
compose=(docker compose -f docker-compose.api.yml)
project=conjectures-api
backup_dir=.work/db-restore

die()  { echo "error: $*" >&2; exit 1; }
step() { echo; echo "==> $*"; }

usage() {
  # The header block above, without its rules and without the leading '# '.
  awk 'NR > 1 && /^#/ { sub(/^# ?/, ""); if ($0 !~ /^=+$/) print; next } NR > 1 { exit }' \
    "${BASH_SOURCE[0]}"
  cat <<'USAGE'

options:
  -y, --yes        do not ask for confirmation
      --no-backup  skip the safety dump of the current dev database
      --no-migrate do not run pending migrations after the restore
      --no-repair  do not run `flyway repair` on a checksum mismatch
      --strict     fail instead of retrying without owners/grants
      --force      proceed even when .env says APP_MODE=PROD
USAGE
}

# --- arguments ---------------------------------------------------------------

dump=""
assume_yes=0; do_backup=1; do_migrate=1; do_repair=1; strict=0; force=0

while (( $# )); do
  case "$1" in
    -y|--yes)     assume_yes=1 ;;
    --no-backup)  do_backup=0 ;;
    --no-migrate) do_migrate=0 ;;
    --no-repair)  do_repair=0 ;;
    --strict)     strict=1 ;;
    --force)      force=1 ;;
    -h|--help)    usage; exit 0 ;;
    --)           ;;
    -*)           die "unknown option: $1 (try --help)" ;;
    *)            [[ -z "$dump" ]] || die "more than one dump given: $dump and $1"
                  dump="$1" ;;
  esac
  shift
done

# --- preconditions -----------------------------------------------------------

command -v docker >/dev/null || die "docker is not on PATH"
docker info >/dev/null 2>&1  || die "cannot reach the Docker daemon"
[[ -f .env ]] || die ".env is missing — cp .env.example .env"

mode=$(grep -E '^APP_MODE=' .env | tail -1 | cut -d= -f2- | tr '[:lower:]' '[:upper:]' || true)
if [[ "$mode" == "PROD" && $force -eq 0 ]]; then
  die ".env says APP_MODE=PROD. This DESTROYS the database it is pointed at.
  If this really is a development host with a production-shaped .env, pass --force."
fi

# The newest dump in the repo root, when none was named. Backups this script
# takes live under .work/ and so are never candidates.
if [[ -z "$dump" ]]; then
  dump=$(ls -t ./*.dump ./*.dump.gz ./*.sql.gz 2>/dev/null | head -1 || true)
  [[ -n "$dump" ]] || die "no dump given and no *.dump in the repo root.
  usage: scripts/restore_dev_db.sh <dump file>"
  echo "==> no dump named; using the newest one here: $dump"
fi
[[ -f "$dump" ]] || die "no such file: $dump"

# Decide how to feed it to Postgres from the file's own bytes rather than its
# extension, which is routinely wrong on a file that has been copied by hand.
# .work/ is gitignored, so a safety dump left there can never be committed. It is also
# routinely created by a container running as root; falling back keeps a permissions
# problem on a directory we only use for scratch from stopping the restore.
if ! mkdir -p "$backup_dir" 2>/dev/null || [[ ! -w "$backup_dir" ]]; then
  backup_dir=$(mktemp -d -t conjectures-restore-XXXXXX)
  echo "==> $PWD/.work/db-restore is not writable; keeping the safety dump in $backup_dir"
fi
magic=$(head -c 5 "$dump" | tr -d '\0')
if [[ "$(head -c 2 "$dump" | od -An -tx1 | tr -d ' ')" == "1f8b" ]]; then
  step "decompressing $dump"
  plain="$backup_dir/$(basename "${dump%.gz}")"
  gzip -dc "$dump" > "$plain"
  dump="$plain"
  magic=$(head -c 5 "$dump" | tr -d '\0')
fi
if [[ "$magic" == "PGDMP" ]]; then
  format=custom
else
  format=plain
  head -c 4096 "$dump" | grep -qiE 'postgresql database dump|^(SET|CREATE|--)' \
    || die "$dump is neither a pg_dump archive (PGDMP) nor SQL text.
  Take a restorable dump with:  pg_dump -Fc -d <db> -f backup.dump"
fi

# --- the database container --------------------------------------------------

started_db=0
db=$("${compose[@]}" ps -q db 2>/dev/null || true)
if [[ -z "$db" ]]; then
  step "starting the database"
  "${compose[@]}" up -d db
  db=$("${compose[@]}" ps -q db)
  started_db=1
fi
[[ -n "$db" ]] || die "the db service did not start"

for _ in $(seq 1 60); do
  docker exec "$db" sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1 && break
  sleep 2
done
docker exec "$db" sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1 \
  || die "the database never became ready — check: just logs db"

db_name=$(docker inspect -f '{{.Name}}' "$db" | sed 's#^/##')
pg_user=$(docker exec "$db" printenv POSTGRES_USER)
pg_db=$(docker exec "$db" printenv POSTGRES_DB)

# Every psql/pg_dump call reads the password from the container's own
# environment, so it is never an argument on this host and never in `ps`.
psql_in() { # $1 = database, SQL on stdin
  docker exec -i "$db" sh -c \
    'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -v ON_ERROR_STOP=1 -qtAX -h 127.0.0.1 -U "$POSTGRES_USER" -d "$1"' \
    _ "$1"
}

# --- confirm -----------------------------------------------------------------

size=$(du -h "$dump" | cut -f1)
cat <<EOF

  restore     $dump  ($size, $format format)
  into        database "$pg_db" on container $db_name
  project     $project

  The database is DROPPED and recreated. Every row in it now is gone.
EOF
# Leaving a database container running that was not running when we arrived would be a
# side effect of *declining*, so undo it on any of the ways out of here.
abandon() {
  if (( started_db )); then
    echo "    (stopping the database again — it was not running before)"
    docker stop "$db" >/dev/null || true
  fi
  echo "aborted"
  exit 1
}
if (( ! assume_yes )); then
  [[ -t 0 ]] || { echo; echo "error: not a terminal — pass --yes to run unattended" >&2; abandon; }
  read -rp "  Continue? [y/N] " reply
  [[ "$reply" == [yY] || "$reply" == [yY][eE][sS] ]] || abandon
fi

# --- stop whatever is holding a connection -----------------------------------

mapfile -t stopped < <(
  docker ps --filter "label=com.docker.compose.project=$project" --format '{{.Names}}' \
    | grep -vx "$db_name" || true
)
if (( ${#stopped[@]} )); then
  step "stopping ${#stopped[@]} container(s) that talk to the database"
  printf '    %s\n' "${stopped[@]}"
  docker stop "${stopped[@]}" >/dev/null
fi

restart_stopped() {
  if (( ${#stopped[@]} )); then
    step "starting them again"
    docker start "${stopped[@]}" >/dev/null || true
  fi
}
trap restart_stopped EXIT

# --- safety dump of what is about to be destroyed ----------------------------

if (( do_backup )); then
  safety="$backup_dir/dev_before_restore_$(date +%Y-%m-%d_%H-%M-%S).dump"
  step "backing up the current dev database first"
  if docker exec "$db" sh -c \
       'PGPASSWORD="$POSTGRES_PASSWORD" exec pg_dump -Fc -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /tmp/pre_restore.dump' \
     && docker cp "$db":/tmp/pre_restore.dump "$safety" >/dev/null; then
    docker exec "$db" rm -f /tmp/pre_restore.dump || true
    echo "    $safety"
  else
    # An unrestorable or half-broken database is exactly the state this script
    # is usually reached from. Not being able to dump it is not a reason to stop.
    echo "    could not dump the current database — continuing anyway"
  fi
fi

# --- drop and recreate -------------------------------------------------------

step "recreating database \"$pg_db\""
# WITH (FORCE) terminates the remaining backends itself (PostgreSQL 13+), which
# is what makes this reliable without hunting pids in pg_stat_activity.
psql_in postgres <<EOF
DROP DATABASE IF EXISTS "$pg_db" WITH (FORCE);
CREATE DATABASE "$pg_db" OWNER "$pg_user";
EOF

# --- restore -----------------------------------------------------------------

remote=/tmp/restore_$$.dump
docker cp "$dump" "$db":"$remote" >/dev/null
cleanup_remote() { docker exec "$db" rm -f "$remote" >/dev/null 2>&1 || true; restart_stopped; }
trap cleanup_remote EXIT

restore_custom() { # $* = extra pg_restore flags
  docker exec -i "$db" sh -c \
    'PGPASSWORD="$POSTGRES_PASSWORD" exec pg_restore --single-transaction -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$@"' \
    _ "$@" "$remote"
}

step "restoring"
if [[ "$format" == custom ]]; then
  if ! restore_custom; then
    # --single-transaction rolled the whole thing back, so the database is empty
    # and clean and a second attempt starts from the same place. The usual cause
    # is a role named in the dump's GRANTs or ownership that this cluster has
    # never heard of; dropping both is the right trade on a development box.
    if (( strict )); then die "restore failed (see above); --strict, so not retrying"; fi
    step "retrying without owners and grants (--no-owner --no-acl)"
    restore_custom --no-owner --no-acl \
      || die "restore failed even without owners and grants — see the output above"
  fi
else
  docker exec -i "$db" sh -c \
    'PGPASSWORD="$POSTGRES_PASSWORD" exec psql -v ON_ERROR_STOP=1 -X --single-transaction -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f "$1"' \
    _ "$remote" >/dev/null \
    || die "restore failed — see the output above"
fi

# --- reapply what lives on the database object, not in the dump --------------

init=/docker-entrypoint-initdb.d/00_init.sh
if docker exec "$db" test -f "$init"; then
  step "reapplying deploy/db/00_init.sh (extensions, timeouts, roles)"
  # PGHOST forces TCP: this runs as whatever uid `docker exec` gives us, and the
  # unix socket would be peer-authenticated against a role that does not exist.
  docker exec "$db" sh -c 'PGHOST=127.0.0.1 PGPASSWORD="$POSTGRES_PASSWORD" exec bash "$1"' _ "$init" \
    || echo "    00_init.sh reported a problem — the data is restored regardless"
else
  echo "    (00_init.sh is not mounted; skipping database settings and roles)"
fi

psql_in "$pg_db" <<<'ANALYZE;' >/dev/null || true

# --- migrations --------------------------------------------------------------

flyway_repair() {
  # `run` generates its own container name, so the exited conjectures_migrate holding the
  # service's container_name is not in the way, and --no-deps keeps it from restarting the
  # database underneath us. Same service as `migrate`, so the connection settings and the
  # statement_timeout escape in FLYWAY_INIT_SQL are the ones the compose file already defines.
  "${compose[@]}" run --rm --no-deps migrate repair
}

if (( do_migrate )); then
  step "applying pending migrations"
  log=$(mktemp)
  if ! "${compose[@]}" up --exit-code-from migrate migrate 2>&1 | tee "$log"; then
    if (( do_repair )) && grep -qi 'checksum mismatch' "$log"; then
      step "checksum mismatch: the dump's history disagrees with this checkout"
      echo "    Running \`flyway repair\` — it rewrites flyway_schema_history only."
      if flyway_repair; then
        step "migrating again"
        "${compose[@]}" up --exit-code-from migrate migrate \
          || die "migrations still fail after repair — read the output above"
      else
        die "flyway repair failed — read the output above, or run it again with:
    just repair"
      fi
    else
      die "migrations failed — read the output above"
    fi
  fi
  rm -f "$log"
fi

# --- what landed -------------------------------------------------------------

step "restored"
# Queried separately and only when the table is there: a statement that merely
# NAMES a missing relation fails at parse time, WHERE clause or not, and would
# take the whole summary down with it.
if [[ "$(psql_in "$pg_db" <<<"SELECT to_regclass('public.flyway_schema_history') IS NOT NULL")" == t ]]; then
  psql_in "$pg_db" <<<"SELECT 'flyway version: ' || coalesce(max(version), '(none applied)') FROM flyway_schema_history WHERE success" \
    | sed 's/^/    /'
else
  echo "    flyway version: (no history table)"
fi

psql_in "$pg_db" <<'EOF' | sed 's/^/    /'
SELECT 'schemas: ' || string_agg(nspname, ', ' ORDER BY nspname)
FROM pg_namespace
WHERE nspname NOT LIKE 'pg\_%' AND nspname <> 'information_schema';

SELECT rpad(n.nspname || '.' || c.relname, 44) || ' ~' || greatest(c.reltuples, 0)::bigint || ' rows'
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r' AND n.nspname NOT IN ('pg_catalog', 'information_schema')
ORDER BY c.reltuples DESC
LIMIT 8;
EOF
