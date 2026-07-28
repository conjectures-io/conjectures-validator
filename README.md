# conjectures.io — Bittensor Subnet 66

**conjectures.io** is the pay-to-submit API for **Bittensor Subnet 66**. A client pays to submit a
conjecture, the service turns an eligible submission into an immutable proof task, miners compete to
produce a Lean proof, and validators reward results that pass deterministic kernel-level
verification.

Payment buys admission to the task pipeline; it must never buy a favorable verification result.
Every accepted proof is checked against the same committed statement and verifier policy.

This repository contains the task-generation and hardened-verification foundation. It does not yet
contain the public submission API, payment confirmation and reconciliation, the complete paid-task
lifecycle, or the validator scoring and weight-setting loop required for the finished service.

| Area | Status |
| --- | --- |
| Audited Lean task generation and immutable task commitments | Implemented |
| Hostile-proof verification in an isolated container | Implemented |
| Paid-service proof handoff with exact task digest | Implemented |
| Public conjecture submission API | Required |
| Payment confirmation, idempotency, reconciliation, and refunds | Required |
| Paid submission state and status API | Required |
| Validator challenge, verification, scoring, and weight-setting loop | Required |
| Subnet 66 production launch and operating runbooks | Required/needs confirmation |

See [`docs/SUBNET.md`](docs/SUBNET.md) for the intended network flow, the exact implementation
boundary, the draft pay-to-submit contract, the work required to operate Subnet 66, and the
remaining product decisions.

## What the subnet is trying to do

The subnet turns paid conjecture submissions into mathematical work with independently verifiable
results:

1. A client obtains a price or payment instruction from conjectures.io.
2. The client pays and submits a conjecture with an idempotency key and payment reference.
3. The API confirms the payment exactly once, validates the submission, and returns a durable
   submission ID and status.
4. An eligible submission is formalized or matched to one exact Lean proposition and published as
   an immutable task bundle.
5. Miners use any solver or research workflow they choose to produce a candidate Lean proof.
6. Validators run candidate proofs in the isolated verifier.
7. A deterministic scoring policy converts valid results into Bittensor weights for netuid 66.

The verifier checks the exact formal proposition. It does **not** decide whether the Lean statement
faithfully represents the informal mathematics, whether a proof is globally novel, or how much a
valid proof should be rewarded. Payment handling, formalization review, and incentive design sit
outside the verifier's acceptance boundary.

## Verification foundation

The task pipeline turns one immutable revision of the complete
[`google-deepmind/formal-conjectures`](https://github.com/google-deepmind/formal-conjectures)
repository into cataloged Lean targets and verifies hostile single-file submissions against those
targets.

The pinned data flow is:

```text
Formal Conjectures e923379e…
  -> Lean environment catalog extraction
  -> open-source eligibility + versioned adapter
  -> deterministic task payload + externally published SHA-256
  -> static policy scanner + fresh workspace
  -> Landlock/seccomp + Comparator + Lean kernel (+ optional Nanoda)
  -> stable JSON reason code and verdict
```

The verifier remains isolated from networking, payment credentials, and wallets. The public API,
payment service, subnet processes, and proof verifier must run as separate trust domains.
[`verifier/service_adapter.py`](verifier/service_adapter.py) is the narrow handoff for proof bytes
from the future paid service into this unchanged verification contract.

## Quick start

On Ubuntu 24.04 (or an equivalent Linux host with Python 3.11+, Git, curl, a C toolchain, Go, Rust,
and Landlock support):

```bash
./scripts/bootstrap.sh
export PATH="$PWD/.venv/bin:$PWD/.elan/bin:$PATH"
python -m verifier doctor
```

The bootstrap script checks out only the commits in `pins.lock.json`, installs the pinned Lean
toolchains, downloads Mathlib's trusted binary cache, builds Formal Conjectures in `answer`
postpone mode, and builds Comparator, the Lean-4.27 `lean4export` backport, and Landrun. Nanoda is
optional: set `ENABLE_NANODA=1` during bootstrap to build it. Bootstrap also installs the pinned
service and Subnet 66 dependencies; it does not install the removed legacy miner transport.

Build and inspect the real full-repository catalog:

```bash
python -m verifier catalog build \
  --repo-dir vendor/formal-conjectures \
  --output data/catalog.json

python -m verifier catalog stats --catalog data/catalog.json
```

Generate one target or a category batch:

```bash
python -m verifier task generate \
  --catalog data/catalog.json \
  --theorem Arxiv.id2303_01089.conjecture_1_3 \
  --mode formalized \
  --output tasks/furstenberg-formalized
```

## Gold-standard solver tasks

Use the immutable bundles in [`tasks/gold/`](tasks/gold/) as the standard public targets for
solver attempts. The collection is pinned to Formal Conjectures commit
`e923379e609b9d5987011a1d1f06ec22ea25cd20` and contains 29 reward tasks covering 29
audited Erdős problems from 29 source files. Each task has one exact canonical theorem whose proof
closes the whole source problem. Partial results, numbered parts, variants, candidate bounds, and
multi-target bundles are excluded. Written on the Wall II is hard-excluded. The 178 source
declarations and canonical types used by the two previous pools are explicitly retired and are not
reused.

Every gold task has mode `formalized`. Its generated target is definitionally equal to the source
theorem's complete Lean type and has the same canonical type hash. The pool admits only direct
propositions: it does not synthesize `¬P`, extract one side of an answer wrapper, or substitute a
new answer. A source theorem that is itself a negation remains a negation because that is the
formalization; the generator never synthesizes one.

Choose a task, read its `Challenge.lean` and `manifest.json`, then write
`submissions/Main.lean` with a proof of the single `Bounty.target`, replacing its `sorry`. Do not
edit the task bundle. Confirm that the bundle is byte-for-byte allowlisted before spending solver
time:

```bash
TASK="$(find tasks/gold -mindepth 1 -maxdepth 1 -type d | sort | head -n 1)"
python scripts/check_gold_task.py "$TASK"
```

The complete machine-readable admission set and bundle commitments are in
[`gold/allowlist.json`](gold/allowlist.json). Only an allowlisted, verifier-accepted proof should
advance to human mathematical review. Generation compiles and independently inspects every target;
this proves that the validator has an exact, hole-free target. The separate
[`gold/selection-audit.json`](gold/selection-audit.json) records the Formal Conjectures status,
Erdős Problems tracker status, pull-request review, and formal-surface screen. “Plausibly
attackable” is not a guarantee of solvability and does not prove that the informal statement is
correct.

The deterministic pool selection and compiled validation are implemented by
`scripts/rebuild_gold_pool.py`. It loads the exact audited selection and
[`gold/whole-problem-targets.json`](gold/whole-problem-targets.json), admits exactly one canonical
whole-problem theorem per task and source file, enforces the Erdős minimum and WOWII exclusion, and
refuses to overwrite an existing pool or allowlist. The complete admission contract is in
[`gold/README.md`](gold/README.md).

The direct CLI form is below. It reports a production sandbox only when the live probe confirms an
equivalent hardened Linux setup, including a non-executable workspace; the Compose profile later in
this section is the supported deployment path.

```bash
python -m verifier verify \
  --task tasks/furstenberg-formalized \
  --submission submissions/Main.lean \
  --expected-task-sha256 sha256:<64-lowercase-hex-digits>
```

The checked-in examples are deliberately admitted test fixtures. On macOS, their explicit
development-only invocation is:

```bash
python -m verifier verify \
  --task examples/simple-direct/task-positive \
  --submission examples/valid-submission/Main.lean \
  --allow-test-task \
  --allow-insecure-development
```

Never expose `--allow-test-task`, `--allow-uncommitted-task`, or
`--allow-insecure-development` in a production service.

The exit status is `0` for accepted, `1` for a rejected proof, and `2` for bad verifier/task
configuration. Reports use sorted JSON keys and stable reason codes. Timing fields are measurements
and naturally vary.

The production container profile runs the same commands without exposing the host toolchain:

```bash
docker compose build verifier
docker compose run --rm verifier doctor

FC_SUBMISSION_FILE=./submissions/Main.lean docker compose run --rm verifier verify \
  --task /inputs/tasks/furstenberg-formalized \
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

The production gold mode handles:

- `DIRECT_PROP` with mode `formalized`: the exact source theorem type, with no logical
  transformation.

The version-1 adapter table also retains the following non-gold generation modes for verifier
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

`source-metadata.json` includes a `references` array when the Formal Conjectures module docstring
has a `*Reference:*` or `*References:*` section. Each entry preserves the source Markdown so clients
can render linked and unlinked citations without parsing Lean source. Grouped task metadata carries
the same field for every member.

Production generation accepts only `formalized` tasks from compiled `research open`,
`DIRECT_PROP` theorem declarations whose dependency closure contains `sorryAx`, which is how the
source repository marks an admitted open conjecture. The source type itself must contain no
`sorryAx` term or answer annotation. Production rejects formal-proof metadata and exact canonical
type collisions with cataloged theorems that have non-admitted proofs. Verification recomputes the
source category, declaration kind, formal-proof status, axiom closure, exact source/target type
equality, and absence of holes in the generated target from the compiled Lean environment rather
than trusting JSON metadata.

## Submission policy and verification stages

The only untrusted input is one regular `.lean` UTF-8 file. It is placed between the trusted header
and footer. A comment/string-aware Lean token scanner rejects command-form `import`, `prelude`,
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
