# Formal Conjectures Verifier POC

This project turns one immutable revision of the complete
[`google-deepmind/formal-conjectures`](https://github.com/google-deepmind/formal-conjectures)
repository into cataloged Lean targets and verifies hostile single-file submissions against those
targets. It checks the exact formal proposition. It does **not** decide whether that proposition is a
faithful formalization of the informal mathematics.

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

The verifier remains isolated from networking and wallets. A separate `frontier_subnet` package
adds the phase 1–3 Bittensor foundation and a submission-only miner; it does not include a solver,
validator scoring loop, emissions logic, frontend, accounts, or remote submission upload.

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
optional: set `ENABLE_NANODA=1` during bootstrap to build it. It also installs the pinned Bittensor
v11 subnet dependencies and the `frontier-miner` command.

## Submission-only Bittensor miner

The reference miner stores Lean source that its operator created elsewhere. It has no solver
integration: `frontier-miner load` safely imports one local, allowlisted task/submission pair, and
`frontier-miner serve` returns signed, chain-round-bound commitments and timed reveals to
authorized validators. Runtime state lives outside the Git worktree.

See [`docs/SUBNET.md`](docs/SUBNET.md) for the protocol boundary, localnet setup, container
deployment, and phase 1–3 verification gates.

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
  --theorem GracefulLabeling.graceful_tree_conjecture \
  --mode positive \
  --output tasks/graceful-positive

python -m verifier task generate-all \
  --catalog data/catalog.json \
  --category "research open" \
  --modes positive,negative \
  --output tasks/generated
```

## Gold-standard solver tasks

Use the immutable bundles in [`tasks/gold/`](tasks/gold/) as the standard public targets for
solver attempts. The collection is pinned to Formal Conjectures commit
`e923379e609b9d5987011a1d1f06ec22ea25cd20` and contains 227 positive or negative bundles from 114
source theorems. Each source passed a deny-by-default audit for current open status, fidelity of the
Lean statement, proof-level research significance, and absence of an evident elementary or
definitional shortcut. "Gold" is an admission standard, not a promise that a problem remains open;
the collection must be re-audited when its sources or bundles change.

The positive Graceful Tree Conjecture bundle is a concrete starting point:

[`tasks/gold/fc-e923379e-gracefullabeling-graceful-tree-conjecture-6de09defc1-positive-v1/`](tasks/gold/fc-e923379e-gracefullabeling-graceful-tree-conjecture-6de09defc1-positive-v1/)

Read its `Challenge.lean` and `manifest.json`, then write `submissions/Main.lean` with the same
`Bounty.target` theorem and replace the `sorry` with a proof. Do not edit the task bundle. Confirm
that the bundle is byte-for-byte allowlisted before spending solver time:

```bash
TASK=tasks/gold/fc-e923379e-gracefullabeling-graceful-tree-conjecture-6de09defc1-positive-v1
python scripts/check_gold_task.py "$TASK"
```

Verify a candidate in the production container against the committed bundle digest:

```bash
FC_SUBMISSION_FILE=./submissions/Main.lean docker compose run --rm verifier verify \
  --task /inputs/tasks/gold/fc-e923379e-gracefullabeling-graceful-tree-conjecture-6de09defc1-positive-v1 \
  --submission /inputs/submissions/Main.lean \
  --expected-task-sha256 sha256:59e350eb60c7c5773203c9631c1c71364047a7cd7ee211fa84092728615e3ba6
```

The complete machine-readable admission set and bundle commitments are in
[`gold/allowlist.json`](gold/allowlist.json). Only an allowlisted, verifier-accepted proof should
advance to human mathematical review.

Generate a production task and save the `task_bundle_sha256` printed by the command. The digest must
be published through an authenticated operator channel before miners submit proofs.

The direct CLI form is below. It reports a production sandbox only when the live probe confirms an
equivalent hardened Linux setup, including a non-executable workspace; the Compose profile later in
this section is the supported deployment path.

```bash
python -m verifier verify \
  --task tasks/graceful-positive \
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
  --task /inputs/tasks/graceful-positive \
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

The built-in, version-1 adapter table handles:

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
`comparator-config.json`, `trusted-hashes.json`, and `source-metadata.json`. The task ID is a pure
function of the repository commit, source theorem, mode, and adapter version. Every trusted payload
is hashed; task loading rejects symlinks, duplicate JSON keys, changed bytes, extra files, and files
that are hashed but do not exactly match output reconstructed by the pinned generator. A separate
whole-bundle SHA-256 prevents a self-consistent replacement task from being substituted after task
publication.

Production generation accepts only compiled `research open` theorem declarations whose dependency
closure contains `sorryAx`, which is how the source repository marks an admitted open conjecture.
It rejects formal-proof metadata and exact canonical type collisions with cataloged theorems that
have non-admitted proofs. Verification recomputes the source category, declaration kind, formal
proof status, axiom closure, and absence of holes in the generated target from the compiled Lean
environment rather than trusting JSON metadata.

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
