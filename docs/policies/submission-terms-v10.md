# Submission terms

**Terms version:** `v10`<br>
**Effective date:** 2026-10-14<br>
**Manual review policy:** `v4`

These terms apply only to submissions accepted on or after the effective date. A submission
accepted earlier remains governed by the terms and review-policy version recorded at acceptance.

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

One reward is paid per exact theorem target. Its proof and refutation tasks compete for that one
reward, including across source repins. Independently formalized parents, parts, and variants are
separate targets and can each earn a reward.

A reward is disqualified as `NOT_NOVEL` if the result was already available in the exact
pinned environment, or a complete, established public proof of the same direct mathematical
problem predates our acceptance of the submission. This includes conventional mathematical
proofs and computer-assisted proofs, even if they were never formalized in Lean. An independent
rediscovery, a different argument, or a first Lean formalization does not restore eligibility.

We must identify the actual prior proof, establish reliable public chronology, match the exact
scope and hypotheses, and explain why the proof is complete and established. An unsupported
claim, inaccessible announcement, intention to prove, or proof of only a different/special case
is insufficient. Material uncertainty about validity, chronology, or target match must be
resolved before rejecting on that basis. `NOT_NOVEL` concerns bounty eligibility and does not
accuse the submitter of plagiarism or declare their Lean proof invalid.

A reward may also be refused when public, dated evidence shows that the same target had already
been completed in an external formal proof system before we accepted the submission. This is
recorded as `PRIOR_EXTERNAL_FORMALIZATION`; it is not an accusation that the miner copied anyone's
work. A claim or announcement that a paper was formalized does not by itself meet this standard
when no inspectable artifact, theorem statement, certificate, or other target-specific record is
available. A mathematical paper may instead be evaluated under `NOT_NOVEL` using the requirements
above.

Bounty amounts shown in the catalog are dynamic estimates. For submissions accepted after the V012
policy activation, a fresh quote is locked and will not be repriced during verification or review.
The lock is conditional: it does not reserve the reward target, and another proof may become the
successful claim first. Rejected attempts release their treasury exposure. If your submission
is approved for the full conjecture bounty, its payout event copies the locked amount and pricing
inputs exactly.

For a `FORMALIZATION_DEFECT_AWARD` under review policy `v3` or `v4`, the reward is **$750 USD equivalent
or the task bounty locked for your submission, whichever is less**, paid in Subnet 66 Alpha.
Convert $750 to integer Alpha rao at the authoritative Alpha/USD rate when the payout record is
created, using the existing nearest-rao rounding convention, then take the smaller of that amount
and your locked bounty. This replaces the full conjecture bounty; it is not an additional reward.
Submissions accepted under earlier review policies retain their recorded award terms, including
the fixed $750 defect award under policies `v1` and `v2`.

Submissions accepted before that activation retain the payout-time pricing policy in force when
they were submitted; the new lock is not applied retroactively.

## Chat logs

**Every open problem requires the AI chat logs that produced the proof.** Upload them **before
submitting the proof**: from a signed-in account on the website, or with `conjectures logs
upload` from a CLI session whose coldkey is linked to your account. A coldkey-signed
submission uses the logs deposited by the account that coldkey is linked to; a coldkey linked
to no account cannot submit. A proof is not accepted without its logs, and no credit or
payment is spent. A reviewer accepts them only if they are the original chat logs that
produced the proof, not a recreation of them. If the logs are incomplete, the reviewer may ask
for more instead of rejecting them: you can then upload a new version, up to three versions in
all, each still the original chats and showing the proof before it was submitted. If none
arrives within 14 days of the request, or the logs are still not enough, they may be rejected.
Rejected or withdrawn logs mean the proof is rejected as `CHAT_LOGS_NOT_ACCEPTED`: no bounty
is paid and the target reopens to other solvers. The separately capped formalization-defect
award retains its existing rules. The [chat-log terms](../trajectories/chat-log-terms-v3.md)
apply.

Requiring logs does not change what a problem pays. Targets first admitted under
`linear-age-v3-chat-logs`, including the P34 and P35 unit-square circle-packing targets, start
at **2.5% of the available Alpha treasury** and grow to 12.5% over 15 days, rounded down to
integer rao and locked at submission. Older targets retain the starting share they opened
with. A submission accepted before these terms took effect keeps the rules recorded at
acceptance and needs no logs.

For full-bounty proofs submitted before chat-log intake opened, accepted logs earn an
additional **$500 USD for Erdős problems**, or **$250 USD for all other problems**, based
on the original task bundle and paid in Subnet 66 Alpha worth that amount when paid. The chat
must show the proof before it was submitted. Claims close one year after intake opened.
Existing proof payouts are not reduced or made conditional on logs. There is no extra log
bonus for a proof that required logs.

Uploading logs grants Conjectures a permanent, worldwide, non-exclusive right to use, copy,
sell and license them to buyers, including for training and evaluating AI systems, once they
are accepted. Logs are not published openly, by us or by buyers. Logs that are not accepted
are deleted within 90 days; accepted logs are kept so they can be sold.

## Attribution and publication

**Your identity is published with your result.** Every result on the public feeds — certified or
awaiting review — names its solver: your account display name if you have set one, and otherwise
the coldkey that signed the submission. Submitting is a public act: assume that anyone can see
which identity attempted which conjecture, and when.

Note that setting a display name publishes it. If you would rather be identified only by an
address, leave it unset.

**Public name credit is optional and permanent for that submission.** You may attach a public
credit name and, optionally, an HTTPS profile URL and ORCID. These values are included in the
request digest your coldkey signs, stored as a snapshot on the submission, and published beside
your solver identity once the result reaches a public feed. They are not taken from your mutable
account display name. Omit public credit and you are credited by display name or coldkey as
above. Do not name another person or
team without their permission.

**An approved proof is published in full.** Once review approves your submission, the exact
`Main.lean` you sent is served at `GET /v1/results/{id}/solution`, together with its digest. A
proof that is still in review, was refused, or failed the kernel is not published.

Per-conjecture activity is still served as salted pseudonyms, but they are weak now and you should
not rely on them: because a verified result names your solver identity and carries its verification time,
the pseudonyms on a conjecture you have a verified result for can be matched back to you by
timing — and with that, your unsuccessful attempts on that conjecture too.

What is still never published: your paying coldkey, your payment reference, the funding extrinsic,
and the verifier's stdout or stderr.

## Your keys and your money

- Amounts are integer rao throughout. 1 TAO is 1,000,000,000 rao.
- Deposits are credited at the amount **observed on chain**, not the amount you declared.
  A short transfer is credited short.
- Credits are non-transferable and are not refundable to TAO.
- The validator never asks for and never holds a secret key. Every signature you provide
  is over a message this service minted, and you can read it before signing.
- A payout needs one coldkey. It does not have to be a key you have proved to us, or even one
  you control — it is only a destination — so check it carefully. Set it
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
