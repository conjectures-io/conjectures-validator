# Manual reward-review policy

**Policy version:** `v4`<br>
**Effective date:** 2026-10-12<br>
**Applies to:** submissions accepted with `review_policy_version = "v4"` on or after
this date

## Relationship to earlier policies

This is the prospective successor to [v3](policies/manual-review-v3.md). A complete,
established public proof of the same direct problem, published before acceptance, now
supports `NOT_NOVEL` regardless of whether it was formalized in Lean or another proof
system and regardless of whether the submission independently rediscovered it. Source-to-
submission copying or substantial use is no longer required for this eligibility reason.

Submission-time policy versions, locked amounts, artifacts, and earlier decisions remain
recorded unchanged. This release does not automatically re-adjudicate existing approvals.
Any specifically authorized historical correction must identify its original contract,
the new rule or other basis being applied, the approving human authority, supporting
proof evidence, and the superseded decision. In particular, the October 12 owner-directed
Wilf e=4 correction is an explicit individual reconsideration, not an assertion that v3
always contained v4's rule. Other potentially affected cases require separate review.

The capped formalization-defect award from v3 is unchanged; v1/v2 contracts retain their
recorded terms. Existing payout amounts are never repriced by this change.

## Purpose

Manual review determines the reward outcome of a submission that has already passed the
validator's automated checks and the Lean kernel.

Review does not replace verification. A reviewer cannot approve a proof that Lean rejected,
change the submitted proof, or judge it against a different task. The reviewer decides whether a
verified submission:

1. earns the displayed conjecture bounty;
2. earns the formalization-defect award capped at the lower of $750 or its locked task bounty; or
3. is rejected for one of the published disqualification reasons.

The exact task bundle accepted at submission is the submission's contract. It includes the task
ID, `Challenge.lean`, bundle digest, machine contract, pinned source revision, submission terms,
and review-policy version. Later source edits or policy changes do not alter that record.

## Two mandatory stages

### Stage 1: Lean verification

A submission must first pass the production verification pipeline, including the machine-contract
checks, Comparator checks, permitted-axiom checks, and the Lean kernel check against the exact task
and proof digests recorded for the submission.

Only a submission with a final `VERIFIED` verdict may enter manual reward review. A failed,
incomplete, stale, or mismatched verification cannot be approved by a reviewer, a model, or the
team. Manual review may classify a Lean-valid result; it may not override Lean.

### Stage 2: team review with independent agent assessments

Each Lean-verified submission must be reviewed by humans from the team in conjunction with
independent assessments from multiple current state-of-the-art LLM or agent systems. The team and
agents should test the proof and review evidence from different angles, including:

- whether the Lean theorem matches the intended informal conjecture;
- whether the proof relies on a task defect, forbidden dependency, or unpermitted assumption;
- whether the result was already present in the pinned environment;
- whether a dated public source had already solved the same direct mathematical problem;
- whether that earlier source supplies a complete proof, including inspectable computational
  evidence where needed, rather than an unsupported announcement;
- whether a claimed external formalization is public and specific enough to establish the exact
  reward target;
- whether concrete chronology or attribution evidence affects eligibility; and
- which published outcome and reason code the evidence supports.

The agents assist the team throughout the review, but their assessments are advisory. They do not
approve, reject, or pay a submission. Humans from the review team own and sign the binding decision
and must resolve material disagreement rather than treating a model vote as conclusive.

For audit, retain each assessment's model or agent identifier and version, review-policy version,
verdict, cited evidence, and concise justification. Do not rely on an unrecorded model response.
The team may consult a qualified mathematician or other specialist when the agents do not resolve a
material mathematical question.

## Review outcomes

### 1. Full bounty — `REVIEW_APPROVED`

Approve the displayed conjecture bounty when:

- the production verifier accepted the exact committed task;
- the Lean statement faithfully represents the intended conjecture;
- no earlier eligible submission holds the same reward target;
- the work is not shown to have been copied or falsely attributed;
- the result was not already available in the pinned environment;
- no established public proof of the same direct problem predates acceptance;
- the target was not already established by a qualifying external formalization; and
- no abuse or other published disqualification reason is established.

If the evidence is insufficient to establish either a formalization defect or a disqualification
reason, use `REVIEW_APPROVED`. Do not reduce or deny a reward based on speculation.

### 2. Formalization-defect award — `FORMALIZATION_DEFECT_AWARD`

Use this outcome when:

- the production verifier accepted the exact committed task;
- the proof or refutation succeeds because the published Lean statement materially differs from
  the intended informal conjecture;
- the result therefore does not genuinely settle the intended conjecture; and
- no disqualification reason applies.

The miner receives **$750 USD equivalent or the task bounty locked for the submission,
whichever is less, paid in Subnet 66 Alpha**, instead of the full conjecture bounty.
This is an approved result, not a rejection and not an additional payment.

Convert $750 to integer Alpha rao using the authoritative Alpha/USD price source used by the
bounty system when the payout record is created, rounding to the nearest rao as for earlier
awards. Pay the smaller of that conversion and `submission.bounty_amount_rao`; a v3 or v4 submission
must have a submission-time bounty lock. Record the $750 ceiling, price, source, timestamp,
uncapped Alpha conversion, locked bounty cap, actual Alpha amount, and payout-policy version
`formalization-defect-capped-v2`. Do not use the task's later catalog quote as the cap.

A material formalization defect includes an omitted or added hypothesis, an incorrect domain or
quantifier, the wrong notion of convergence or equality, an incorrect negation, or another
difference that changes the mathematical problem being solved.

The following are not material defects:

- changes to comments, references, names, formatting, or file organization;
- adding another independent subproblem to the same source file;
- refactoring that preserves the theorem's meaning; or
- a later source correction that does not affect the submitted theorem.

After this outcome:

- quarantine or correct the affected task before accepting another submission for it;
- preserve the submitted bundle and review evidence for audit; and
- do not publicly describe the result as solving the intended informal conjecture.

The public explanation must identify the mismatch, state what the Lean artifact actually proved
or refuted, explain why that did not settle the intended conjecture, and state that the miner
received the capped formalization-defect award, stating the actual Alpha amount and which limit applied.

### 3. Rejection

A verified submission may be rejected only with one of the following codes. The reviewer must
record evidence that directly supports the selected code.

| Code | When it applies | Evidence required |
| --- | --- | --- |
| `ADMITTED_DEPENDENCY` | The proof depends on `sorry`, `sorryAx`, or a declaration that transitively depends on an admitted result | The dependency or axiom trace identifying the admitted declaration |
| `TRIVIALISED_STATEMENT` | The submitted artifact weakened, replaced, restated, or made the challenge vacuous | A diff or elaborated declaration showing how the submitted artifact changed the target |
| `FORBIDDEN_IMPORT` | The proof imports a source declaration or module forbidden by the machine contract | The import trace and violated machine-contract entry |
| `UNPERMITTED_AXIOM` | The proof's transitive axiom closure contains an axiom outside `permitted_axioms` | Kernel or export evidence identifying the axiom |
| `NOT_NOVEL` | The result was already available in the pinned environment, or an established public proof resolved the same direct problem before acceptance, regardless of formalization language, independence, or source use | The existing declaration and pinned revision, or the complete prior proof, reliable public chronology, exact scope/hypothesis match, and the reviewer's basis for accepting the proof as established |
| `PRIOR_EXTERNAL_FORMALIZATION` | Before acceptance, the same reward target had already been formally established in a publicly documented external proof system | A public, dated proof record identifying the formal system and completed result, plus an explanation that it matches the exact reward target |
| `DUPLICATE_OF_EARLIER_SUBMISSION` | An earlier eligible submission already holds the same stable reward target | The earlier submission, its acceptance time, and the matching reward target |
| `MISATTRIBUTED_WORK` | Reliable evidence shows that the submission substantially reproduces work the submitter is not entitled to claim | The earlier source, public timestamp, and distinctive overlap |
| `ABUSE` | The submission attempts to overload, probe, escape, or otherwise game the validator | Relevant request, rate-limit, sandbox, or incident evidence |

A defect in the task published by the validator is not `TRIVIALISED_STATEMENT` when the miner left
the challenge unchanged and proved or refuted it exactly. Use `FORMALIZATION_DEFECT_AWARD` instead.

The first four rejection reasons should normally be caught automatically. If manual review finds
one, treat it as a verifier incident: stop affected payouts, preserve the evidence, repair the
automated check, and inspect other potentially affected submissions.

## Prior publication and attribution

Use the validator's paid-submission acceptance time for chronology, not the later verification or
review time.

### A solution was published after submission

A solution first made public after the validator accepted the submission does not disqualify the
miner. Approve the submission if it otherwise qualifies, even when the two proofs are similar.

### A solution was published before submission

Use `NOT_NOVEL` when a complete, established proof of the same direct informal problem
was publicly available before the validator accepted the submission. An ordinary mathematical
paper or a reproducible computer-assisted proof can qualify. Lean formalization, another proof
assistant, peer review, and third-party endorsement are not mandatory formats, but the reviewer
must substantively assess the proof and document the basis for treating it as complete and valid.

Match every relevant hypothesis, quantifier, domain, direction, and exceptional case. A proof of
a special case, a related theorem, or a stronger-assumption variant does not establish the exact
target. Record a reliable public timestamp; an author-controlled commit date alone is insufficient.
A prior proof published only after acceptance does not disqualify the submission.

Inspect the actual proof. An unsupported claim, planned proof, inaccessible announcement, or
unverified assertion that a formalization exists is insufficient. For a computer-assisted proof,
identify the mathematical reductions, finite coverage, arithmetic guarantees, code/certificates,
and reproducibility evidence, and disclose which checks were independently replayed. Resolve a
concrete gap or material uncertainty before using prior proof as a binding rejection rationale.
A temporary, explicit payout/task hold can preserve the situation while that review continues;
uncertainty is not itself evidence for `NOT_NOVEL`.

An independent new proof, a different argument, or the first Lean formalization of an already
proved problem does not earn this open-problem bounty. Correspondence or copying is not required.
`NOT_NOVEL` is an eligibility decision, not a finding of invalidity, plagiarism, or misconduct.

Use `MISATTRIBUTED_WORK` separately only when reliable evidence shows that the submission
substantially reproduces work the submitter is not entitled to claim. Distinctive overlap may
include the same unusual construction, intermediate lemmas, ordering, errors, notation, or other
choices unlikely to arise independently. Merely proving the same target, using standard library
lemmas, following a conventional strategy, or producing a necessary witness is not sufficient
evidence of misattribution.

The reviewer must cite the earlier source, establish that it was public before submission, and
explain the distinctive overlap for a misattribution allegation. Uncertain attribution does not
establish `MISATTRIBUTED_WORK`; independent discovery does not defeat a documented prior-proof
`NOT_NOVEL` decision.

## Ambiguous external-formalization evidence

A public statement that a paper was formalized does not by itself establish
`PRIOR_EXTERNAL_FORMALIZATION` when the formal artifact, theorem statement, certificate, or other
target-specific record is unavailable. In that situation, the completion and exact target match
remain ambiguous, even if the statement is attributable and credible.

An ambiguous formalization announcement may be retained as supporting context, but it cannot be
the decisive evidence for `PRIOR_EXTERNAL_FORMALIZATION`. A separate public mathematical paper can
still support `NOT_NOVEL` when the complete, established proof directly resolves the exact problem
and its public date precedes acceptance, whether or not the submission uses its solution.

A stated intention to formalize, a claim that code will be published later, an informal proof of a
related claim, or a formal proof under materially different hypotheses does not qualify as a prior
external formalization. If the timestamp, completion, accessibility, or target match remains
genuinely uncertain, do not reject under that code.

## Required review record

Review the immutable submission record, including:

- submission ID and acceptance time;
- task ID, task mode, reward target, and task bundle digest;
- proof digest and exact submitted `Main.lean`;
- pinned source and dependency revisions;
- verifier version, sandbox mode, report, checks, and axiom closure;
- earlier claims on the same reward target;
- any earlier public mathematical source asserted under `NOT_NOVEL`;
- the exact prior-proof scope, chronology, completeness assessment, and any reproduction limits; and
- any source, attribution, or formalization evidence relevant to the decision.

Every binding decision must record:

- `APPROVED` or `REJECTED`;
- one available reason code;
- the governing policy version;
- the reviewer and decision time;
- the advisory agent assessments and any material disagreement among them;
- a concise miner-visible explanation; and
- citations or immutable evidence supporting the decision.

Use `REVIEW_APPROVED` or `FORMALIZATION_DEFECT_AWARD` with `APPROVED`. Use only a published
disqualification code with `REJECTED`.

## Review procedure

1. Confirm that the submission is `VERIFIED` and that the report matches the proof and task
   digests under review. Stop if it does not.
2. Confirm the production verifier, sandbox, Comparator result, pins, and axiom closure.
3. Have humans from the review team examine the submission in conjunction with independent
   assessments from multiple current state-of-the-art LLM or agent systems under this policy
   version, and retain those assessments.
4. Check whether an earlier eligible submission holds the reward target.
5. Compare the committed Lean statement with the intended conjecture closely enough to identify a
   material formalization defect.
6. For `NOT_NOVEL`, establish that an inspectable complete prior proof solved the exact direct
   problem before acceptance. Record the source, scope, chronology, and validity assessment;
   source use, copying, Lean formalization, and lack of independence are not required.
7. For `PRIOR_EXTERNAL_FORMALIZATION`, distinguish a target-specific public proof record from an
   ambiguous announcement that a formalization exists.
8. Evaluate concrete attribution or abuse evidence. Do not conduct an open-ended search merely to
   create doubt after a valid result arrives.
9. Resolve material disagreements among the agent assessments. If specialist mathematical
   judgment is necessary, record the authoritative source or qualified expert basis used.
   Unresolved uncertainty favors `REVIEW_APPROVED`.
10. Record one of the three outcomes and its corresponding reason code. The team reviewer, not an
    agent, signs the binding decision.
11. Publish the decision rationale with supporting links.
12. If a formalization defect was found, quarantine or correct the task independently of the
    payout.
13. If a qualifying prior public solution was found, suspend or retire the affected task before
    accepting another submission for it.

## Public explanation

Publish a concise rationale for every binding approval and rejection. It must state:

- what was decided;
- which reason code and policy version were used;
- the decisive facts and timestamps;
- whether the full locked bounty or the lower-of-$750-or-locked-bounty defect award applies;
- for `NOT_NOVEL`, the exact earlier result, public date, target match, and basis for treating
  the complete prior proof as established, including any computation replay limits;
- how the independent agent assessments agreed or materially disagreed;
- how the team resolved any material disagreement;
- supporting public links where available; and
- how the miner can submit contrary evidence or request reconsideration.

Do not use vague conclusions such as "not meaningful," "too similar," or "not novel enough."
State the concrete facts that satisfy the selected outcome. Do not publish a pending or rejected
proof unless a separate publication policy permits it.

Publish the review team's decision rationale and the evidence needed to understand it. Do not
publish private model chain-of-thought, hidden reasoning traces, security-sensitive validator
details, personal data, or unpublished proof bytes. A concise summary of what each assessment
checked, the conclusion it reached, and the evidence it cited is sufficient.

## Current implementation status

The production path already enforces Stage 1: only a Lean-verified submission can reach the manual
review gate, and manual review cannot override a failed verifier verdict.

The data model can retain append-only binding decisions, advisory LLM evidence, internal reviewer
notes, and a separate redacted `notes_public` rationale. The miner's submission detail and the
world-readable certified-result API return only the binding decision and `notes_public`; internal
notes, reviewer identity, and raw agent evidence remain private.

As of 2026-08-06, the reviewer-facing decision service records the binding decision, published
reason code, separate internal notes, and required public rationale. The automated multi-agent
review runner is not yet implemented. A team can perform the multi-agent review manually and write
the approved rationale to `notes_public`, after which the APIs publish it with the binding
decision. A review must not be represented as having completed an automated multi-agent process
when it did not.

The certified-result detail endpoint publishes the current binding rationale for verified
submissions, including rejections. Preserve supporting evidence and a stable decision link.

## Reconsideration and corrections

A miner may request reconsideration and provide contrary evidence. Review decisions are
append-only. A corrected decision is a new record that supersedes the earlier decision; the
original remains available for audit.

## Related policy

- [Submission terms](SUBMISSION_TERMS.md) define the miner-facing contract and published review
  codes.
- [Security policy](../SECURITY.md) defines the automated trust boundary.
- Verification, review, reward eligibility, and payout are separate states.

Any material change to this policy requires a new `REVIEW_POLICY_VERSION`. Do not reinterpret
earlier contracts silently. Explicitly authorized historical reconsiderations must disclose
the original contract and the new basis, as specified above.

Unsigned payout instructions may be cancelled and a decision superseded through the audited
[operator workflow](PAYOUT_CANCELLATION.md). Cancellation preserves proof, amounts, chain facts,
and earlier decisions; it is never an on-chain revocation.
