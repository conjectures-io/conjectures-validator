# Erdős expansion prepared on 28 October 2026

This candidate adds 264 direct-proposition targets across 199 of the 328 requested Erdős numbers. Together with the 290 retained targets, the prepared pool contains 554 reward targets and 1,108 proof/counterexample bundles. It has not been deployed or pushed. Admission is provisional on the bounded review described below; this document does not claim that every admitted statement is mathematically unsolved.

The original upstream staging package remains unchanged in the task repository at `staged/erdos-328-20261028`. Its 640 draft bundles and its 95 fully blocked problem numbers describe the earlier staging pass, not this production-compatible candidate. This review supersedes its admission checklist.

## Decisions

| Target decision | Count |
| --- | ---: |
| Admit | 264 |
| Statement depends on an unfilled answer or definition | 113 |
| Preserve prior retirement | 45 |
| Hold an overlapping resolution claim | 39 |
| Duplicate or equivalent reward | 14 |
| Hold a statement dispute | 4 |
| Statement/source discrepancy | 2 |
| Preserve an unresolved earlier hold | 1 |
| Total reviewed targets | 482 |

Every one of the 328 numbers has a disposition in [problem-dispositions.json](problem-dispositions.json). Of the **95 originally fully blocked numbers**, **#564 and #1004 are admitted** and **93 are excluded from this release**; see [blocked-problem-dispositions.json](blocked-problem-dispositions.json). Exclusion resolves the intake decision without inventing an unknown mathematical answer or asserting that a claimed solution is correct.

The three resolved historical holds are #282 (the newer recursive remainder removes used denominators), #564 (the exponent constant is a positive real rather than a natural number), and #1004 (the full claimed argument was withdrawn; the remaining smaller-constant result is partial). Their old bundle bytes and admissions remain in version history. Their stale held display rows are archived in the evidence and removed from the current closed-problem display. The earlier #887(ii) and #1074 density-one-half holds remain; the newer #1074 density-one target is reviewed separately.

Fourteen duplicate targets are excluded, including reformulations of retired #252 and #595, the existing Carmichael totient target, and the existing Erdős–Purdy degenerate-quadruple target. [duplicate-screen.json](evidence/duplicate-screen.json) records the reasons and the exact-hash comparison against the baseline catalog and the rebuilt catalog. Exact hashes alone cannot detect all mathematically equivalent encodings.

## Review and its limits

The review covers compiled statements, docstrings, relevant definitions, the 328 primary problem pages and discussions, 83 proof-claim pages, and the 129 relevant open PRs found by examining all 661 open PRs and their touched files. Dated captures and fetch metadata are archived under `evidence/`; PR metadata and patches are compressed when large. [target-decisions.csv](target-decisions.csv) is the compact decision table; [target-status-audit.json](target-status-audit.json) contains the full notes, target hashes, references, and retained selection.

This is a bounded intake review, not an exhaustive literature search, a verification of each claimed proof, or a formal equivalence proof for every source encoding. A full claim that overlaps the exact target causes a hold; a partial result does not automatically close a different variant. A numbered problem's status is recorded separately from the status of its remaining parts. The 290 existing admissions retain their prior review; they were checked for statement/dependency drift, not freshly reclassified as open.

The particularly important exclusions include #1186's exact k=3 target, covered by the October 5 Toledo claim; #897's growth-filter/prime-power statement defects; and the disputed target-cardinal range in three #1167 formulations. The bounded #1167 infinite-target formulation is reviewed separately. The disputed #654 Erdős–Pach formulation is held.

## Production compatibility and commitments

The source is based on upstream `5a2fb85045c86e4a184b15ce7531ba199a3c4580`, integrated into the existing audited production source. The candidate derived commit is `6cd829f0ab173b10c8d375642f7564729ee29bc9`, reconstructed from base `7d1a8c9912747679d0093f6d1216420c33ee5ffa` and the task repository's `tiers/tier-1/formal-conjectures-audit-fixes.patch`. [source-reproduction.json](evidence/source-reproduction.json) checks that this recipe reproduces the exact pinned tree and commit.

Lean remains **4.35.0-rc2**, with Mathlib `065356127b1dc0016f66b7283ce0ce2c4055aa55`. Comparator, exporter, sandbox, and environment identity remain unchanged. Upstream's 4.33.1 source needed API ports, imports for backports now supplied by Mathlib, the newer question metadata, and a few proof repairs. The unrecognized `optimization_constant` attribute was removed from #513 without changing its constant or proposition.

All 290 existing type hashes and their transitive local kernel constants remain unchanged. In particular, existing geometry definitions are preserved: the newer upstream distinct-distance definitions have separate names for new targets. Of the 264 admitted statements, 258 have unchanged printed statements and six differ only by these explicit helper names (and whitespace). The comparison is recorded in [statement-port-comparison.json](evidence/statement-port-comparison.json).

The rebuilt release catalog contains 2,300 declarations from 570 modules: all 328 requested files plus every retained production target module. It intentionally reports that rebuilt scope in `generated_by`; it does not relabel unrebuilt entries from the earlier full catalog as belonging to the new source pin. The earlier catalog remains available in validator history.

Selection audit schema 3 binds each new admission to the reviewed production type hash and to the hash of its target-status evidence. Catalog extraction rejects transitive statement holes and recognizes `question value` metadata even when an answer definition is filled. This prevents unknown values from silently becoming fixed proposition rewards.

The bundles use dependency identity v2, with per-source-instance build provenance. [dependency-index.json.gz](dependency-index.json.gz) records the complete commitments; each immutable bundle also carries its own commitment. Changed import graphs issue new versions for 258 retained targets even though their statements and definitions are unchanged. The other 32 retained targets keep their exact versions: 64 bundles are reused, and 1,044 new versions are compiled and inspected (528 for new targets, 516 for retained targets). Historical bundles and admissions remain available, and worker routing stays bound to each original source instance.

## Validation and reproduction

Evidence includes the complete 328-module and retained-module builds, extractor safety checks, regression-test results, source reconstruction, statement and dependency comparisons, compilation/inspection of newly generated versions, validation of all bundle commitments, publication/API/worker-routing checks, standard-validator parity, and production-sandbox controls. `publication.json`, `evidence/release-audit.json`, `evidence/standard-validator-parity.json`, and `evidence/runtime/summary.json` record the completed outcomes. The positive sandbox control uses a solved test fixture; it does not prove an open problem.

The final pool integration run passed 14 tests; all 22 sandbox cases and all six standard-validator parity checks passed. The release audit preserved all 1,681 historical versions byte-for-byte and preserved routing for all four earlier verification instances. Ruff and the regression/API test runs passed; their detailed results are archived under `evidence/`.

The scripts in `scripts/` record this local release preparation. Copy them and their sidecar inputs to `<workspace>/build/admission` before rerunning; they are archived evidence rather than an installed CLI. They use sibling validator/task/source checkouts and an isolated container: `/opt/fc-verifier` is the verifier, `/admission` the working evidence directory, and `/candidate-tasks` the candidate tasks checkout. Regeneration starts from the prior task commit `e46f6c64`, reconstructs the source using the pinned patch, builds the release modules and tools, extracts the catalog, prepares reviewed metadata, derives dependencies, and compiles/inspects every generated bundle before committing the local registry/allowlist publication. The optional locally generated pickle cache is not part of the archived evidence and must never be loaded from an untrusted source.

The runtime checks use an unprivileged user, a read-only root filesystem, no network, no added privileges, dropped capabilities, and writable work/tmp mounts. They require the production sandbox; insecure-development mode is disabled. The API checks load real bundles and the real public grouping; no live database or service is modified.

`SHA256SUMS.json` seals this review package. Deployment, external publication, and release activation remain separate actions and were not performed.
