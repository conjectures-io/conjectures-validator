# Submission terms

Served as `body_md` by `GET /v1/catalog/submission-terms`, alongside the machine-readable
disqualification list from `submission_api/credits.py`. The prose here explains those
codes; the list is the authority on what they are. Shown to a miner before they spend a
credit, and to a reviewer when they decide, so that no rejection can cite a reason the
miner was never told.

## What a credit buys

One credit buys **one verification attempt**. That is the whole of it.

Payment does not change Lean's verdict, does not reserve a conjecture, does not entitle
you to a review outcome, and does not guarantee a reward. An attempt that the kernel
rejects is an attempt that was made; the credit is spent.

A credit is refunded only when the attempt was never made or could not be made for a
reason on our side — the verifier was misconfigured, the sandbox was unavailable, a pin
rotation invalidated the task mid-flight. A proof that fails is not one of those cases.

## What verification checks

Your proof is compiled in an isolated container against the pinned environment named in
`GET /v1/catalog/meta`, and checked by the Lean kernel and Comparator. It must:

- prove the exact statement in the task's `Challenge.lean`, as published, with its
  published digest;
- depend on no admitted result — no `sorry`, no `sorryAx`, and no lemma that
  transitively relies on one;
- import nothing the task's `forbidden_dependencies` excludes, including the source
  declaration itself;
- close over no axiom outside the task's `permitted_axioms`;
- fit inside the task's declared byte and time limits.

Every one of these is in the machine contract on the conjecture's detail page. Nothing is
checked that is not published there.

## Reward review

A Lean-valid proof is **held for review** before any reward is paid, and review can
refuse it. The reasons it can refuse are exactly the `disqualification_reasons` list on
this endpoint — a reviewer cannot invent one — and a refusal returns the code and any
miner-visible notes.

Verification, review, and reward are three independent states. A proof can be verified and
not approved; approved and not yet paid. Reading one says nothing about the others.

One reward is paid per numbered Erdős family. If an earlier submission solved its parent, a part,
a variant, or the opposite proof/refutation task, a later Lean-valid result in that family earns
nothing.

## Attribution

Certified results are published attributed to conjectures.io. There is no author credit,
and no public endpoint discloses which hotkey produced a result. Per-conjecture activity
is published as salted pseudonyms that cannot be linked across conjectures.

If you need your work attributed to you by name, this is not the right venue for it.

## Your keys and your money

- Amounts are integer rao throughout. 1 TAO is 1,000,000,000 rao.
- Deposits are credited at the amount **observed on chain**, not the amount you declared.
  A short transfer is credited short.
- Credits are non-transferable and are not refundable to TAO.
- The validator never asks for and never holds a secret key. Every signature you provide
  is over a message this service minted, and you can read it before signing.
- A payout needs both a coldkey and a hotkey, because alpha is held as stake. Set them
  together on your account before a reward can be paid.

## Conduct

Do not attempt to overload, probe, or game the validator. Concretely: do not submit proofs
you have no reason to believe are valid in order to map the checker's behaviour, do not
attempt to escape the verification sandbox, and do not submit work that is not yours to
claim. Any of these is grounds for refusing a reward and for closing an account.

## Changes

These terms are versioned. The version and effective date are on this endpoint, and the
version in force when a submission was accepted is the version that governs it — a later
change does not apply retroactively to a submission already made.
