# Deploys from GitHub Actions: server and GitHub setup

`.github/workflows/deploy.yml` deploys `dev` to the dev server after every merge, and moves the
tag `deployed/dev` to the commit it deployed. This page sets that up: once on the server, and
once in GitHub. The design is in [`docs/ci-cd/PLAN.md`](../../docs/ci-cd/PLAN.md). The
day-to-day rules are in [`docs/DEPLOYING.md`](../../docs/DEPLOYING.md).

```
GitHub Actions --ssh, key with a forced command--> deploy@server
                                                   └─ sudo conjectures-deploy validator
                                                      ├─ reads /etc/conjectures/deploy-validator.env
                                                      ├─ refuses a dirty checkout or a commit not on origin/dev
                                                      ├─ installs the .env from GitHub (if allowed)
                                                      ├─ just doctor, pin-tasks if needed, DB dump if migrations changed
                                                      ├─ just deploy-up $DEPLOY_SERVICES
                                                      └─ waits for /readyz + the image's commit label; on failure restores
```

| File | Installed as |
| --- | --- |
| [`conjectures-deploy`](conjectures-deploy) | `/usr/local/sbin/conjectures-deploy` |
| [`deploy-validator.env.example`](deploy-validator.env.example) | `/etc/conjectures/deploy-validator.env` |

The installed script is a copy, so a change to it in the repository reaches the server only
when an operator reinstalls it. A deploy cannot replace the program that runs deploys.

## On the server (once)

Run as root, from the release checkout (`/opt/conjectures-validator/current`).

```bash
# 1. The account the key logs in as. It needs a real shell: sshd runs the forced command through it.
adduser --system --group --shell /bin/bash --home /home/deploy deploy

# 2. The script and its config.
install -m 755 -o root -g root deploy/ci/conjectures-deploy /usr/local/sbin/conjectures-deploy
install -d -m 755 /etc/conjectures
install -m 600 -o root -g root deploy/ci/deploy-validator.env.example /etc/conjectures/deploy-validator.env
${EDITOR:-nano} /etc/conjectures/deploy-validator.env      # DEPLOY_SERVICES, BRANCH, ALLOW_ENV_FROM_GITHUB

# 3. sudo for exactly that command. env_keep matters: without it sudo drops
#    SSH_ORIGINAL_COMMAND and every deploy is refused.
cat > /etc/sudoers.d/conjectures-deploy <<'EOF'
Defaults:deploy env_keep += "SSH_ORIGINAL_COMMAND"
deploy ALL=(root) NOPASSWD: /usr/local/sbin/conjectures-deploy validator
EOF
chmod 440 /etc/sudoers.d/conjectures-deploy && visudo -c

# 4. The checkout must be clean, root-owned, with origin on GitHub over https.
git -C /opt/conjectures-validator/current remote -v
git -C /opt/conjectures-validator/current status --short     # must print nothing
```

If sshd limits logins with `AllowUsers` or `AllowGroups`, add `deploy` there and reload sshd.

### The key

Generate it on a workstation, not on the server, and keep the private half only in GitHub:

```bash
ssh-keygen -t ed25519 -N '' -C gha-validator-dev -f ./gha-validator-dev
```

On the server, append the public half with the forced command. `restrict` disables the PTY,
port, agent and X11 forwarding:

```bash
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
printf 'restrict,command="sudo -n /usr/local/sbin/conjectures-deploy validator" %s\n' \
  "$(cat gha-validator-dev.pub)" >> /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys && chmod 600 /home/deploy/.ssh/authorized_keys
```

Check it from the workstation. It should print the deployed commit, and nothing else should
work:

```bash
ssh -i ./gha-validator-dev deploy@<server> status     # -> a 40-character SHA
ssh -i ./gha-validator-dev deploy@<server> id         # -> refused: only 'status', ...
```

The server's host key, for `DEPLOY_KNOWN_HOSTS`:

```bash
ssh-keyscan -t ed25519 <server>                       # on the workstation
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub      # on the server: the fingerprints must match
```

## In GitHub (once)

Settings → Environments → **New environment** → `dev`:

- **Deployment branches and tags:** "Selected branches and tags", add `dev`. A run from any
  other ref cannot reach these secrets.
- **Environment secrets:**

  | Secret | Value |
  | --- | --- |
  | `DEPLOY_SSH_KEY` | the private key, `gha-validator-dev` (whole file) |
  | `DEPLOY_HOST` | the server's address |
  | `DEPLOY_KNOWN_HOSTS` | the `ssh-keyscan` line |
  | `DOTENV` | optional: the server's whole `.env` (next section) |

- **Environment variables (optional):** `DEPLOY_USER` (default `deploy`), and `DEPLOY_URL`,
  the link shown on the deployment.

Then delete `gha-validator-dev` and `gha-validator-dev.pub` from the workstation.

## The .env from GitHub

Yes, the server's `.env` can live in GitHub: the whole file goes into **one** environment
secret, `DOTENV`. On every deploy the runner sends it to the server over the same SSH
connection, and the script installs it.

Copy it from the server straight into the secret, without saving it anywhere in between:

```bash
ssh <you>@<server> 'sudo cat /opt/conjectures-validator/current/.env' \
  | gh secret set DOTENV --env dev --repo conjectures-io/conjectures-validator
```

Or paste it by hand: Settings → Environments → `dev` → Add secret → `DOTENV`.

What the server does with it:

- It accepts it only if `/etc/conjectures/deploy-validator.env` has `ALLOW_ENV_FROM_GITHUB=1`.
  With `0`, the server keeps its own `.env`, and a deploy that sends one is refused.
- It checks the file before touching anything: at most 64 KiB, and every line is
  `KEY=value`, a comment or blank. CRLF line endings are converted. A bad line is reported
  by its number, never shown.
- If the file differs from the current `.env`, the old one is saved to
  `$BACKUP_DIR/env/<utc>-before-<sha>.env` (mode 600). The log shows which **key names**
  were added, removed or changed, never the values. Owner and mode stay as they were.
- If the deploy fails, the previous commit **and** the previous `.env` are restored.
- After the deploy, `just env-sync check` lists keys that `.env.example` has and the
  `.env` lacks.

Things to know:

- **GitHub becomes the source of truth.** An edit made on the server is overwritten by the
  next deploy. To change a value, update the secret, then redeploy.
- **Secrets are write-only.** GitHub never shows the value again. To change one key, paste
  the whole file again, with the change. Keep the canonical copy where you keep other
  credentials, or copy it from the server as above. The server's backups hold every
  previous version.
- **Changing the secret does not deploy anything.** Apply it with a redeploy:
  `gh workflow run deploy.yml --ref dev`. Manual runs appear once `deploy.yml` is also on
  `main`, because GitHub lists `workflow_dispatch` workflows from the default branch. Until
  then, re-run the latest Deploy run, which reads the secret's current value.
- **Size.** A secret holds at most 48 KB. The `.env` is well below that.
- **Logs are public.** This repository is public, and so are its Actions logs. The workflow
  never prints the secret, and GitHub masks it if something tries. The server script prints
  key names only.
- **Not covered:** `/etc/conjectures/verification-worker.env` (the production verification
  worker) and the competition repositories' own `.env` files. Those stay operator-managed.

## Day to day

```bash
git fetch --tags --force && git log -1 --format='%h %s' deployed/dev   # what the dev server runs
gh run list --workflow deploy.yml --limit 5                            # recent deploys
gh workflow run deploy.yml --ref dev -f sha=<commit on dev>            # redeploy / roll back
```

On the server, `/var/log/conjectures-deploy-validator.log` has one line per deploy:
`time sha run-id ok|failed|restored|restore-failed`.

To stop all deploys at once, remove the key's line from `/home/deploy/.ssh/authorized_keys`,
or disable the workflow (Actions → Deploy → "Disable workflow").
