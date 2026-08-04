# conjectures.io — Bittensor Subnet 66 Validator

This is the complete validator repository for **conjectures.io**, **Bittensor Subnet 66**. Its
product is a paid proof-submission API: a miner pays **0.5 TAO**, submits a candidate Lean proof for
an eligible task, and receives a durable submission ID. The validator confirms the payment, checks
the proof with Lean, optionally holds a valid proof for manual reward review, and passes every
reward-eligible proof into the Subnet 66 reward pipeline.

Payment buys one proof-verification attempt. It never changes Lean's verdict and does not guarantee
a reward.

This repository is the system boundary for the entire validator, including:

- the miner-facing paid submission and status API;
- payment confirmation, idempotency, and reconciliation;
- durable submission, verification, review, and reward records;
- immutable proof artifacts and verifier reports;
- asynchronous verification workers and the hardened Lean verifier;
- the optional manual reward-review queue;
- reward eligibility, scoring, and Subnet 66 weight submission; and
- deployment, monitoring, backup, and recovery configuration.

Some of those validator components still need to be implemented. The current checkout already
contains the audited task pool, exact task commitments, service adapter, and hardened Lean
verification core.

| Validator component | Status |
| --- | --- |
| Audited Lean task generation and immutable task commitments | Implemented |
| Hostile-proof verification in an isolated container | Implemented |
| API-neutral proof handoff with exact task digest | Implemented |
| Hardened miner submission bundle format and archive admission | Implemented |
| Miner-facing paid submission and status API | Implemented |
| Shared durable schema and migrations | Implemented |
| Finalized 0.5 TAO transfer reader for payment-gated intake | To build |
| Asynchronous verification worker | To build |
| Manual reward-review decision service | To build |
| Automatic reward eligibility and one-reward-per-family constraint | Implemented |
| Scoring and Subnet 66 weight-setting loop | To build |
| Production launch and operating runbooks | To build |

The submission API captures the per-submission manual-review policy and records the review gate
decision, but the reviewer-facing decision service itself is still to build.

Miners should start at [`docs/MINER.md`](docs/MINER.md): what to do, in order, to get one proof
submitted, verified and paid. [`docs/API.md`](docs/API.md) documents the API surface and its
configuration, [`docs/SUBMISSION_BUNDLE.md`](docs/SUBMISSION_BUNDLE.md) the submission format,
[`deploy/README.md`](deploy/README.md) the database deployment, and
[`docs/SUBNET.md`](docs/SUBNET.md) the service contract, trust boundaries, and remaining work.

## Validator flow

1. A miner chooses an eligible committed task — proving the conjecture, or refuting it with a
   counterexample — prepares one candidate Lean proof, and checks it locally with
   `verifier bundle scan`: a rejected bundle costs nothing to fix.
2. The miner pays exactly **0.5 TAO** and submits the proof bundle through the validator API with
   its task ID and digest, the payment reference, miner identity, and an idempotency key.
3. The API admits the bundle, authenticates the hotkey signature, and confirms the transfer
   against finalized chain state. Intake is payment-gated: a refused request creates no
   submission and is recorded in `api_rejection_log` instead.
4. Once confirmed, the API durably records the proof bytes and the submission, and returns a
   submission ID together with the bounty the submission is owed if it verifies, frozen at that
   moment. The submission is queued for verification by being `UNVERIFIED`.
5. A verification worker claims it under a lease and passes the exact proof bytes and task digest
   to the isolated verifier, in a container holding neither the database nor any key.
6. A proof rejected by policy, Comparator, or the Lean kernel is recorded as rejected and never
   reaches rewards. A run that failed for the validator's own reasons — no sandbox, a dead
   container — is not a rejection: the submission stays unverified and is retried, because the
   miner has already paid and their proof was never judged.
7. A Lean-valid proof and its immutable verification report are durably recorded.
8. If manual reward review is required, the valid submission remains held until a reviewer
   approves or rejects reward eligibility. Manual review cannot override a failed Lean verdict.
9. If manual reward review is not required, or if a held proof is approved, the submission becomes
   reward-eligible and is passed to the reward pipeline.
10. The reward processor pays the amount frozen at intake and records the chain evidence. It
    never reprices; a payout that disagrees with the frozen amount is a bug, not a repricing.

```text
miner pays 0.5 TAO
        |
        v
POST bundle to validator API
        |
        +-- bundle, signature, or payment refused --> no submission, logged in api_rejection_log
        |
        v
durable submission (payment already confirmed) + stored proof bytes
        |
        v
isolated Lean verification
        |
        +-------------------- rejected --------------------> no reward
        |
        v
Lean valid
        |
        +-- review required --> held --> approve/reject
        |                                 |
        +-- review not required ----------+
                                          |
                                          v
                                 reward-eligible
                                          |
                                          v
                                Subnet 66 reward pipeline
```

The manual-review switch controls only whether a Lean-valid submission is held before reward
eligibility. It must not make an invalid proof valid, mutate the submitted artifact, or replace the
deterministic verifier result.

## Lean verification

The task pipeline turns one immutable revision of the complete
[`google-deepmind/formal-conjectures`](https://github.com/google-deepmind/formal-conjectures)
repository into cataloged Lean targets and verifies hostile single-file submissions against those
targets.

The pinned data flow is:

```text
Formal Conjectures 379fc029…
  -> Lean environment catalog extraction
  -> open-source eligibility + versioned adapter
  -> deterministic task payload + externally published SHA-256
  -> static policy scanner + fresh workspace
  -> Landlock/seccomp + Comparator + Lean kernel (+ optional Nanoda)
  -> stable JSON reason code and verdict
```

The verifier remains isolated from networking, payment credentials, wallets, and the validator
database. The API, payment watcher, reward process, and proof verifier are parts of this repository
but must run as separate trust domains. [`verifier/service_adapter.py`](verifier/service_adapter.py)
is the narrow handoff from the validator's verification worker into this unchanged verification
contract.

## Quick start

On Ubuntu 24.04 (or an equivalent Linux host with Python 3.11+, Git, curl, a C toolchain, Go, Rust,
and Landlock support):

```bash
./scripts/bootstrap.sh
export PATH="$PWD/.venv/bin:$PWD/.elan/bin:$PATH"
python -m verifier doctor
```

The bootstrap script materializes the pinned
[`conjectures-tasks`](https://github.com/conjectures-io/conjectures-tasks) checkout and the other
commits in `pins.lock.json`, installs the pinned Lean toolchains, downloads Mathlib's trusted binary
cache, builds Formal Conjectures in `answer` postpone mode, and builds Comparator, the Lean-4.27
`lean4export` backport, and Landrun. Nanoda is optional: set `ENABLE_NANODA=1` during bootstrap to
build it. Bootstrap also installs the pinned service and Subnet 66 dependencies; it does not
install the removed legacy miner transport.

## Submission API

The miner-facing API lives in [`submission_api/`](submission_api/). It authenticates a miner by
hotkey signature, admits one proof bundle, records durable submission and payment state, and queues
the proof for the isolated verifier.

```bash
cp .env.example .env                              # then edit the passwords
docker compose -f docker-compose.db.yml up -d     # Postgres + Flyway migrations

export PAYMENT_RECIPIENT_SS58='5C4h…'
export DATABASE_URL='postgresql+psycopg://conjectures:<pw>@127.0.0.1:5432/conjectures'
uvicorn submission_api.asgi:app --host 127.0.0.1 --port 8080
```

The API has no database of its own. [`conjectures_subnet/db/`](conjectures_subnet/db/) is the
runtime view of [`deploy/migrate/sql/`](deploy/migrate/sql/), which is the source of truth and is
applied by Flyway; the same store is shared with the payment, verification, review, and reward
components. `DATABASE_URL` or the standard `POSTGRES_*` variables configure it once for every
process. Confirm the ORM mirror still matches the migrations with:

```bash
python3 scripts/check_schema_drift.py --dsn postgresql://conjectures:<pw>@127.0.0.1:5432/postgres
```

Intake is payment-gated: a submission row exists only for a transfer already confirmed on
finalized chain state, so a refused request creates no submission and is recorded in
`api_rejection_log` instead. A miner therefore checks a bundle *before* paying:

```bash
python3 scripts/build_submission_bundle.py \
  --proof Main.lean --task-id <task id> --task-sha256 <sha256:…> \
  --hotkey <ss58> --output submission.zip

python -m verifier bundle scan --bundle submission.zip
```

`GET /v1/tasks` publishes the submittable task ids, their committed digests, the price, and the
payment address. Start at [`docs/MINER.md`](docs/MINER.md) for the miner's path end to end, then
[`docs/API.md`](docs/API.md) for endpoints, headers, the signature scheme and configuration, and
[`docs/SUBMISSION_BUNDLE.md`](docs/SUBMISSION_BUNDLE.md) for the bundle format.

The API process must not share a trust domain with the proof verifier; production refuses to start
if it is configured to run verification in process, to authenticate with the development key, or
to accept payments without reading the chain.

Build and inspect the real full-repository catalog:

```bash
python -m verifier catalog build \
  --repo-dir vendor/formal-conjectures \
  --output data/catalog.json

python -m verifier catalog stats --catalog data/catalog.json
```

Generate one target or a category batch:

```bash
mkdir -p ../conjectures-tasks/scratch

python -m verifier task generate \
  --catalog data/catalog.json \
  --theorem Arxiv.id2303_01089.conjecture_1_3 \
  --mode formalized \
  --output ../conjectures-tasks/scratch/furstenberg-formalized

python -m verifier task generate \
  --catalog data/catalog.json \
  --theorem Arxiv.id2303_01089.conjecture_1_3 \
  --mode counterexample \
  --output ../conjectures-tasks/scratch/furstenberg-counterexample
```

## Solver task pool

Use the immutable bundles in the pinned
[`conjectures-tasks`](https://github.com/conjectures-io/conjectures-tasks/tree/main/pool) checkout as
the public targets for solver attempts. The pool currently has one compatibility tier:
[`tier-1`](https://github.com/conjectures-io/conjectures-tasks/tree/main/pool/tier-1) contains all 74
audited targets, including complete statements and independently formalized parts or variants. The source
snapshot is Formal Conjectures commit `379fc0298dc146df549e7061c3ede0353a5bb51f`, deterministically
derived from upstream `f7349f32ba6df6e7b7baf77467a3c6c7777a634d` plus the checked-in semantic
correction patch. The tier contains 148 immutable bundles for 74 theorem targets. Every
target has a `formalized` task for `P` and a `counterexample` task for `¬ P`.

Each bundle has a commit-specific `problem_id`, while every statement under the same numbered
Erdős problem has a stable `reward_family_id` such as `erdos-340`. The database permits at most one
reward per family, including across source repins, so parent problems and related variants
cannot be paid separately. The release has 55 reward families. Multi-target bundles and answer
wrappers remain excluded. The 178 source declarations and canonical types used by the two previous
releases are explicitly retired and are not reused.

The pool admits only direct propositions. It does not extract one side of an answer wrapper or
substitute a new answer. Both task variants are compiled and inspected: `formalized` must be
definitionally equal to the source type, while `counterexample` must be definitionally equal to its
logical negation.

Choose a task, read its `Challenge.lean` and `manifest.json`, then write
`submissions/Main.lean` with a proof of the single `Bounty.target`, replacing its `sorry`. Do not
edit the task bundle. Confirm that the bundle is byte-for-byte allowlisted before spending solver
time:

```bash
TASK="$(find ../conjectures-tasks/pool -mindepth 2 -maxdepth 2 -type d | sort | head -n 1)"
python ../conjectures-tasks/scripts/check_task.py "$TASK"
```

The complete machine-readable admission set and bundle commitments are in
[`tasks/allowlist.json`](https://github.com/conjectures-io/conjectures-tasks/blob/main/allowlist.json).
It records a tier for every source and task
bundle. Only an allowlisted, verifier-accepted proof or refutation should
advance to human mathematical review. Generation compiles and independently inspects every target;
this proves that the validator has an exact, hole-free target. The tier's selection audit records
the Formal Conjectures status,
Erdős Problems tracker status, pull-request review, and formal-surface screen. “Plausibly
attackable” is not a guarantee of solvability and does not prove that the informal statement is
correct.

The deterministic pool selection and compiled validation are implemented by
`../conjectures-tasks/scripts/rebuild_task_pool.py`. It loads the exact audited selection and
[`tier-1 task targets`](https://github.com/conjectures-io/conjectures-tasks/blob/main/tiers/tier-1/task-targets.json), admits exactly
the 74 audited direct propositions, generates committed `formalized` and
`counterexample` task variants, enforces the tier policy, and
refuses to overwrite an existing pool or allowlist. The complete admission contract is in
[`conjectures-tasks/POOL.md`](https://github.com/conjectures-io/conjectures-tasks/blob/main/POOL.md).

The direct CLI form is below. It reports a production sandbox only when the live probe confirms an
equivalent hardened Linux setup, including a non-executable workspace; the Compose profile later in
this section is the supported deployment path.

```bash
python -m verifier verify \
  --task ../conjectures-tasks/scratch/furstenberg-formalized \
  --submission submissions/Main.lean \
  --expected-task-sha256 sha256:<64-lowercase-hex-digits>
```

The task repository's fixtures are deliberately admitted for tests. On macOS, their explicit
development-only invocation with a validator submission example is:

```bash
python -m verifier verify \
  --task ../conjectures-tasks/fixtures/simple-direct/task-positive \
  --submission examples/valid-submission/Main.lean \
  --allow-test-task \
  --allow-insecure-development
```

Never expose `--allow-test-task`, `--allow-uncommitted-task`, or
`--allow-insecure-development` in a production service.

The exit status is `0` for accepted, `1` for a rejected proof, and `2` for bad verifier/task
configuration. Reports use sorted JSON keys and stable reason codes. Timing fields are measurements
and naturally vary. Report schema version 2 includes the deterministic `problem_id` used to join
the mutually exclusive proof and counterexample outcomes; reward admission separately uses the
stable family recorded by the allowlist.

The production container profile runs the same commands without exposing the host toolchain:

```bash
docker compose build verifier
docker compose run --rm verifier doctor

FC_SUBMISSION_FILE=./submissions/Main.lean docker compose run --rm verifier verify \
  --task /inputs/tasks/pool/tier-1/erdos-11-formalized \
  --submission /inputs/submissions/Main.lean \
  --expected-task-sha256 sha256:<64-lowercase-hex-digits>
```

The image build defaults to sixteen Lean worker threads; set `FC_LEAN_BUILD_THREADS` to match the
memory and CPU available to the builder.

Compose mounts the task collection read-only and only the selected submission file, then applies
the network, capability, UID, read-only-root, PID, memory, CPU, file-descriptor, and tmpfs limits in
`docker-compose.yml`. Deploy a reviewed image by immutable registry digest, not by a mutable tag.

## Architecture and exact target generation

`lean/CatalogExtractor.lean` recursively obtains module names from the repository tree, imports every
module, and then discovers declarations through Lean's module data and persistent category, AMS, and
formal-proof environment extensions. Source text regexes are not used to discover declarations.
For each tagged declaration the extractor inspects the elaborated `ConstantInfo`, answer metadata,
proof/value shape, proposition status, and fully explicit pretty-printed expression. Python hashes
that canonical elaborated representation with SHA-256.

Production generation rejects a compiled target whose canonical type hash already belongs to a
cataloged, non-admitted theorem. This is an exact collision screen, not a general novelty oracle.

The production task modes handle:

- `DIRECT_PROP` with mode `formalized`: the exact source theorem type, with no logical
  transformation;
- `DIRECT_PROP` with mode `counterexample`: the exact logical negation of the source theorem type.

The version-1 adapter table also retains the following non-pool generation modes for verifier
fixtures and offline experiments:

- `DIRECT_PROP`: separate positive `P` and negative `¬P` targets;
- `PROP_ANSWER_WRAPPER`: removes exactly one annotated side from `answer(...) ↔ P`;
- `BOOL_ANSWER`: permits only literal `true` or `false`;
- `NAT_ANSWER`: permits only an ASCII natural numeral;
- `INT_ANSWER`: permits only a signed integer literal;
- `FINITE_ANSWER`: permits only cataloged nullary constructors of a known finite inductive type;
- `POINTER_DECLARATION`: records and skips the duplicate; generate its original declaration instead.

`GENERAL_VALUE_ANSWER`, `MULTIPLE_ANSWER_HOLES`, and `DEFINITION_HOLE` are cataloged but require a
versioned operator adapter. `UNSUPPORTED` remains visible and is never silently activated.

Generated `Challenge.lean` files do not reparse a copied pretty string. Direct propositions use a
trusted `fcTypeOfName% "..."` elaborator that resolves the pinned declaration by name. Finite-answer
types are likewise recovered from the compiled environment rather than splicing catalog text into
Lean source. `lean/TaskSupport.lean` reconstructs proposition answer targets and substitutes value
answers by walking the elaborated expression tree. After the
challenge compiles, `lean/TaskInspector.lean` independently reconstructs the intended type, checks
definitional equality, and emits canonical source and target types for hashing. Generation fails on
a missing/moved declaration, a source hash change, classification drift, or target mismatch.

Each task contains `manifest.json`, `Challenge.lean`, trusted solution header/footer,
`comparator-config.json`, `trusted-hashes.json`, and `source-metadata.json`. An all-of task also
contains `group-metadata.json` with every exact source declaration and an explicit `all_of`
completion policy. Single-target task IDs are derived from the repository commit, source theorem,
mode, and adapter version; grouped IDs commit to the ordered theorem list as well. Every trusted
payload is hashed; task loading rejects symlinks, duplicate JSON keys, changed bytes, extra files,
and files that are hashed but do not exactly match output reconstructed by the pinned generator. A
separate whole-bundle SHA-256 prevents a self-consistent replacement task from being substituted
after task publication.

The allowlist assigns both modes the same deterministic `problem_id` and assigns every related
statement a stable `reward_family_id`. Reward storage enforces at most one reward across the entire
family, including proof/refutation modes, parents, parts, variants, and source repins.

`source-metadata.json` includes a `references` array when the Formal Conjectures module docstring
has a `*Reference:*` or `*References:*` section. Each entry preserves the source Markdown so clients
can render linked and unlinked citations without parsing Lean source. Grouped task metadata carries
the same field for every member.

Production generation accepts only `formalized` and `counterexample` tasks from compiled
`research open`,
`DIRECT_PROP` theorem declarations whose dependency closure contains `sorryAx`, which is how the
source repository marks an admitted open conjecture. The source type itself must contain no
`sorryAx` term or answer annotation. Production rejects formal-proof metadata and exact canonical
type collisions with cataloged theorems that have non-admitted proofs. This collision check is
applied to both `P` and the generated `¬ P`. Verification recomputes the source category,
declaration kind, formal-proof status, axiom closure, and absence of holes from the compiled Lean
environment. It then checks that the target is definitionally equal to `P` in `formalized` mode or
to `Not P` in `counterexample` mode rather than trusting JSON metadata.

## Submission policy and verification stages

A miner submits a `conjectures-submission/v1` ZIP bundle, which the exact-shape scanner in
[`verifier/bundle.py`](verifier/bundle.py) admits only if it contains exactly a bounded strict-JSON
manifest and one regular UTF-8 `.lean` file. The archive is never extracted: entry names are only
compared against a two-name allowlist and never used as filesystem paths. Check a bundle before
submitting it with `python -m verifier bundle scan --bundle submission.zip`. See
[`docs/SUBMISSION_BUNDLE.md`](docs/SUBMISSION_BUNDLE.md) for the format and the full admission
rules.

The only untrusted input reaching Lean is that one regular `.lean` UTF-8 file. It is placed between
the trusted header and footer. A comment/string-aware Lean token scanner rejects command-form `import`, `prelude`,
`module`, `axiom`/`constant`, `unsafe`, `extern`, `foreign`, initializers, syntax/elaboration
extensions, `set_option`, `run_cmd`, and `run_tac` even when commands share a line; it rejects
`native_decide` (which trusts generated native code), and qualified or quoted `sorry`, `admit`, and
`sorryAx` references. Instance declarations,
declaration attributes (including module initializers), meta declarations, syntax/notation
extensions, and interpolated strings are also rejected so apparently literal
answers cannot elaborate to attacker-defined values or hide executable elaborator syntax. Token,
nesting, line, Unicode, numeral-digit, and total-byte limits are applied before Lean runs. NUL
bytes, non-UTF-8, symlinks, non-regular or non-Lean files, and oversized files are rejected using a
no-follow descriptor and a bounded read.

Verification executes these logical stages:

```text
LOAD_TASK -> VERIFY_TRUSTED_HASHES -> LOAD_SUBMISSION -> STATIC_POLICY_CHECK
-> CREATE_WORKSPACE -> BUILD_CHALLENGE -> BUILD_SOLUTION/RUN_COMPARATOR
-> RUN_KERNEL -> RUN_NANODA (optional) -> BUILD_REPORT -> CLEANUP
```

The verifier compiles and inspects the trusted challenge before it writes `Solution.lean`.
Comparator itself performs the untrusted solution build, exports both environments, checks theorem
and definition identity, checks the permitted-axiom closure, and replays the solution through the
Lean kernel. This ordering matters: compiling an attacker-controlled solution with plain `lake
build` before Comparator would contaminate the workspace and would still fail to check statement
identity or axiom closure. A successful `lake build` alone is therefore never an accepted verdict.

Each fresh workspace contains a minimal Lake manifest and path-only overrides for the already-pinned
local package graph. Immutable source and build artifacts are linked into per-package mirrors while
Lake's lock/config metadata is copied into the writable tmpfs. Verification never runs `lake update`
and cannot clone or refresh dependencies at runtime; manifest paths are validated to remain inside
the trusted project before any mirror is created.

## Isolation and trusted computing base

Production verification is the Ubuntu container path on a kernel with Landlock ABI 4 or newer. It
runs as UID 10001, drops capabilities, uses `no-new-privileges`, a read-only root, bounded
PID/CPU/memory/file settings, a bounded tmpfs, and `network_mode: none`. Comparator invokes a pinned,
fail-closed wrapper around Landrun: hostile code can read only the immutable Lean toolchain, package
sources/build cache, and its fresh workspace; it cannot read another concurrent workspace, the rest
of the verifier tree, `/proc`, host home directories, or unrelated mounts. It can write only its
workspace and harmless `/dev` nodes. A seccomp layer denies all socket creation (including the
AF_UNIX channel called out by Comparator), `io_uring`, pidfds, cross-process memory/limit/signal
operations, kernel keyrings, legacy IPC, namespace/mount operations, process-group escape, and file
metadata mutation (which Landlock itself does not mediate). The writable build directory is
deliberately non-executable at both the Landlock and container-mount layers; anonymous executable
memory and executable permission upgrades are also denied. The workspace is deleted unless
`--retain-workspace` was explicitly requested for operator diagnostics.

Direct macOS execution is a development convenience only. Comparator's pinned fake-Landrun shim is
used there and reports `sandbox_mode: development-fake-landrun`; it is not a hostile-submission
security boundary. `doctor` reports `production_ready: false` on that path.

The trusted computing base comprises the hardware, Ubuntu/kernel, container runtime, Landlock and
Landrun, pinned Formal Conjectures checkout and build cache, pinned Lean kernel/toolchain, Comparator,
the compatible `lean4export`, optional Nanoda, this task generator/inspector, manifest hashing code,
and the operator's immutable image. A Lean/kernel/sandbox vulnerability can invalidate a verdict.
Denial-of-service protection is bounded but not formally proved. Static scanning is defense in depth;
Comparator and the kernels are authoritative.

For open conjectures, attempting to invoke the imported source theorem brings `sorryAx` into the
dependency closure and Comparator rejects it. The same is true for any imported lemma that
transitively depends on an admitted proof. The source identifier is additionally forbidden by the
scanner. This proves “no admitted dependency”; it does not prove global novelty. A miner may use a
legitimate, non-admitted imported lemma that proves the same result, and definitionally equivalent
restatements are not exhaustively detected. Use a separately reviewed source-pruned environment if
reward eligibility requires that stronger property. See [SECURITY.md](SECURITY.md) for the precise
acceptance contract, deployment checklist, and residual risks.

## Pins, cache, and reproducibility

`pins.lock.json` pins each Elan release archive by platform and SHA-256, Formal Conjectures, its
Mathlib revision, Lean, Comparator, a Lean-4.27 `lean4export` backport, Landrun, and Nanoda. `doctor`
checks every available checkout for the exact commit and a clean tree, validates the actual Elan and
Lean binary identities, including Comparator's Lake dependency tree, and reports whether the
production sandbox is available. Production readiness also requires a live behavioral sandbox
probe, not only binary presence. Comparator's own
implementation toolchain can differ from the target project's Lean version; the exporter is the
component that must match the target
environment. Normal verification never fetches or updates a branch. Network access is needed only
while building the trusted image/cache.

Production has exactly one active pin set and never follows a floating branch, tag, or dependency
range. Once a week, operators pause new submissions and wait until every accepted submission has
reached a terminal payment, verification, review, and reward state. No pin update may begin while
any submission is queued, leased, running, retryable, or awaiting review or reward processing.

With the system drained, update the compatible Formal Conjectures, Mathlib, Lean, Comparator, and
`lean4export` pins together; rebuild the trusted cache, catalog, task bundles, allowlist,
commitments, and immutable verifier image; and run the complete integration and security suite.
Activate the new pin set atomically, then reopen submissions. If any build, audit, or test fails,
keep the existing pin set active and leave submissions paused until it is safe to resume. Preserve
the old pin values, task digests, and reports in the durable records, but operate only the new
active verifier after the cutover.

Full-catalog statistics are written to `data/catalog-summary.json` from the actual extracted
catalog. No estimated counts are committed. Batch generation writes `generation-summary.json` with
every generated task, adapter-required skip, unsupported skip, and failure.

## Adding an adapter

Add a pure generator to `verifier/adapters.py`, give it a new immutable version, define the submitted
syntax policy and exact `TaskSupport` expression transformation, and add positive/negative unit and
integration cases. General values must specify the submitted type, canonical syntax, equality or
equivalence semantics, exact verification theorem, and any policy checks. Do not activate arbitrary
definition holes merely because Comparator can type-check them.

## Tests and measurements

```bash
.venv/bin/pytest
./scripts/run_integration_tests.sh
```

Unit tests cover schemas, hashes, catalog statistics, adapters, deterministic skips, tokenizer
behavior, answer literals, workspaces, and reports. The opt-in integration suite uses the real pinned
catalog. `data/performance.json` records this checkout's measured catalog extraction, direct task
generation, and warm/cold verification runs; hardware and cache state are included alongside the
numbers rather than presenting them as universal benchmarks.

An accepted report has this shape:

```json
{
  "accepted": true,
  "reason_code": "VERIFIED",
  "stage": "COMPLETED",
  "checks": {
    "task_commitment_valid": true,
    "production_task": true,
    "production_sandbox": true,
    "same_statement": true,
    "axioms_permitted": true,
    "lean_kernel_passed": true
  },
  "sandbox_mode": "landrun+seccomp",
  "task_bundle_sha256": "sha256:..."
}
```

A policy rejection instead returns `accepted: false`, stage `STATIC_POLICY_CHECK`, and reason
`SUBMISSION_POLICY_VIOLATION`; the verifier never starts Lean for that submission.
