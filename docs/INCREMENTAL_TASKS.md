# Incremental task publication

How task versions are identified, built, published, routed and verified, so that a release
rebuilds only what changed and paid work always returns to the environment that accepted it.

Code: `verifier/task_versions.py` (identities), `verifier/task_store.py` (bundle store),
`verifier/version_registry.py` (registry), `verifier/incremental.py` (planning and publishing),
`verifier/publication.py` (checkout commits and coherent reads), `verification_worker/tasks.py`
(routing), `conjectures_subnet/db/verification.py` (claim), `lean/DependencyClosure.lean` and
`lean/DependencyExtractor.lean` (closure extraction).

## Why

A v1 task ID is `fc-<source commit>-<slug>-<type hash>-<mode>-v1`. Every repin of
formal-conjectures therefore rotates every task ID and regenerates every bundle, including the
targets whose statements did not change. The v1 `type_hash` is a hash of the printed type, so a
definition whose body changed while its name stayed the same is invisible to it: a Math30 target
whose type is just `Statement16` keeps its hash when `Statement16`'s body changes. And the worker's
claim was not partitioned by environment, so a worker could lease, and charge an attempt for, paid
work it could not verify.

## Identities

A v2 task version is fixed by three identities, all derived by running the pinned toolchain in
the verification environment itself. None of them is accepted from a caller.

| identity | what it commits to | where it lives | changes when |
|---|---|---|---|
| **EnvironmentIdentity** | Lean toolchain and `lean --githash`; Mathlib and every Lake git package pin; Comparator and lean4export commits; `TaskSupport.lean`; the workspace template; `TASK_GENERATOR_VERSION`; the verification policy (permitted axioms, static policy digest, `VERIFICATION_POLICY_VERSION`). Cross-checked against `pins.lock.json`. | in the task ID; `task-version.json` in the bundle | toolchain, Mathlib, package, Comparator, exporter, generator or policy changes, even at the same source commit |
| **DependencyIdentity** | The kernel content of every local constant reachable from the statement's type: `def`/`opaque`/recursor bodies included, theorem bodies excluded (only their types), serialized as a DAG by `DependencyClosure`. Plus the structural import graph: local modules with their direct imports, and external imports with their pinned origin. | in the task ID; `task-version.json` | the statement, a definition it unfolds to (directly or transitively), or the shape of its import graph changes |
| **BuildProvenance** | The full source text and the `.olean` SHA-256 of every local module in the closure, plus the source `lakefile`. | per admission in the registry; recomputed in the verifier container | any byte of a local module in the closure, or its compiled `.olean`, changes |

The v2 task ID is `fc-v2-<slug>-<24 hex>-<mode>`. The 24 hex are a digest of the environment
identity, the dependency identity and the hashes of every trusted bundle file, including
`task-version.json`. The source commit is deliberately *not* in it, and neither is the text of
whole modules. An unrelated target in the same module keeps its ID. So do an edit to a comment
and a re-pin that leaves a target's closure unchanged.

### What is immutable

- The **task version** (ID, bundle digest, dependency identity including the import graph,
  environment identity) never changes. A paid submission binds `task_id` and
  `task_bundle_sha256`, and the `problem_id` it was accepted under, at intake.
- `problem_id(commit, theorems)` commits to the source commit of the instance that accepted
  the submission. The instance is the original environment: that commit together with the
  environment identity named by the version. It is fixed by values written at intake and
  authenticated by the bundle digest, never by the current release.
- **Build provenance** is current build and cache provenance, not task identity. Each admission
  records the provenance its instance built. The verifier recomputes it in its container before
  it compiles a proof: stale or tampered `.olean`s, or source text that differs from what the
  index saw, produce `ENVIRONMENT_MISMATCH`. A new publication may add an admission with new
  provenance. It never rewrites an earlier one.

### Deriving identities

`derive_dependency_index` runs `dependency_extractor` once per release, over all selected
targets, in the publishing environment. The derivation is bracketed by
`lake build --no-build`, plus stat snapshots of every local source and `.olean` before and after,
so an out-of-date or concurrently modified build fails closed rather than being indexed. Every
module's claimed origin is cross-checked against the `.olean` Lean actually loaded. Parsing is
strict: bounded sizes, unique keys, no symlinks (each path component is opened with `O_NOFOLLOW`).
Missing provenance is refused. There is no "statement hash only" fallback.

## Bundle store

`versions/<task_id>/` in the tasks repository (`TaskVersionStore`):

1. A bundle is generated into `versions/.staging/` and validated in full.
2. Every staged file and the staged directory are flushed (`fsync`).
3. The directory is renamed into place and the store directory is flushed.
4. It is sealed (files 0444, directory 0555).

If the destination already exists, its full contents are re-validated. A different digest is
`TASK_COMMITMENT_MISMATCH`. The same digest is resealed and reused, which finishes a publisher
that died between the rename and the seal. Every load re-hashes every trusted file against the
expected digest. A sealed mode is hygiene, not a trust boundary.

## Registry

`task-versions.json` is the admission, selection and retirement record. It is separate from the
bundles and only ever appended to:

- **instances**: (source commit, environment identity | None). None marks a legacy instance.
- **publications**: ordered. Each names its instance and the exact `allowlist.json` digest it opened.
- **versions**: one record per version: `state` ∈ active / retired / held / superseded, and
  append-only **admissions** (instance, problem_id, build provenance, first publication).

A worker for instance *I* serves exactly the triples (task_id, digest, problem_id) of versions
admitted at *I*: `served_keys`. Retired or held versions remain served for their pending work.
Two instances never serve the same key, and there is no "equivalent environment" rule.

Before a record is used, every field that names the task is re-derived from the bundle's own
bytes (`assert_record_matches_bundle`). That covers the task ID, digest, provenance, mode, the
exact theorem tuple (equal to the bundle's sources), reward target, the legacy commit, the v2
identities, and each admission's problem ID recomputed from the bundle's sources at that
admission's commit. A registry that is internally consistent but describes a different theorem
than its bundle is refused.

## Publishing

`verifier task publish --catalog … --tasks-root … --pool-size … --audit-date … [--jobs N]`
(`publish_tasks_checkout`):

1. Take the checkout's writer lock (`.publication.lock`, exclusive `flock`), first rolling
   forward any interrupted commit.
2. Read the audited selection metadata (`tiers/tier-1/`) and the previous registry *inside the lock*.
3. Derive the dependency index in this environment.
4. Plan. A version whose ID is already published and whose bundle is in the store under its
   published digest is **reused** (re-validated against its record). Otherwise it is **built**
   with the real Lean target validator, in spawn-context worker processes with a per-job
   timeout. Retired and held sources get their recorded states. Single-target generation and
   surgical retirement/reuse are preserved.
5. Check the exact allowlist bytes about to be committed against the bundles and the new registry.
6. Commit (`commit_publication`):
   - refuse if the on-disk registry is not the one read in step 2;
   - write both files under unique names and flush them;
   - write and flush a journal naming them and their digests;
   - rename the allowlist, then the registry;
   - flush the directory and remove the journal.

A crash leaves either nothing visible or a journal. The next writer rolls a journal forward
from its digest-checked staged files. It stops if neither the staged nor the committed bytes
match (for example after tampering).

## Reading

Readers (the API task catalog, the worker resolvers) use `read_coherently`:

1. Refuse if a journal exists.
2. Read the registry and the allowlist once each.
3. Build only from those bytes. The allowlist bytes parsed are the bytes checked against the
   registry's current publication.
4. Refuse if a journal exists now. If either file changed in the meantime, start over (at most
   three times).

A publication that is pending at any point during the read is refused. One that completed
during the read is re-read whole. Readers take no lock and write nothing, so they work on
read-only mounts and on checkouts that never had a writer. Independently of all this, a
registry whose current publication does not name the allowlist's digest is refused.

## Routing and claims

`claim_next` leases only rows whose (task_id, digest, problem_id) is in the worker's served
triples. It joins `unnest(:served_task_ids, :served_digests, :served_problem_ids)`, uses
`FOR UPDATE … SKIP LOCKED`, and increments attempts only for the row it claims. A worker
therefore never leases, and never charges an attempt for, another environment's paid work. A
misrouted claim (the resolver refuses after the claim, which the served set should make
impossible) refunds the attempt, excludes the row for this process, and emits
`claim_routing_violation`. The attempt cap applies to every worker, including the original one.

At startup the worker asks its verifier image which instance it is (`doctor`):

- A report **with** `task_versions` must carry a derived identity and no error. Otherwise the
  worker stops before any claim.
- A report **without** the field (an image that predates it) is the legacy instance of its
  `formal_conjectures.actual_commit`.

The worker then loads the resolver for exactly that instance. An instance absent from the
registry is refused (`TaskNotAllowed`).

## Verification of a v2 task

`verify()` performs these checks in order, and every mismatch is an operator outcome, never a
verdict against the miner:

1. Before the proof is read, the image's derived environment identity must equal the task's
   (`ENVIRONMENT_MISMATCH`), and production tasks must be given the expected build provenance.
2. The challenge is built and the inspector's closure is compared with the task's dependency
   identity (`DEPENDENCY_IDENTITY_MISMATCH`) and with the expected build provenance
   (`ENVIRONMENT_MISMATCH`), before Comparator runs.
3. Comparator runs. A comparator child crash is `COMPARATOR_TOOL_CRASHED` (operator) only when
   the **final nonempty** stderr line is `uncaught exception: Child exited with N`, with N ≥ 128
   and N ≠ 137. Exit 137 is `RESOURCE_LIMIT`. Earlier lines cannot change the classification.
   The three upstream exporter-panic controls (`quot_mismatch`, `primitive_issue`,
   `char_ofnat_issue`) are tool crashes. Genuine semantic rejections keep their reasons.

Legacy (v1) tasks are verified exactly as before, against their own source commit.

## What each kind of change does

These are measured counts from the bounded real-Lean benchmark: 11 targets / 22 versions in the
final image, with this session's code overlay at that time. See `OPUS-RESULT.md` for exact
digests, and the scripts under the evidence directory.

| change | built | reused | notes |
|---|---|---|---|
| cold start | 22 | 0 | publish 115.1 s (6 spawn jobs) |
| unchanged re-run | 0 | 22 | publish 0.29 s |
| retire one target (2 versions) | 0 | 20 | 2 retired; their pending work is still served |
| reintroduce it | 0 | 22 | same task IDs as before |
| stale `.olean` against fresh source | — | — | refused: "not up to date"; registry unchanged |
| one definition body (printed type unchanged) | 2 | 20 | the type hash is unchanged, the dependency identity changed |
| a definition shared by two targets in one module | 4 | 18 | three other targets in the same module were reused |
| one statement in a module with siblings | 2 | 20 | the three siblings were reused |
| comment-only edit to an imported module | 0 | 22 | same IDs; build provenance changed for the 5 dependent targets |
| broken import | — | — | refused (build failed) |
| import-graph edit | 10 | 12 | |
| generator/policy change at the **same** source commit | 22 | 0 | all IDs new; new instance at the same commit |
| corrupted store entry | — | — | refused `TRUSTED_FILE_MODIFIED`; registry unchanged |
| entry restored | 0 | 22 | |

## Public catalog and task listing

The public catalog (`/v1/catalog/…`) returns every item of the requested page, each with its
bounty's `available` flag. `GET /v1/tasks` lists only tasks whose bounty is available, and
`GET /v1/tasks/{task_id}` answers `404 BOUNTY_CLOSED` for one that is not
(`submission_api/routers/tasks.py`). Retired and held versions are not in the current
allowlist, so the API never lists them. They stay in the registry and store only so that their
pending paid work can be verified.

## Security review

| threat | control | evidence |
|---|---|---|
| A definition changes under a target's name | dependency identity covers definition bodies transitively; the verifier re-derives it before Comparator | `test_task_versions`, benchmark s2/s3, `test_v2_verification` |
| Stale or tampered build cache | build provenance (source text + `.olean` SHA-256) recomputed in-container; coherent-build check during derivation | benchmark s2-stale, `test_task_versions` |
| Bundle poisoning or corruption in the store | content-addressed IDs; full rehash on every load; refusal of an existing entry with a different digest; symlink and traversal refusal | `test_task_store`, benchmark s8 |
| Registry metadata naming another theorem | `assert_record_matches_bundle` against the bundle's own sources | `test_resolver_source_binding` (independent review reproduction) |
| Concurrent or interrupted publication | checkout writer lock, stale-head refusal, journaled commit with roll-forward | `test_publication_commit` (two full publishers; kill after each step; tampered recovery) |
| A reader overtaken by a publication | `read_coherently`: refusal of pending commits, re-read of completed ones | `test_publication_readers` (48 interleavings; independent review reproductions) |
| Crash during bundle publication | flush of files and directory before the rename; reseal on retry | `test_task_store` (crash after `staged-synced` / `renamed`; concurrent retries), also as UID 10001 |
| Environment mix-ups | environment identity in the task ID; checked before the proof is read; served-key partitioned claims; startup refusal of unknown instances or failed identity | `test_worker_routing` (real DB), `test_v2_verification`, historical runtime |
| Wrong worker burning attempts | the SQL claim filter: attempts increment only for claimed served rows; refund on misroute | `test_worker_routing`, historical runtime |
| Spoofed crash diagnostics | final-line-only rule | `test_v2_verification`; real Comparator controls |
| Symlinks or path traversal in the tasks checkout | `O_NOFOLLOW` per component; location prefixes; `_bundle_directory` realpath check | `test_task_store`, `test_version_registry` |
| Test suite reaching shared or production databases | `tests/database_guard.py`: explicit DSNs only; the server's `system_identifier` must equal the declared one; effective libpq endpoints parsed; TCP 5432/5440, service files and the default socket refused | `test_database_guard` |

Trust limits, stated plainly:

- The integrity of external artifacts (toolchain binaries, Mathlib `.olean`s, Comparator and
  exporter binaries) still rests on the **pinned image**. The environment identity records what
  was derived inside that image; it does not independently attest the image.
- The database guard proves which server a DSN reaches (`system_identifier`). That the server is
  a new private fixture is established by the operator's orchestration, not by repository code.
- Bundles are content-addressed and re-hashed. Seal modes are hygiene only.

## Migration and backward compatibility

- **No database migration.** A submission row already carries `task_id`, `task_bundle_sha256`
  and `problem_id`, and `problem_id` already commits to the intake commit.
- **Seeding the registry** (once, in the tasks repository): `scripts/seed_version_registry.py
  --environment-identity ENVIRONMENT.json [--historical history/legacy/HISTORY.json]`.
  - Every bundle the current allowlist admits is registered as a legacy version at this
    release's instance.
  - Each preserved historical legacy bundle under `history/legacy/` is registered at the
    instance that originally accepted its paid work, with intake closed.
  - No bundle byte changes, so legacy task IDs, digests and problem IDs keep routing.
- **Legacy verifier images** are still usable for their own pending work. Their `doctor`
  predates `task_versions`, so the worker treats them as the legacy instance of their commit.
  Their `verify` CLI accepts the arguments the runner passes for legacy tasks (no
  `--expected-build-provenance` flag is passed for legacy work). A legacy image cannot read a v2
  bundle, and refuses it (it reports an unexpected `task-version.json`). A v1 bundle in a newer
  image is refused against that image's source commit.
- **Deployment order.** Upgrade workers before publishing a registry with more than one
  instance. An old worker has no served-key claim filter. It would lease other environments'
  rows (and refuse to resolve them), charging attempts.
- **Rollback.** The registry and store are append-only, and readers pin themselves to the
  current publication's allowlist digest. Rolling back a release means committing a new
  publication; nothing is rewritten.
- **Historical environments.** Keep the original verifier image for any instance with pending
  paid work. If only a reconstruction exists, it must be labeled as reconstructed. It must
  preserve the original Lean, Mathlib, Comparator and task bytes, and its doctor must report the
  original commit.

## Limitations

- One target per task (`v2 publication supports single-target tasks`); grouped tasks are refused.
- The first derivation of a release costs about 20–30 s for 11 targets and about 71 s for all
  275, plus the Lean builds of the versions that changed.
- A reader that meets an unfinished publication fails closed until the next writer rolls the
  journal forward.
