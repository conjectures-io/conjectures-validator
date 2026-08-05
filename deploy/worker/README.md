# Production verification worker

The production worker runs directly on the validator host under systemd. It holds the database
credential and launches one fresh, networkless verifier container for each proof. The verifier
container receives one read-only task directory and one read-only `Main.lean`; it never receives
the database, task pool, Docker socket, or validator wallet.

Do not adapt `docker-compose.worker.yml` for production. That file deliberately runs the insecure
development path. Putting the production worker inside a container would require Docker daemon
access and would make its per-proof temporary bind paths invisible to the host daemon. The host
service keeps the boundary explicit and lets Docker mount the exact host paths the worker names.

## Host prerequisites

- Ubuntu with a kernel that passes the verifier's Landlock ABI 4+ and seccomp behavioral probe;
- Docker Engine with the reviewed verifier image already built or pulled;
- PostgreSQL/Flyway stack running on loopback with all migrations applied;
- root-owned validator and task releases at `/opt/conjectures-validator/current` and
  `/opt/conjectures-tasks`, with pins matching `pins.lock.json`;
- at least 72 GiB available to a verifier container and four CPUs.

The worker's membership in the `docker` group is privileged host access. Give that membership only
to this dedicated account. No network-facing service should run as this user.
The supplied unit denies outbound IP traffic except loopback: PostgreSQL must listen locally and
Docker must use its local Unix socket, never a remote daemon.

## Install

Create the service account and Python environment:

```bash
sudo useradd --system --home-dir /var/lib/conjectures-worker \
  --create-home --shell /usr/sbin/nologin conjectures-worker
sudo usermod --append --groups docker conjectures-worker

cd /opt/conjectures-validator/current
sudo python3 -m venv .venv
sudo .venv/bin/pip install --constraint requirements-service.lock \
  SQLAlchemy greenlet 'psycopg[binary]'
sudo chown -R root:root .venv
```

Build or pull the reviewed verifier image, then record its local immutable image ID:

```bash
test -z "$(git status --porcelain=v1 --untracked-files=all)"
git rev-parse HEAD
sudo docker build --tag formal-conjectures-verifier:release .
sudo docker image inspect --format '{{.Id}}' formal-conjectures-verifier:release
```

The clean-check includes untracked files because Docker builds from the filesystem, not from Git's
index. The build context excludes environment files, private-key files, and Bittensor wallet data;
keep all production credentials outside the release checkout regardless.

Install the environment template, replace every placeholder in the installed copy, and set the
digest to that exact `sha256:...` image ID. Then install the unit:

```bash
sudo install -d -o root -g conjectures-worker -m 0750 /etc/conjectures
sudo install -o root -g conjectures-worker -m 0640 \
  deploy/worker/verification-worker.env.example \
  /etc/conjectures/verification-worker.env
sudoedit /etc/conjectures/verification-worker.env
sudo install -o root -g root -m 0644 \
  deploy/worker/conjectures-verification-worker.service \
  /etc/systemd/system/conjectures-verification-worker.service
sudo systemctl daemon-reload
```

## Required preflight

The unit will not start unless all of these pass together:

1. production settings specify the container runner, database URL, release version, and image ID;
2. the live task pool matches the audited allowlist, task commits, and bundle digests;
3. the configured image tag still resolves to the configured image ID;
4. that exact image passes its pin, toolchain, Landlock, seccomp, and non-root doctor probes;
5. the worker can connect to PostgreSQL;
6. every completed report matches the claimed task and stored proof, passes the strict production
   acceptance contract, and is written only while this process still owns a live database lease.

Run the same non-mutating check manually through systemd, then start the worker:

```bash
sudo systemctl start conjectures-verification-worker
sudo systemctl status conjectures-verification-worker --no-pager
sudo journalctl -u conjectures-verification-worker -n 100 --no-pager
sudo systemctl enable conjectures-verification-worker
```

`ExecStartPre` performs the check before every start and does not claim a submission. A deliberate
configuration refusal exits 2 and is not restart-looped. Runtime failures restart after ten
seconds. Shutdown allows up to 75 minutes for an in-flight proof to finish before systemd kills the
process group.

## Release and rollback

Build the verifier image from the same reviewed release commit, deploy by immutable image ID, and
set `VERIFIER_VERSION` to that release identity. To update, stop submissions, drain the queue,
install the new validator/task releases and image, update the environment file, run the preflight,
then restart. Keep the previous root-owned release and image ID until the first production proof
has completed. Rollback is switching `current`, the task release, version, image tag and image ID
back as one unit while submissions remain paused.
