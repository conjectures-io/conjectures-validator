# Whole-pool solution review and 10 MB release — September 8, 2026

The review removes two settled targets and withholds six targets with unresolved claims or a
statement mismatch. The replacement local release has **251 targets / 502 proof-refutation bundles**:
227 Erdős targets and 24 Green targets across 216 numbered source files. This is a dated search for
prior resolutions, not a proof that every surviving problem has never been solved.

The previous review admitted **Erdős 700 part ii incorrectly**. Its source problem's main discussion
and open badge did not expose the accepted solution recorded on the separate proof-claims tab.
This report explicitly supersedes that admission in the
[addition review](../2026-09-08-add-50/REVIEW.md); the earlier evidence is preserved unchanged.

## Withdrawn targets

Both proof and refutation tasks are removed in every row. Quarantine means excluded from admission
pending review; it does **not** mean the claimed proof has been validated.

| Exact target | Finding | Decision |
| --- | --- | --- |
| `Erdos126.erdos_126` | September 2026 proof gives a square-root lower bound, implying the target's growth faster than logarithmic. The source records it as proved with Lean. [Source and proof](https://www.erdosproblems.com/126), [proof repository](https://github.com/tadamcz/erdos126). | Settled; retire |
| `Erdos700.erdos_700.parts.ii` | Claim 157 is accepted by the source site. Stijn Cambie reports checking the paper in detail. The fixed-gap-prime construction gives infinitely many composite n with f(n)² > n. [Accepted claim](https://www.erdosproblems.com/forum/thread/700/proof-claims#proof-claim-157), [review comments](https://www.erdosproblems.com/forum/proof-claims/157/comments). | Settled; retire |
| `Erdos727.erdos_727.variants.k_2` | A September claim supplies an unconditional k=3 construction and a matching Lean k=2 endpoint. Its build was not independently reproduced here. [Claim](https://www.erdosproblems.com/forum/thread/727/proof-claims), [repository](https://github.com/beetree/math_erdos_727). | Quarantine unverified claim |
| `Erdos1059.erdos_1059` | September 5 manuscript and Lean repository claim the full factorial-avoiding-prime theorem. The README notes that its recorded build used a dirty/untracked snapshot and lacks independent replication. [Claim](https://www.erdosproblems.com/forum/thread/1059/proof-claims#proof-claim-264), [repository](https://github.com/beetree/math_erdos_1059). | Quarantine unverified claim |
| `Erdos96.erdos_96` | A paper claims the target linear bound for convex unit distances; the source discussion treats it as unconfirmed and identifies a missing argument. [Paper](https://arxiv.org/abs/1605.08066v2), [discussion](https://www.erdosproblems.com/forum/thread/96). | Quarantine disputed claim |
| `Erdos242.erdos_242` | A 2026 Erdős–Straus solution claim is disputed. Other advertised formal solutions leave unsupported steps. The pinned target also requires distinct denominators, so acceptance of a broader claim would still require a statement comparison. [Paper](https://arxiv.org/abs/2602.11774), [discussion](https://www.erdosproblems.com/forum/thread/242). | Quarantine disputed claim |
| `Erdos12.erdos_12.parts.iii` | Public reciprocal-summability claim leaves a growth axiom/block lemma unproved. It is not an unconditional formal proof. It is withheld conservatively while the underlying claim is unresolved. [Claim](https://www.erdosproblems.com/forum/thread/12/proof-claims#proof-claim-172), [comments](https://www.erdosproblems.com/forum/proof-claims/172/comments). | Quarantine incomplete claim |
| `Green72.green_72` | Pinned statement asserts existence for every N≥3; Green asks eventual impossibility. Those quantifiers make them more than opposite answers to the same proposition. [Correction PR 4941](https://github.com/google-deepmind/formal-conjectures/pull/4941), [Green's source, problem 72](https://people.maths.ox.ac.uk/greenbj/papers/open-problems.pdf). | Quarantine statement mismatch |

The settled classifications use the public source's acceptance/status and statement comparison.
This audit did not independently compile either external solution or adjudicate their authorship.
No reward eligibility or payout decision is made here.

## Scope matters for the surviving targets

Each of the original 259 targets has an entry, pinned type, source hash, decision, and evidence
locators in [target-decisions.json](target-decisions.json). In particular:

- **Erdős 126 upper bound stays:** f=o(n/log n) does not follow from the new square-root lower bound.
- **Erdős 242 Schinzel generalization stays:** a numerator-4 result does not settle all numerators.
- **Erdős 617 stays:** the seven proof claims concern fixed r, rather than every r.
- **Erdős 357, 383, 416, 699, 770 and 982 stay:** the reported bounds, subsequence conclusions, or
  restricted cases do not establish the active statements.
- **Erdős 726 stays:** its advertised full result assumes an unproved equidistribution hypothesis.
  Erdős 12 is withheld because a purported completed proof leaves its central claimed growth step
  unresolved; neither claim is counted as an unconditional solution.
- **Erdős 890, 951 and 978 stay:** counterexamples to older statements fail conditions already present
  in the pinned targets (respectively the corrected counting function, an eventual quantifier, and
  a local divisibility condition).
- **Green 40, 50 and 62 stay:** nonlinear covering, polynomial Freiman–Ruzsa, and three-prime-product
  results do not respectively establish these linear covering, stronger Bogolyubov, and two-prime
  targets.

The withdrawal policy deliberately errs toward withholding unresolved full-scope claims. A
withdrawn target can only return after an explicit new review; a disputed claim is not being
declared correct just to justify its withdrawal.

## Coverage and limitations

The baseline is task release `a0282e5029b6102a64b113aefdfe6f4767136f40`, with 259 targets across
200 Erdős and 22 Green source files. This is the workspace's pinned candidate pool. No production
database or live deployment was queried or modified.

The screen covered all 259 pinned statements against the reviewed upstream source and tracker,
all 200 Erdős source pages and forum threads (965 posts), the separate proof-claims pages for all
15 active source numbers exposing claims, and all 24 associated claim-comment endpoints. It also
used Green's current source document, all 359 open Formal Conjectures PR file lists, 1,121 open
issue records, 584 recently closed issue records, the public StarFleet page, and selected primary
literature searches. Source pages, forums, claim tabs/comments and heads were fetched again on
September 8. GitHub issue/PR snapshots from the preceding same-day audit were reused; their hashes
and source revisions are recorded in [revisions.json](revisions.json).

Issue title matches and forum keyword matches are leads, not proofs. Partial results and suspected
resolutions were compared with the pinned theorem's quantifiers and hypotheses. Upstream name drift
(notably Erdős 913 and 1095) and nested namespace parsing (Erdős 70 and Green 24) were not treated
as disappearance or resolution. The catalogue's absence of formal-proof metadata was not used as
proof that no informal solution exists.

Coverage is not an exhaustive search of every paper, proof repository, language, deleted page,
unpublished argument or older closed GitHub issue. External Lean proof claims were not rebuilt.
The surviving pool therefore has a bounded negative screen, not certified absence of prior work.
The previous semantic/compiled audits remain relevant, but no fresh full-pool Lean compilation or
full independent re-formalization audit is claimed in this follow-up.

## Size limit and immutable release

- Maximum `Main.lean`: **10,000,000 bytes** (decimal 10 MB).
- Maximum ZIP upload: **10,485,760 bytes** (10 MiB), allowing packaging around a full-sized proof.
- The task generator, strict loader, bundle admission, API default cap, standalone builder and
  miner/API documentation use the enlarged policy. Task-specific committed limits still apply.
- All 502 surviving manifests receive new task IDs and bundle digests. Limits over the legacy
  1,000,000-byte policy contribute to the deterministic ID. Old bundles still load with their old
  IDs and limits, but none of the preceding 518 IDs is admitted by the replacement allowlist.
- Stable problem and reward identities are unchanged. Source statements, five trusted payload
  files, trusted-hash records and prior compiled target hashes are byte-identical. This is a
  resource-policy rebuild that reuses compiled commitments.
- Both modes of every withdrawn target are denied; names and canonical source type hashes are
  also added to the retirement inputs. Retired display metadata preserves their statements.
- The task repository's full rebuild script was aligned with the current validator's allowlist
  builder interface and continues to commit the display-only retired metadata digest.

[task-id-migration.json](task-id-migration.json) records every old/new ID and digest.
[release-validation.json](release-validation.json) records full-pool admission, immutable-payload,
retirement and policy checks. The relevant Python suite passed **235 tests**, with **60 database
tests skipped** because the isolated test database was unavailable; see [tests.xml](tests.xml).
The boundary check accepts a 10,000,000-byte proof in an uncompressed ZIP and rejects one byte over.
Retired-display regeneration passes `--check`; the full rebuild entry point loads successfully.

## Local release artifacts

Replacement task commit: **`3763d4ea01f097f9b5685b5dffa47ea089a39905`** on
`codex/review-pool-10mb-20260908`. The validator's `pins.lock.json` points to it.
No commit was pushed and no production deployment was performed.

[tasks-release.bundle](tasks-release.bundle) preserves the release and preceding local task
history, with prerequisite `c1829b7c28bd54a59a9f1f2dcb9834b1cab53cfd`. In a tasks checkout that has
that prerequisite:

```sh
git bundle verify /absolute/path/to/tasks-release.bundle
git fetch /absolute/path/to/tasks-release.bundle refs/heads/codex/review-pool-10mb-20260908
git switch --detach 3763d4ea01f097f9b5685b5dffa47ea089a39905
```

The reusable [migration script](rebuild_reviewed_release.py) documents the retirement, historical
display recovery and policy rebuild phases. The final release also contains the normal full Lean
rebuild command. Before production activation, account for submissions queued against old IDs;
changing the active allowlist closes those IDs to new admission.

[source-evidence.tar.gz](source-evidence.tar.gz) contains captured primary pages and compact
review inputs. Fetch logs identify URLs, UTC timestamps and SHA-256 digests. The
[evidence manifest](evidence-manifest.json) binds this report, release bundle, decisions, scripts,
tests and source archive. All earlier audit artifacts remain unchanged.
