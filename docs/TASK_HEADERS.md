# Trusted task headers

Adapter version 2 adds `import Mathlib` to the trusted Challenge and SolutionHeader files.
Adapter version 1 retains its exact original bytes. Submitted proofs still cannot import
modules, and static policy, permitted axioms, Comparator and kernel checks are unchanged.

The adapter version is separate from the task manifest schema version. Both legacy bundles
and the current immutable version registry can reconstruct headers under adapters 1 and 2.
Header bytes and the adapter version are committed in each task ID and bundle digest.

## Compatibility

Loading this validator does not regenerate tasks. Ordinary incremental publication preserves
the header adapter of each active version. New registry tasks default to adapter 1 until an
explicit upgrade is selected; legacy standalone generation defaults to adapter 2. A publication
with `--adapter-version 2` builds new committed versions, retains the original versions and
their admissions, and marks the replaced active versions superseded. Subsequent publications
without that option preserve adapter 2. A downgrade to adapter 1 is refused.

For the current source pool Mathlib is already imported transitively. This change makes that
import explicit in the trusted header; it does not require solvers to add imports to proofs.

The October 18, 2026 tasks pin activates adapter 2 for all 548 active bundles (274 targets).
All 1,101 earlier versions remain available with their original bytes and admissions. The
replacement manifests record the four-hour timeout already enforced in production. Source
statements, dependency and environment identities, reward identities, and proof size limits
are unchanged. The tasks repository records every old/new commitment in
`history/releases/2026-10-18-mathlib-headers.json`.

## Preparing a header upgrade

Use the existing registry-aware release builder in an isolated tasks clone and the pinned
verification environment. Do not use the old pool-only regeneration script: it predates the
immutable registry and cannot preserve admissions correctly.

```bash
python -m verifier task publish \
  --catalog data/catalog.json \
  --tasks-root /absolute/path/to/private-tasks-clone \
  --audit-date YYYY-MM-DD --adapter-version 2
```

This command writes only to the explicitly named local tasks checkout. It performs real Lean
target validation for rebuilt versions and updates that clone's allowlist and append-only
registry together. It does not push Git refs, deploy an image or change the served tasks pin.
The normal release review and activation remain separate steps. Keep original bundles available
for pending work and historical verification. Install validator support before admitting new
adapter-2 versions; older validators reject those headers.

## Checks

`tests/test_task_loader.py` loads every preserved registry version against its committed digest.
`tests/test_incremental_release.py` checks an explicit header upgrade, preservation of old
admissions, reuse after upgrade and refusal of downgrades. Generator tests cover both header
formats and malicious changes even when the attacker recomputes bundle hashes.
