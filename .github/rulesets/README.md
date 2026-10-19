# Rulesets

Four rulesets enforce the release process. They are kept here as JSON, the format GitHub
imports. They take effect only once they are created in the repository settings.

| File | Applies to | Rules |
| --- | --- | --- |
| [`dev.json`](dev.json) | branch `dev` | changes only through a PR; required checks `ruff`, `pytest`, `schema drift`, `Collatz statement checks`; merge or squash; no force-push, no deletion |
| [`main.json`](main.json) | branch `main` | the same, plus the required check `release source` (only `dev` or `hotfix/*`, and only while `dev` has everything `main` has); merge commits only |
| [`release-tags.json`](release-tags.json) | tags `v*` (one per production release) | restrict creation, update and deletion |
| [`deployed-tags.json`](deployed-tags.json) | tags `deployed/dev`, `deployed/production` | restrict creation, update and deletion |

The branch rulesets ask for no approving review on the PR. Production's approval lives on the
`production` environment instead, so a release takes one click, not two. Raise
`required_approving_review_count` in `dev.json` or `main.json` if PRs should need a review too.

The tag rulesets' bypass list starts empty, so nobody can create or move those tags. A
**GitHub App** is then added as the only bypass actor. The workflows write the tags with that
App's token:
- `deploy.yml` moves `deployed/dev`;
- `production-release.yml` moves `deployed/production` and publishes the release;
- `back-merge.yml` opens the `main` → `dev` PR.

The App is needed because the workflows' built-in `GITHUB_TOKEN` cannot be added to a
ruleset's bypass list (GitHub Apps can), and because a PR opened with `GITHUB_TOKEN` never runs
CI.

## Setup, in this order

The order matters. With a ruleset active and no App configured, the workflows fall back to
`GITHUB_TOKEN`, and the tag step fails.

1. **Create the App.** Organization settings → Developer settings → GitHub Apps → New GitHub
   App.
   - Name: e.g. `conjectures-release`.
   - Homepage: the repository URL.
   - Webhook: off.
   - Repository permissions: **Contents: Read and write** (tags, releases), **Pull requests:
     Read and write** (the back-merge PR). Metadata: read (the default).
   - "Only on this account".

   Create it, note the **App ID**, then generate a private key (a `.pem` download).
2. **Install it** on `conjectures-io/conjectures-validator` only (the App's page → Install
   App).
3. **Give the workflows its credentials.** Repository settings → Secrets and variables →
   Actions:
   - variable `RELEASE_APP_ID` = the App ID;
   - secret `RELEASE_APP_KEY` = the whole `.pem` file.

   Then delete the `.pem` from your machine.
4. **Create the rulesets.**
   - In the UI: Repository settings → Rules → Rulesets → New ruleset → **Import a ruleset**,
     once per JSON file. Then open each *tag* ruleset → Bypass list → Add bypass → the App →
     "Always allow" → Save. The branch rulesets need no bypass.
   - Or, in one go, with the App already in the tag rulesets' bypass list:

     ```bash
     APP_ID=<the App ID>
     REPO=conjectures-io/conjectures-validator
     for f in .github/rulesets/release-tags.json .github/rulesets/deployed-tags.json; do
       jq --argjson id "$APP_ID" \
         '.bypass_actors = [{"actor_id": $id, "actor_type": "Integration", "bypass_mode": "always"}]' "$f" \
         | gh api -X POST "repos/$REPO/rulesets" --input -
     done
     for f in .github/rulesets/dev.json .github/rulesets/main.json; do
       gh api -X POST "repos/$REPO/rulesets" --input "$f"
     done
     ```

   The required checks are tied to GitHub Actions (`integration_id` 15368), so a status with
   the same name posted by anything else does not satisfy them.
5. **Check it.** A tag push from a laptop is refused, and the next deploy still moves
   `deployed/dev`:

   ```bash
   git tag v1999.01.01 && git push origin v1999.01.01   # refused by the ruleset
   git tag -d v1999.01.01
   ```

To change a ruleset later, edit it in the settings and export it back over the JSON here, so
the file stays the record of what is configured.
