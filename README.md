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
  -> versioned adapter + hashed task manifest
  -> static policy scanner + fresh workspace
  -> Comparator + Lean kernel (+ optional Nanoda)
  -> stable JSON reason code and verdict
```

No Bittensor networking, miners, validators, emissions, scoring, commit-reveal, novelty analysis,
frontend, accounts, or remote service is present.

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
optional: set `ENABLE_NANODA=1` during bootstrap to build it.

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

Verify a submission:

```bash
python -m verifier verify \
  --task examples/simple-direct/task-positive \
  --submission examples/valid-submission/Main.lean
```

The exit status is `0` for accepted, `1` for a rejected proof, and `2` for bad verifier/task
configuration. Reports use sorted JSON keys and stable reason codes. Timing fields are measurements
and naturally vary.

## Architecture and exact target generation

`lean/CatalogExtractor.lean` recursively obtains module names from the repository tree, imports every
module, and then discovers declarations through Lean's module data and persistent category, AMS, and
formal-proof environment extensions. Source text regexes are not used to discover declarations.
For each tagged declaration the extractor inspects the elaborated `ConstantInfo`, answer metadata,
proof/value shape, proposition status, and fully explicit pretty-printed expression. Python hashes
that canonical elaborated representation with SHA-256.

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

Generated `Challenge.lean` files do not reparse a copied pretty string. Direct propositions use
Lean's `type_of%` against the pinned declaration. `lean/TaskSupport.lean` reconstructs proposition
answer targets and substitutes value answers by walking the elaborated expression tree. After the
challenge compiles, `lean/TaskInspector.lean` independently reconstructs the intended type, checks
definitional equality, and emits canonical source and target types for hashing. Generation fails on
a missing/moved declaration, a source hash change, classification drift, or target mismatch.

Each task contains `manifest.json`, `Challenge.lean`, trusted solution header/footer,
`comparator-config.json`, `trusted-hashes.json`, and `source-metadata.json`. The task ID is a pure
function of the repository commit, source theorem, mode, and adapter version. Every trusted payload
is hashed; task loading rejects symlinks and changed bytes.

## Submission policy and verification stages

The only untrusted input is one regular `.lean` UTF-8 file. It is placed between the trusted header
and footer. A comment/string-aware Lean token scanner rejects command-form `import`, `prelude`,
`module`, `axiom`/`constant`, `unsafe`, `extern`, `foreign`, initializers, syntax/elaboration
extensions, `set_option`, `run_cmd`, and `run_tac` even when commands share a line; it rejects
qualified or quoted `sorry`, `admit`, and `sorryAx` references. It also enforces literal answer
syntax before Lean runs. NUL bytes, non-UTF-8, symlinks, non-regular or non-Lean files, and oversized
files are rejected using a no-follow descriptor and a bounded read.

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

## Isolation and trusted computing base

Production verification is the Ubuntu container path. It runs as UID 10001, drops capabilities,
uses `no-new-privileges`, a read-only root, bounded PID/CPU/memory settings, and `network_mode: none`.
Comparator invokes Landrun around Lean and `lean4export`; the workspace is fresh and writable while
the source checkout and prebuilt dependency products are read-only. The workspace is deleted unless
`--retain-workspace` was explicitly requested for diagnostics.

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
dependency closure and Comparator rejects it. The source identifier is additionally forbidden by
the scanner. Automatically using already-proved source declarations as bounties is discouraged,
because an automated tactic could in principle rediscover a globally available proof without
spelling its name; use a purpose-built source-pruning adapter/image when that distinction matters.

## Pins, cache, and reproducibility

`pins.lock.json` pins the Elan installer, Formal Conjectures, its Mathlib revision, Lean, Comparator,
a Lean-4.27 `lean4export` backport, Landrun, and Nanoda. `doctor` checks every available checkout for
the exact commit and a clean tracked tree. Comparator's own implementation toolchain can differ
from the target project's Lean version; the exporter is the component that must match the target
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
    "same_statement": true,
    "axioms_permitted": true,
    "lean_kernel_passed": true
  },
  "sandbox_mode": "landrun"
}
```

A policy rejection instead returns `accepted: false`, stage `STATIC_POLICY_CHECK`, and reason
`SUBMISSION_POLICY_VIOLATION`; the verifier never starts Lean for that submission.
