# Shipping a change and deploying it

This page is for everyone who changes this repository, people and coding agents alike. It
describes how a change gets from a branch to the dev server and to production. The short
version: **a merged PR is the deploy**, and git tags show what each environment runs.

The design and its reasons are in [`ci-cd/PLAN.md`](ci-cd/PLAN.md). Some steps depend on
settings that are not in place yet. They are marked **(rulesets)** (needs the branch and
tag rulesets imported), **(production)** (needs the production server and environment set up)
or **(App)** (needs the release GitHub App). Everything not marked applies today.

| Environment | Branch | Server | Deployed by | Tag |
| --- | --- | --- | --- | --- |
| Dev (testnet 577) | `dev` | dev server | a merge into `dev`, automatically | `deployed/dev` |
| Production (netuid 66) | `main` | production server | a merge into `main`, after one approval **(production)** | `deployed/production` + release `vYYYY.MM.DD[.N]` |

---

## Rules

1. **Changes reach `dev` and `main` only through pull requests with green CI.**
2. **Start work from `origin/dev`:** `git fetch origin && git switch -c <type>/<name> origin/dev`.
   If you stack on another branch, say so in the PR.
3. **Open PRs into `dev`.** Only two kinds of PR go into `main`: a `dev` → `main` release,
   and a `hotfix/*` branch.
4. **Deploys run from GitHub Actions.** The servers' checkouts are changed only by the
   deploy script. If a change needs something done on a server, describe it under
   "Operator steps" in the PR, and an operator will do it (see "Operator changes" below).
5. **Hotfixes start from the current `main`:**
   `git fetch origin && git switch -c hotfix/<name> origin/main`.
6. **Merge method.** `dev` → `main` and `main` → `dev` PRs use "Create a merge commit". Topic
   PRs into `dev` may squash.
7. **Database migrations work with the previous release's code (expand/contract).** Add a
   column and start using it in one PR. Drop the old one in a later PR, once nothing reads
   it. Applied migrations are never edited (see [`deploy/README.md`](../deploy/README.md)).
8. **The API changes by adding first.** The website and other clients build against it.
   Rename or remove a field only once its clients no longer read it, and say in the PR when
   a change touches the API.
9. **Labels drive the release notes.** Label your PR `feature`, `fix`, `migration`, `api` or
   `ops`. Release and back-merge PRs get `changelog: skip`.

## The normal path

```bash
git fetch origin
git switch -c feat/<short-name> origin/dev
# ... change, then run what CI runs:
ruff check .
pytest -q -m "not integration and not subnet_integration and not needs_checkouts"
python scripts/check_schema_drift.py --dsn <scratch-postgres-dsn>   # if the schema changed
git push -u origin feat/<short-name>
gh pr create --base dev --label feature --title "<what it does>" --body-file <filled-in PR template>
gh pr checks <number> --watch
```

- The PR body follows [`.github/pull_request_template.md`](../.github/pull_request_template.md).
  `gh pr create --body ...` does not fill in the template, so copy its headings.
- A red check blocks the merge **(rulesets)**. Fix it on the branch.
- After the merge, the dev server deploys itself. CI is not run a second time when the merged
  commit has exactly the contents your PR's green run tested. If another PR was merged into
  `dev` in between, it runs again on the combined result. Follow the run:
  `gh run list --workflow deploy.yml --branch dev --limit 3`, then `gh run watch <id>`.

## What runs where

```bash
git fetch --tags --force origin
git log -1 --format='%h %s' deployed/dev            # dev server
git log -1 --format='%h %s' deployed/production     # production
git log --oneline deployed/production..deployed/dev # on dev, not yet in production
gh release view                                     # current production release and its notes
gh release list --limit 10                          # production history
```

On a server, the running API image is labelled with its commit:
`docker inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' conjectures_api`.

## Releasing to production

1. `gh pr create --base main --head dev --label "changelog: skip" --title "Release YYYY-MM-DD"`.
2. The `release source` check is red while `main` has commits that `dev` lacks. Merge the
   open `main` → `dev` back-merge PR first. It is a required check **(rulesets)**.
3. Merge with a merge commit. The Deploy run's `plan` summary shows the scope (`app`,
   `migration`, `operator`, or `first` when production has no `deployed/production` tag yet)
   and any new migration files.
   - `app` or `migration`: the deploy waits in the `production` environment until one approver
     approves it **(production)**. Whoever started the deploy cannot approve it.
   - `operator` or `first`: nothing is deployed. An operator does the steps, then runs
     `gh workflow run deploy.yml --ref main -f environment=production -f ref=<sha> -f operator_steps_done=true`,
     which also waits for the approval.
4. Before production deploys from GitHub, whoever deploys it by hand runs
   `gh workflow run production-release.yml -f ref=<deployed 40-hex sha>` right afterwards.
   This records the deploy the same way the pipeline does.
5. After a successful deploy, the workflow moves `deployed/production` and creates the
   release `vYYYY.MM.DD` with generated notes: the PRs merged since the previous release,
   grouped by label, plus the deploy run link.
6. Agents do not approve production deploys. Give the human the run URL and the scope.

## Hotfixes

```bash
git fetch origin
git switch -c hotfix/<name> origin/main
# ... smallest possible fix, with a test ...
git push -u origin hotfix/<name>
gh pr create --base main --label hotfix --title "Hotfix: <what>"
```

Same approval and same release as a normal release. Afterwards, a bot opens the `main` →
`dev` back-merge PR **(App)**. Without the App, open it yourself:
`gh pr create --base dev --head main --label "changelog: skip" --title "Back-merge main into dev"`.

## Rollback

Production rolls back to an earlier **release tag**. Dev rolls back to an earlier commit on
`dev`:

```bash
gh release list --limit 10                                                 # pick the release
gh workflow run deploy.yml --ref main -f environment=production -f ref=v2026.10.18   # (production)
gh workflow run deploy.yml --ref dev -f environment=dev -f ref=<40-hex sha on dev>
```

A production rollback still needs the approval. It moves `deployed/production` and marks
that release "Latest". It creates no new release. Migrations are not rolled back, which is
why rule 7 exists.

## Configuration (.env)

Each server's `.env` is kept in GitHub as the `DOTENV` secret of its environment (`dev`,
later `production`). It is installed on every deploy. Setup, and how to copy it from the
server, are in [`deploy/ci/README.md`](../deploy/ci/README.md#the-env-from-github).

- To change a value, update the secret (the whole file), then redeploy:
  `gh workflow run deploy.yml --ref dev -f environment=dev`.
- An edit made on the server is overwritten by the next deploy. The server keeps every
  previous `.env` in its backups.
- A PR that adds a key to `.env.example` lists it under "Operator steps". The deploy summary
  shows new keys too.

## Operator changes

An automatic deploy never makes the following changes. If a PR needs one, list it under
"Operator steps", and the production deploy waits for an operator **(production)**:

- changes under `verifier/`, `verification_worker/`, `lean/`, `Dockerfile`,
  `docker-compose.yml`, `deploy/worker/`, `deploy/db/` or `docker-compose.db.yml`. These
  need `just build-verifier` (about an hour, 72 GiB) and/or `just install-worker`;
- new or changed keys in `.env.example` that a server must set. The deploy reports them;
  someone with access to the environment's `DOTENV` secret adds them (see "Configuration");
- starting, stopping or reconfiguring a process that signs or writes to the chain or the
  ledgers: the emissions worker, the deposit watcher, the registration watcher, and any weight
  setter. There must be exactly one `set_weights` caller per hotkey;
- nginx, systemd units, backups and restores.

## What an agent may and may not do

May:
- create branches from `origin/dev` (or from `origin/main` for `hotfix/*`), commit, push
  them, and open labelled PRs;
- read CI and deploy runs (`gh pr checks`, `gh run list`, `gh run view --log-failed`), tags
  and releases;
- dispatch a **dev** redeploy or rollback when a human asks for it.

May not:
- push to `dev` or `main`, merge its own PR unless a human asks it to, or approve a
  production deploy;
- connect to the dev server or the production server to check out, migrate, restart or edit
  files;
- create, move or delete `v*` or `deployed/*` tags, or create or edit releases by hand;
- read, set or print the `DOTENV` secret or any server's `.env`;
- change `.github/`, the justfile's deploy recipes or `deploy/` without stating it in the
  PR (CODEOWNERS for them is planned), add `pull_request_target` to a workflow, or put
  secrets in any file.
