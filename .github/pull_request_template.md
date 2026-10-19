## What and why

<!-- One or two sentences. Link the issue or discussion if there is one. -->

## Target

- [ ] Topic branch → `dev` (cut from `origin/dev`)
- [ ] Release `dev` → `main` (merge commit; back-merge PR merged first)
- [ ] `hotfix/*` → `main` (cut from the current `origin/main`)

## Checks run locally

<!-- ruff, pytest (with the marker set CI uses), schema drift if the schema changed. -->

## Deploy scope

- [ ] App code only
- [ ] Migration: `deploy/migrate/sql/V___`. It works with the previous release's code (expand/contract).
- [ ] Operator steps needed (list them below). The deploy waits for an operator.

### Operator steps

<!-- e.g. "just build-verifier + just install-worker", "new .env key FOO on PROD", "restart the
     emissions worker". Write "none" if there are none. See docs/DEPLOYING.md. -->

## API consumers

<!-- Does this add, rename or remove anything in the public API? Additions ship first;
     renames and removals only once clients no longer read the old shape. Write "none" otherwise. -->

## Labels

<!-- One of: feature, fix, hotfix, migration, api, ops. Release and back-merge PRs: "changelog: skip".
     Labels group the generated release notes. -->
