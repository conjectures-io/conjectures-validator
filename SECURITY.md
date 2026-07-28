# Security model

This document defines the isolated proof verifier's acceptance boundary for conjectures.io and
Bittensor Subnet 66. The pay-to-submit API, payment boundary, and complete service architecture are
documented in [`docs/SUBNET.md`](docs/SUBNET.md); networking, payment credentials, customer data,
and wallets never enter the verifier container.

This verifier treats a miner's submitted Lean file as hostile. A production acceptance means that
the pinned Comparator found every theorem named by the task (`Bounty.target` for a single target or
`Bounty.target_1`, `Bounty.target_2`, and so on for an all-of group) to have the same statement as
the committed challenge, found no transitive axiom outside the explicit allow-list, and replayed
the exported environment through the Lean kernel. It does not mean that the informal conjecture
was formalized correctly or that the proof is mathematically novel.

## Production acceptance contract

A report is production-grade only when all of the following are true:

- `accepted` is `true`, `reason_code` is `VERIFIED`, and `stage` is `COMPLETED`;
- every check from manifest/task/submission validation through `challenge_built`, `solution_built`,
  `same_statement`, `axioms_permitted`, and `lean_kernel_passed` is `true`, including
  `task_commitment_valid`, `production_task`, and `production_sandbox`;
- if `checks.nanoda_enabled` is `true`, `checks.nanoda_passed` is also `true`;
- `workspace_retained` is `false`;
- the report's `task_bundle_sha256` equals a digest obtained through an operator-controlled,
  authenticated channel;
- `sandbox_mode` is `landrun+seccomp` and the process ran as an unprivileged user in the one-shot,
  networkless, read-only container profile;
- the image itself was built from the reviewed verifier commit and is deployed by immutable image
  digest.

The task commitment is a SHA-256 content commitment, not an identity signature. Publishing a digest
through an unauthenticated channel does not make the task authentic.

## What miners control

The miner controls exactly one regular UTF-8 `.lean` file, up to 1,000,000 bytes. The threat model
assumes the miner knows the entire public task, verifier source, imported Formal Conjectures tree,
and Mathlib environment. It also assumes the miner will try parser ambiguities, name shadowing,
custom instances, elaborator side effects, resource exhaustion, filesystem reads, process attacks,
and dependency reuse.

The miner does not control the task commitment, verifier command-line overrides, container image,
dependency cache, or host/container configuration. Do not expose the development override flags in
a miner-facing API.

## Admitted proofs and open sources

Formal Conjectures represents an open conjecture as a theorem whose repository proof transitively
contains Lean's `sorryAx`. Production task generation intentionally requires that marker, the
`research open` category, `DIRECT_PROP` classification, theorem declaration kind, and absence of
formal-proof metadata. The task must use `formalized` mode: every complete target type is
definitionally equal to its corresponding source theorem type and has the same canonical hash.
Answer extraction and synthetic positive/negative pairing are not admitted to the gold pool.
Those facts are independently recomputed for every target from the compiled Lean environment
during verification.

The admitted source theorem is used only to identify and reconstruct its type. It is placed on the
forbidden-dependency list, and `sorryAx` is not a permitted axiom. Consequently, invoking that
source theorem—or any other lemma whose dependency closure contains an admitted proof—causes
Comparator to reject the submission. Test fixtures that use admitted proofs require the explicit
`--allow-test-task` switch.

The permitted production axioms are exactly:

- `propext`;
- `Quot.sound`;
- `Classical.choice`.

## Defense layers

| Miner strategy | Enforcement |
| --- | --- |
| Change or swap task files | No-follow bounded reads, exact file set, per-file hashes, deterministic payload regeneration, and external whole-bundle SHA-256 |
| Supply a solved, transformed, paired-negative, answer-wrapper, or test task | Exact `formalized` mode, source/target type-hash equality, compiled classification/category/declaration kind, formal-proof tag, `sorryAx` dependency, and target-hole checks |
| Submit `sorry`, `admit`, an axiom, a module initializer, or the admitted source theorem | Token policy plus Comparator's transitive axiom closure |
| Hide an answer behind a decoy namespace or alternate declaration kind | Exactly one ordinary `Bounty.submittedAnswer` definition is required |
| Make numeral text elaborate to a different value | Instance declarations and syntax/notation extensions are prohibited; the definition type/value is still kernel checked |
| Inject an executable, exporter, `PATH`, `LEAN_PATH`, or preload library | Executable paths and a minimal environment are constructed from the pinned tree |
| Use symlink races or mutate trusted files after checking | Final components are opened with no-follow descriptors and copied from one immutable byte snapshot |
| Read host files, another miner workspace, or trusted verifier metadata | Landlock ABI 4+ exposes only immutable Lean/tool/package paths and the current workspace; the live probe requires sibling-workspace reads and writes to fail |
| Mutate trusted metadata or contact another process | Workspace-only content writes, safe `/dev` nodes, no `/proc`, and seccomp denial of metadata mutation, sockets, `io_uring`, pidfds, cross-process memory/limits/signals, kernel keyrings, IPC, and namespace/mount syscalls |
| Refresh or substitute a Lake dependency at runtime | A validated path-only package graph uses immutable pinned checkouts plus writable per-workspace metadata mirrors; no `lake update` runs and the container has no network |
| Fork, flood output, memory-map the host, or fill disk | One deadline, CPU/address-space/file/open-file/process limits, bounded output tails, container PID/memory/CPU limits, and a bounded tmpfs workspace |
| Downgrade the sandbox on macOS or an old Linux kernel | Production fails closed; insecure development requires an explicit override and remains visible in the report |
| Substitute a broken sandbox binary or executable workspace | Production readiness runs a live allow/deny probe through the exact Landrun/seccomp wrapper and requires generated files to be non-executable |
| Smuggle commands in task payloads | Every trusted Lean/config payload is reconstructed byte-for-byte by the pinned pure generator |

Static checks are defense in depth. The authoritative correctness checks are Comparator's statement
comparison and axiom closure followed by kernel replay.

The wrapper specifically compensates for Landrun's documented
[AF_UNIX escape](https://github.com/Zouuup/landrun/issues/43) and
[unmediated metadata changes](https://github.com/Zouuup/landrun/issues/58). It also refuses kernels
below Landlock ABI 4 instead of accepting `--best-effort` degradation on older ABIs.

## Deployment requirements

1. Build the image in a clean environment, record its immutable image digest, and review dependency
   pin changes. The moving operating-system package mirror is part of the image-build trust path;
   the resulting image digest is the deployable artifact. Runtime checks verify every inherited
   Lake package checkout revision and source-tree cleanliness; the image digest commits to the
   resulting compiled caches as well.
2. Run one submission per fresh container as UID 10001 with no capabilities, no new privileges,
   `network_mode: none`, a read-only root, and the supplied PID/CPU/memory/tmpfs limits.
3. Mount only the selected submission file and public task directory read-only. Never mount wallet
   keys, validator credentials, Docker/container sockets, home directories, or other secrets.
4. Publish or sign each generated `task_bundle_sha256` before accepting submissions. Pass that exact
   digest with `--expected-task-sha256`.
5. Never pass `--allow-uncommitted-task`, `--allow-insecure-development`, `--allow-test-task`, or
   `--retain-workspace` in production.
6. Never compile a miner submission outside Comparator or reuse a container/workspace between
   miners.
7. Treat timeouts and resource-limit results as rejections, and apply queue limits and rate limits
   in the validator service outside the one-shot verifier.

## Residual risks and non-goals

- The Lean kernel, Comparator, `lean4export`, Landlock/seccomp implementation, Linux/container
  runtime, hardware, pinned source/build cache, task generator, and operator image are in the trusted
  computing base. A vulnerability in that base can invalidate a verdict.
- Optional Nanoda adds a second kernel implementation but does not eliminate the rest of the trusted
  computing base.
- Exact canonical type-hash collisions with cataloged, non-admitted theorems are excluded from
  production task generation. Definitionally equivalent restatements or proofs available through
  unrelated imported lemmas are not a complete novelty check. If rewards require global proof
  novelty, use a separately reviewed source-pruned environment and an explicit novelty policy.
- Correctness of the informal-to-Lean formalization remains a human review obligation.
- Resource bounds reduce single-submission denial of service. The paid API, payment reconciliation,
  validator replication, scoring, consensus, weight submission, and reward logic remain outside the
  one-shot verifier.
- Compiler, kernel, microarchitectural, and hardware side channels are not claimed to be eliminated.

Security issues should include the verifier commit, image digest, task digest, report, host kernel
version/Landlock ABI, and the smallest non-secret reproducer. Do not attach private validator keys or
other credentials.
