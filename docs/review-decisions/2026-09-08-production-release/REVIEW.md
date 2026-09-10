# Production pool release — September 8, 2026

This release reconciles the earlier September 8 reviews with the actual production source pin.
It retains 202 of the 208 live targets, withdraws six, and adds 57 reviewed targets, producing
**259 targets / 518 proof-refutation bundles**, comprising **235 Erdős and 24 Green targets**
across **223 numbered source files**. Forty-nine additions survive the earlier fifty-candidate
review; eight further targets replace the settled or withheld candidates.

The source stays at Formal Conjectures `8432eac998110a563e03df65a28c117e97c8c142`, based on
`7d1a8c9912747679d0093f6d1216420c33ee5ffa` and the existing audited patch, on Lean **4.33.1**.
Production was at validator `b2f0c3306e63e5861d2950223d154c3000ed9cf8` and tasks
`68bb8ea0968994e0f36a2db015dbdbcc13902416`. The earlier local review used Lean 4.27;
its task commits are preserved as evidence and are superseded by this production release.

## Withdrawals

Erdős 126's main target is settled. Erdős 12 part iii, 96, 242's main target, 727's k=2
variant, and 1059 are withheld because of unresolved, disputed, or incomplete full-scope
proof claims. Both modes of each live target leave admission. Green 72 was already withdrawn
in production; Erdős 700 part ii was in the proposed additions and is excluded because of
its accepted solution. Their old names and canonical source types are denied as well.

The [whole-pool review](../2026-09-08-pool-review/REVIEW.md) provides the individual evidence
and distinctions between full statements and surviving partial targets. There are 24 historical
retirement log entries, of which 22 have previously published bundles recoverable for display.
Historical display records retain the source revision of the statement actually offered.

## Eight replacements

| Target | Statement fidelity and prior-result distinction |
| --- | --- |
| `Erdos10.erdos_10` | Uniformly bounded numbers of powers of two, with repetition, plus one prime. The source's eventual formulation is equivalent: each finite exceptional integer at least two is 2 plus copies of 1. The solved three-power even-number variant does not settle existence of some uniform bound. [Source](https://www.erdosproblems.com/10) |
| `Erdos18.erdos_18b` | Subpolynomial growth of the maximum minimum divisor-sum length for factorials. Including the factorial itself adds only a one-divisor representation. The July claim settles the other question, about infinitely many arbitrary practical numbers; the claim discussion explicitly distinguishes factorials. [Source](https://www.erdosproblems.com/18), [discussion](https://www.erdosproblems.com/forum/proof-claims/131/comments) |
| `Erdos128.erdos_128` | Triangle-free sparse halves at the exact 1/50 threshold. The cardinal inequality implements floor(n/2), as the source specifies. The August claimed improvement to 0.0262 is above 0.02 and does not imply this threshold. [Source and claim](https://www.erdosproblems.com/forum/thread/128/proof-claims) |
| `Erdos263.erdos_263.parts.i` | Every positive integer sequence asymptotic to 2^(2^n) has irrational reciprocal sum. Positivity makes the inverse ratio equivalent; convergence is automatic at this growth rate. The pinned definition includes strict monotonicity. The folklore growth results and all-but-countably-many bases theorem do not cover this fixed base. [Source and claims](https://www.erdosproblems.com/forum/thread/263/proof-claims) |
| `Erdos828.erdos_828` | For every integer shift a, infinitely many natural n satisfy φ(n) dividing n+a in the integers. Special negative-shift families and finite positive-shift searches do not settle the universal statement. [Source](https://www.erdosproblems.com/828) |
| `Erdos386.erdos_386.variants.two` | Infinitely many n with n choose 2 a product of consecutive primes. The lower-bound condition excludes the edge cases, and the indexed prime product permits an arbitrary consecutive block. Finite examples, sparsity estimates, bounded-block finiteness, and conditional product-spacing results do not decide infinitude. [Source and claim](https://www.erdosproblems.com/forum/thread/386/proof-claims) |
| `Erdos366.erdos_366` | Existence of a positive 2-full n followed by a 3-full n+1. Positivity excludes the vacuous zero case; n=1 fails the second condition. Examples in the reverse order and an ABC-conditional finite upper bound do not decide this existence claim. [Source and claim](https://www.erdosproblems.com/forum/thread/366/proof-claims) |
| `Erdos653.erdos_653` | Asymptotically n distinct pinned-distance counts. The supremum ranges over realizable n-point sets and is bounded by n. Counting self-distance adds one to every count and preserves how many distinct counts occur. The recent sublinear-defect upper-bound improvement remains compatible with the proposed asymptotic lower bound. [Source and claim](https://www.erdosproblems.com/forum/thread/653/proof-claims) |

Every replacement's numbered source file is byte-identical between the production pin and the
reviewed upstream head `2c817e975be7a95478b72a8429155ca568e1a3de`. The source tracker snapshot is
`5308c57c700559416b9f205df274b136784203e7`. All eight source pages, forum threads, separate
proof-claim tabs, and associated claim comments were fetched on September 8. The earlier same-day
snapshots of 359 open PR file lists, 1,121 open issues, and 584 recently closed issues were searched
for these additions; source-touching changes were distinguished from resolution claims.

Other candidates were withheld, including recent full-scope claims for 64, 260, 279, 742, 971,
1002, 1061, 1139, and 1212, and the accepted disproof of 193. Erdős 295 has an unfinished
existence theorem inside its counting definition; Erdős 855 uses nonuniform nested eventual
quantifiers. Erdős 509 offers a stronger proposed route rather than a completed proof; it was
not chosen while that route remained unverified. None is admitted to fill the quota. This review does not adjudicate the validity
of the withheld proof claims or make any payout determination.

## Reconciliation with production

The 251 surviving targets from the earlier local review all exist as direct open propositions
in the production catalog. Comparing their source files and shared definitions identified these
substantive differences requiring attention:

- Erdős 41 uses multisets for equal-length sums, allowing repeated summands as intended.
- Erdős 479 quantifies over integers other than one, matching the informal question.
- Erdős 952 adds a logically equivalent `True ↔` wrapper.
- The distance helpers used by Erdős 91 and 1082 move namespaces without changing their
  meaning; the triangle helper used by Erdős 600 moves into the shared `SimpleGraph` helper library on the newer pin.

The complete type and source comparison records accompany this report. The earlier semantic
notes remain bounded manual reviews, not independent formal equivalence proofs of every statement.

## Resource policy and validation

Every active manifest permits **10 MiB = 10,485,760 bytes** of proof text; the ZIP envelope permits
**12 MiB**. The production Nginx body limit also moves from 4 MiB to 12 MiB; the website
reads its proof and bundle limits from the API machine contract. This preserves the current validator's binary-megabyte policy. The earlier local
report's decimal 10 MB policy is superseded here. A limit above the legacy 1,000,000-byte contract
contributes to each deterministic task ID. All 416 preceding production IDs leave the allowlist.
The 404 retained bundles preserve every trusted payload and compiled target hash; newly generated
targets are compiled and independently inspected in both modes on the production toolchain.

The strict type-dependency check passed for all 259 proposed targets: no type contains `sorryAx`,
and no definition reachable from a type depends on an axiom outside `propext`, `Classical.choice`,
and `Quot.sound`. This is stronger than checking only the top-level catalog flag.

The core API, database, upload, task-identity, and deployment suite passed **317 tests**,
including the uncompressed 10 MiB intake/persistence boundary and the one-byte-over rejection.
The additional pool, retired-display, and worker suite passed **73 tests**. The full SQL migration set matches the ORM across **811 schema objects**. All **114 new bundles** compiled and passed independent target inspection.

The replacement tactic screen made **672 attempts** across 16 bundles. Its 32 compile hits all
depended on `sorryAx`; none was an admissible proof. No target-type collision was found in the
pinned catalog. Compilation, tactic-screen, and release-admission records are linked by the
evidence manifest. The standalone command-line bundle builder uses the same 10 MiB cap; its bundle suite passed **133 tests**, including exact-limit round-trip and one-byte-over refusal. Production activation is recorded separately after image construction. Successful compilation establishes the committed formal target and its
negation; it does not itself establish fidelity to informal mathematics or absence of prior work.

The literature screen is a dated, bounded search. It cannot certify absence of unpublished work,
unindexed papers, deleted sources, or every prior solution in every language. Negative search
results are recorded as “no full solution found in reviewed evidence,” never as proof of novelty.
