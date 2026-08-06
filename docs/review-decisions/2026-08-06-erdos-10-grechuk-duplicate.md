# Review decision: Erdős Problem 10, Grechuk variant — duplicate claim

**Publication status:** Draft — one advisory agent assessment recorded; pending the remaining
independent assessments and binding team sign-off
**Decision date:** 2026-08-06
**Submission ID:** `ce95887b-8b61-4a89-9069-9131a58906e0`
**Submitting hotkey:** `5EyGbWh7hkHuRdLnnwyWtfnwHxLDWyzyHqDY2YpLMU7GEFVR`
**Task:** `fc-379fc029-variants-grechuk-e26c885566-formalized-v1`
**Task bundle digest:** `sha256:75dae50947998fe526401998132fe589b236a539908bc9166e8f2b05d8a8f28f`
**Proof digest:** `sha256:784bb738d147dd8b6ad44e1ebf23004a5318cd9f47d4e40185b45209788e1c1d`
**Reward target:** `fc-target:Erdos10.erdos_10.variants.grechuk`
**Acceptance time (paid submission):** 2026-08-06 11:24:37 UTC
**Pinned Formal Conjectures commit:** `379fc0298dc146df549e7061c3ede0353a5bb51f`
**Lean verdict:** `VERIFIED`
**Review outcome:** `REJECTED` — `DUPLICATE_OF_EARLIER_SUBMISSION`
**Review policy:** `v1`

## Decision

The submission is rejected as a duplicate. It is a Lean-valid proof of the exact published task and
the production verifier accepted it, but an earlier eligible submission already holds the same
stable reward target.

This rejection is on chronology alone. No defect, exploit, or misconduct was found in this
submission, and nothing in this decision reflects on the quality of the work. Had it arrived first
it would have been assessed on its merits for the displayed bounty.

## The earlier claim

| | Earlier submission | This submission |
| --- | --- | --- |
| Submission ID | `244ff2d0-399d-4e37-a307-4ff6f3cb3493` | `ce95887b-8b61-4a89-9069-9131a58906e0` |
| Hotkey | `5GeGrYFpMrNSh3Nwcx987zWz4cME9A9NbCkEbjBvv4uLUScV` | `5EyGbWh7hkHuRdLnnwyWtfnwHxLDWyzyHqDY2YpLMU7GEFVR` |
| Reward target | `fc-target:Erdos10.erdos_10.variants.grechuk` | `fc-target:Erdos10.erdos_10.variants.grechuk` |
| Task bundle digest | `sha256:75dae509…8a8f28f` | `sha256:75dae509…8a8f28f` |
| Acceptance time | 2026-08-06 05:12:22 UTC | 2026-08-06 11:24:37 UTC |
| Lean verdict | `VERIFIED` | `VERIFIED` |
| Review outcome | `REVIEW_APPROVED` | `REJECTED` |

The two submissions carry the same task bundle digest and the same stable reward target. Policy `v1`
directs that chronology use the validator's paid-submission acceptance time, not the later
verification or review time. The earlier submission was accepted 6 hours 12 minutes before this
one and has been approved in a
[separate decision](2026-08-06-erdos-10-grechuk.md).

One reward is paid per stable reward target. `submissions_reward_target_reward_unique` enforces this
in the durable schema; this decision records the reviewer-side outcome that matches it.

## What Lean verified

The production verifier accepted the exact formalized task committed by the submission. Its record
(`verification_runs`, verifier `validator-bcda2bde517b829a8b44ea2a387d78674f7e6495`, sandbox
`landrun+seccomp`, container
`sha256:305cc1e8dd13e5301dad759e381d6272287d011cc336db62b7a4be5593903d0f`) shows:

- `accepted: true` and `reason_code: VERIFIED`;
- `same_statement: true`, `challenge_built: true`, `solution_built: true`, `lean_kernel_passed: true`;
- only the permitted axioms `propext`, `Quot.sound`, and `Classical.choice`;
- no `sorry`, no `native_decide`;
- report digest `sha256:eadb15934037eed08b05859d60da9cd0cde72b2c9e711d158e6a074b1122e2b8`.

Note for the record: unlike the earlier submission, this proof states its final target longhand as
`Set.Infinite ({n : ℕ | Even n} \ Erdos10.sumPrimeAndTwoPows 3)` rather than binding it through
`fcTypeOfName%`. That is the usual route by which a substituted statement would enter, so the
Comparator's `same_statement: true` result is cited here explicitly rather than left implicit. The
statement check passed and no substitution occurred.

## On the relationship between the two proofs

The two submissions are independent formalizations of the same published mathematics, not copies of
one another. Both implement the classical Crocker covering-congruence construction; this submission
says so openly, using the namespaces `CrockerFermatProduct`, `CrockerUniformMersenneGCD`,
`CrockerTwoAdic`, and `CrockerCRTAssembly`, and describing itself as bundled from hash-locked
modular artifacts.

The two proofs share the identical 28-row covering table — the same moduli and the same primes in
the same order, from `(3, 7)` through `(360, 168692292721)` — and both use the certified factor
`45592577` of `F₁₀`. That overlap is explained by a shared published source, which is exactly the
kind of overlap policy `v1` says is *not* evidence of copying: proving the same target, using
standard results, or following a conventional strategy does not establish misattribution.

`MISATTRIBUTED_WORK` does not apply to either submission. `DUPLICATE_OF_EARLIER_SUBMISSION` is the
only applicable code, and it applies on time of acceptance alone.

The broader finding that this target was probably not open when it was published is recorded in the
[decision on the earlier submission](2026-08-06-erdos-10-grechuk.md) and applies to the task, not to
this miner.

## Disposition

- Reject under `DUPLICATE_OF_EARLIER_SUBMISSION`; the submission remains `INELIGIBLE` and outside
  the public results feed.
- Preserve the submission, verifier report, and review evidence for audit.
- Publish this rationale at a stable public link and retain that link in the review evidence, as
  required while the public decision feed is unimplemented.
- State plainly in the miner-visible explanation that the rejection is chronological and that no
  defect was found in the work.

## Team and agent review record

One advisory agent assessment has been recorded to date (Claude Opus 5, 2026-08-06), citing both
submissions' `Main.lean`, the shared task bundle digest, the two acceptance times, and both
verification reports. It found the duplicate reward-target claim and found no defect in this
submission.

Before publication as the final binding decision, record the remaining independent LLM or agent
assessments, the human team reviewer or reviewers, any material disagreement, and how the team
resolved it. This decision is contingent on the earlier submission remaining approved: if
`244ff2d0-399d-4e37-a307-4ff6f3cb3493` is later rejected on reconsideration, this submission
inherits the reward target and must be reviewed on its merits under a new append-only decision.

This decision must not be represented as having completed an automated multi-agent review process;
the runner is not yet implemented.

## Reconsideration

The miner may submit contrary evidence or request reconsideration under the
[manual reward-review policy](../MANUAL_REVIEW_CRITERIA.md). Any correction must be recorded as a
new append-only decision that supersedes this one.
