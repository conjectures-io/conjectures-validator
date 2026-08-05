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
- root-owned validator and task releases, with pins matching `pins.lock.json`;
- at least 72 GiB available to a verifier container and four CPUs.

`just doctor-host` checks the kernel, Landlock, cgroup, CPU and disk gates in a second. Run it
first: none of them are optional, and the rest of this page is wasted effort if one fails.

The two releases must be **siblings**, because `docker-compose.api.yml` mounts the task
repository from a relative path and the API and the worker must resolve the same pool:

```
/opt/conjectures-validator/current/              # validator release
/opt/conjectures-validator/conjectures-tasks/    # task release at the pinned commit
```

Keep `current` a real directory rather than a symlink, or Compose resolves that relative mount
against the physical release path instead. Anything under `/root` or `/home` cannot work at all:
the unit sets `ProtectHome=true`, so systemd presents those paths to the service as empty
regardless of ownership or mode bits. If a release is already there, `sudo just relocate-release`
moves both trees and keeps them siblings; `just install-worker` refuses until it has been run.

The worker's membership in the `docker` group is privileged host access. Give that membership only
to this dedicated account. No network-facing service should run as this user.
The supplied unit denies outbound IP traffic except loopback: PostgreSQL must listen locally and
Docker must use its local Unix socket, never a remote daemon.

## Install

```bash
cd /opt/conjectures-validator/current
just doctor-host        # seconds. Stop here if it fails; nothing below can compensate.
just pin-tasks          # task release at the pinned commit, with modes the service can read
just build-verifier     # about an hour, ~72 GiB. Prints the immutable image ID.
just install-worker     # account, venv, environment file, unit, preflight
```

`just install-worker` is idempotent — re-run it after a release, an image rebuild, or a repin. It
creates the `conjectures-worker` account and its `docker` group membership, builds the root-owned
`.venv`, installs `/etc/conjectures/verification-worker.env` from the template with this host's
paths already resolved, refreshes `VERIFIER_CONTAINER_DIGEST` and `VERIFIER_VERSION` from the
image and the release commit, installs the unit with the real release path substituted in, and
runs the preflight. It deliberately does **not** start the service.

The one manual step is the secrets. On a first run the script stops and lists every remaining
placeholder in the installed environment file:

```bash
sudoedit /etc/conjectures/verification-worker.env
just install-worker     # again, once the placeholders are filled
```

The image is pinned by its **local immutable image ID**, never by tag — a tag can be moved onto
other bytes, and every verdict names the image that produced it.

`build-verifier` refuses to build a release from a dirty tree, which is the same check you can run
yourself:

```bash
git status --porcelain=v1 --untracked-files=all   # must be empty
git rev-parse HEAD                               # becomes VERIFIER_VERSION
```

The clean-check includes untracked files because Docker builds from the filesystem, not from Git's
index: an untracked file is in the image while `VERIFIER_VERSION` claims the commit alone. The
build context excludes environment files, private-key files and Bittensor wallet data; keep all
production credentials outside the release checkout regardless.

One file from the task repository enters the build — the audited Formal Conjectures patch, passed
as a named build context because that repository is a sibling of the build context and a `COPY`
cannot reach it. It is accepted only if its `sha256` matches `pins.lock.json` and the commit
derived from applying it equals the pinned `formal_conjectures` commit. No task bundles are baked
into the image; the verifier receives one task directory read-only, per proof.

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
