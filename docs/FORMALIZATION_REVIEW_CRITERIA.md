# Formalization reward-review policy

**Policy version:** `formalization-v1`  
**Applies to:** submissions accepted with this review-policy version against an audited
`formalization` task under task policy version `1`.

This policy incorporates the verification, review process, outcomes, evidence requirements,
and reason codes in [manual review v2](MANUAL_REVIEW_CRITERIA.md), with the following changes.
It does not change the contract of submissions accepted under v1 or v2.

## Known mathematical proofs are allowed

The task asks for a Lean proof of a known mathematical result. Implementing the cited paper,
or another published mathematical proof of the exact result, is expected and allowed.
Neither prior mathematical publication nor correspondence with its argument supports
`NOT_NOVEL` for this track. The prior-publication branch of v2 does not apply.

`NOT_NOVEL` still applies if the exact result was already available in the pinned environment.
`PRIOR_EXTERNAL_FORMALIZATION` still requires the dated, public, completed, target-specific
formal artifact described in v2. An informal paper or an ambiguous formalization announcement
is insufficient. `MISATTRIBUTED_WORK` concerns evidence of falsely claimed work; implementing
an attributed mathematical argument is not itself misattribution.

## Exact target and evidence

Review the immutable bundle identified by the submission digest. Record its `track`,
`policy_version`, `resolution_reference`, exact theorem, pinned revision, and target hash.
The reference must resolve the exact proposition with the same assumptions and quantifiers;
a conditional reduction or a related theorem is not a resolution of the task.

For a conjecture disproved in the literature, admission must select a source theorem stating
the known negation. There is one formalized task for that statement, not a competing proof
and counterexample pair. The verifier always checks the published Lean statement.

The usual statement-fidelity, task-defect, forbidden-dependency, permitted-axiom, and kernel
requirements remain in force. Rewards are determined by the quote recorded at acceptance;
this track does not introduce a separate price or treasury.
