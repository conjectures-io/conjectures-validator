# Task-pool candidate audit — 2026-07-28

## Result

There are not 500 candidates under the current direct-proposition admission
policy.

A full multiline declaration-type rescan corrected the initial lexical count.
The first pass reported 434 `research open` theorem or lemma declarations with
no `answer(sorry)` hole, but it missed 31 answer holes split across multiline
types. The corrected global direct-declaration ceiling is therefore **403**,
already below 500 before freshness, PR, collision, source-accuracy, or
compilation checks.

After freshness, current Erdős statuses, and the conservative
resolution/correction PR screen, the first pass produced 303 declarations in
186 source files, including the 29 current `tier-1` reward problems. Deeper review then:

- removed 21 answer-hole declarations missed by the first parser;
- removed two `type_of%` aliases that merely point at canonical conjectures;
- rejected `Green19.green_19.upper`, whose own source says the exact bound was
  already shown; and
- placed `Mahler32.mahler_conjecture` on hold because open PR 4204 changes its
  exact theorem signature.

That leaves a **provisional ceiling of 278 total direct tasks across 168 source
files**, including the current 29, or **at most 249 additional tasks**. This is
still a review queue, not a certified admission set.

The complete row-level inventory is in
[`candidates/DIRECT_CANDIDATES.md`](candidates/DIRECT_CANDIDATES.md), with
machine-readable records in
[`candidates/direct-candidates-2026-07-28.json`](candidates/direct-candidates-2026-07-28.json)
and explicit manual decisions in
[`candidates/review-decisions.json`](candidates/review-decisions.json).

## Pinned inputs

- Formal Conjectures:
  [`f7349f32ba6df6e7b7baf77467a3c6c7777a634d`](https://github.com/google-deepmind/formal-conjectures/commit/f7349f32ba6df6e7b7baf77467a3c6c7777a634d)
- Erdős Problems:
  [`2e7e7a630f9814f3df562bc1b207d9ad41451a55`](https://github.com/teorth/erdosproblems/commit/2e7e7a630f9814f3df562bc1b207d9ad41451a55)
- Open Formal Conjectures pull requests: 300, queried from all GitHub REST
  pages on 2026-07-28.

The [published Formal Conjectures browser](https://google-deepmind.github.io/formal-conjectures/)
reported 1,171 open statements when checked. The newer source snapshot contained
1,173 `research open` attributes, so this audit used the source snapshot for
classification.

## Funnel

| Screen | Declarations | Source files | Meaning |
|---|---:|---:|---|
| `research open` attributes | 1,173 | 629 | All open-tagged declarations, including answer holes |
| First-pass exact theorem/lemma types | 434 | 266 | Initial lexical classification; later found to contain 31 missed multiline answer holes |
| First-pass not used by either retired release | 347 | 217 | Removes 87 still-open declarations from the preliminary queue |
| First-pass current Erdős tracker status allowed | 340 | 213 | Removes seven declarations whose parent is now proved or disproved |
| Preliminary post-PR queue | 303 | 186 | Conservative path-level screen of current open PRs |
| Full type rescan | 282 | 172 | Removes 21 missed answer-hole declarations from the post-PR queue |
| Canonical pointer deduplication | 280 | 170 | Removes two `type_of%` aliases |
| Confirmed known-result removal | 279 | 169 | Removes Green 19's already-known upper bound |
| Current provisional queue | 278 | 168 | Holds Mahler's conjecture target pending open statement-correction PR 4204 |

The final row is an upper bound, not a certified admission set. It can only
shrink after:

- elaborating every declaration with the pinned Lean and Mathlib versions;
- checking exact canonical-type collisions with proved declarations;
- confirming the selected theorem closes the whole source problem rather than
  a part, bound, or variant;
- reviewing the non-Erdős source literature for subsequently published
  solutions, counterexamples, and statement corrections;
- checking source citations and formalization fidelity;
- compiling and independently inspecting every generated target.

## What remains in the 249

The provisional additions are not 249 interchangeable, ready-to-publish
tasks:

- 68 are Erdős declarations. Every one is a named part or variant, shares its
  source with other candidates, or otherwise needs grouping review. There are
  no new lane-A whole-looking Erdős problems in this set.
- 63 are whole-looking, single-candidate non-Erdős declarations. These are the
  best next review lane, but each still needs an independent literature and
  formalization check.
- 118 are non-Erdős declarations that also need structural, grouping,
  docstring, or open-PR review.
- The set includes six Millennium problems and two fresh WOWII conjectures,
  which should be treated as difficulty extremes rather than evidence of
  near-term solver throughput.

The earlier feasibility screen intersects 66 of these declarations. Sixty-five
are explicitly named parts or variants. The remaining declaration is an
Erdős 349 partial-range statement whose source has a large active stack of
partial-result PRs. That feasibility screen therefore does not produce a new
clean batch of whole numbered Erdős problems under the current policy.

## External-status spot checks

Recent result titles can overstate what they remove from this queue, so the
exact quantifiers were compared for several high-risk cases:

- [Baek's moving-sofa preprint](https://arxiv.org/abs/2411.19826) proves that
  Gerver's sofa attains the maximum area. The formal candidate is the stronger
  uniqueness statement, so it remains under review.
- [Montgomery, Pokrovskiy, and Sudakov](https://arxiv.org/abs/2001.02665)
  prove the Ringel and cyclic-shift statements for sufficiently large trees.
  The two formal candidates quantify over every finite tree.
- The [2025 Latin-tableau paper](https://www.combinatorics.org/ojs/index.php/eljc/article/view/v32i2p48)
  proves partial results rather than the full formal candidate.
- A [2026 determinantal-conjecture paper](https://www.sciencedirect.com/science/article/pii/S0024379526001266)
  still describes the general normal-matrix case as open.
- A [July 2026 no-line paper](https://arxiv.org/abs/2607.05255) resolves the
  no-`(k+1)`-in-line problem for `k >= 3` and sufficiently large grids, while
  explicitly leaving the `k = 2` no-three-in-line case formalized here open.

These checks support retaining those exact targets, but they are spot checks,
not a substitute for the remaining 181 non-Erdős literature reviews.

## Pull-request interpretation

“No open PR” needs a precise definition. Taken literally as “no open PR touches
the source file,” zero current source files pass: open
[PR #4631](https://github.com/google-deepmind/formal-conjectures/pull/4631)
is a repository-wide module-system change and touches all 850 current Lean
source files.

The useful policy is therefore the one used above: block an active proof,
disproof, status change, or statement correction for the candidate, while
recording unrelated mechanical changes. The 303 count is deliberately
conservative because it excludes every direct declaration in a source file
whose open PR title indicates a resolution or correction, even if the PR only
changes a companion declaration.

## Written on the Wall II

WOWII is allowed at the task-pool level.

Current `main` contains 22 open direct WOWII declarations. Only four source
files have no apparent active resolution or correction PR:

- `GraphConjecture40.lean`
- `GraphConjecture61.lean`
- `GraphConjecture133.lean`
- `GraphConjecture144.lean`

The last two were already used by a retired release. Under the current freshness
rule, WOWII therefore contributes two fresh direct candidates: Conjectures 40
and 61. These still need compiled and mathematical review.

## A possible route to 500

After the corrected type classification, the screened source queue also
contains:

- 434 screened proposition-answer wrappers, typically
  `answer(sorry) ↔ P`;
- 109 screened non-proposition answer holes.

The initial report gave 432 proposition wrappers and 90 value-answer
declarations after screening. The multiline rescan reclassified two additional
proposition wrappers and 19 additional value-answer declarations that the first
parser had incorrectly placed in the direct queue. The combined pre-deduplication
source-question count remains 825, but only the provisional 278 direct entries
fit the current direct-proposition protocol.

Those wrappers are not valid direct-proposition tasks under the current policy.
The current pool publishes committed `P` and `¬P` bundles but counts them as one
reward problem, because only one polarity can be true; the solver chooses a
bundle and supplies a proof of that selected proposition. Non-proposition
answers require a separately audited finite or literal answer type; most of the
90 remaining declarations
use unsupported types such as real numbers, functions, sets, or asymptotic
classes and must not be counted as usable without new verifier support.

## Recommendation

Keep `tier-1` as 29 compiled whole-problem rewards with 58 paired task bundles.
Continue reviewing the 249 provisional additional direct candidates, expecting
the count to fall during compiled, grouping, and mathematical review. Treat a 500-task target as
a later protocol project:
it requires an answer-wrapper tier with solver-selected polarity, not merely a
larger allowlist.
