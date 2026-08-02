# conjectures.io Subnet 66 validator

This repository is the complete validator codebase for conjectures.io, Bittensor Subnet 66. The
miner is an external API client. There is no in-repository miner server, commit/reveal transport, or
legacy miner submission protocol.

The validator accepts paid Lean-proof submissions, verifies them, optionally holds valid proofs for
manual reward review, and sends reward-eligible results to the Subnet 66 reward pipeline.

## Core contract

- One submission costs exactly **0.5 TAO**.
- The miner pays and submits a candidate Lean proof through the validator API.
- A payment may fund at most one submission.
- A submission targets one exact immutable proof or counterexample task-bundle digest.
- Proof and counterexample variants share one problem identity and may produce at most one reward.
- Payment never changes the verification result.
- Only a proof accepted by the hardened Lean verifier may reach reward review or rewards.
- Manual review, when enabled, gates reward eligibility after Lean succeeds.
- Manual review cannot make a Lean-invalid proof valid.
- Every payment, state transition, verification, review, and reward decision is durable and
  auditable.

## End-to-end flow

1. A miner selects an eligible `formalized` or `counterexample` task, produces a candidate
   `Main.lean`, then packages it as a `conjectures-submission/v1` bundle
   ([`SUBMISSION_BUNDLE.md`](SUBMISSION_BUNDLE.md)).
2. The miner transfers 0.5 TAO to the configured payment recipient.
3. The miner calls the submission API with:
   - an idempotency key;
   - authenticated miner identity;
   - the task ID and exact task-bundle SHA-256 (the server derives the problem ID and mode from
     the allowlist);
   - the payment transaction or extrinsic reference; and
   - the candidate proof bundle.
4. The API validates request limits, admits the bundle against its exact shape and static Lean
   policy, authenticates the request, and synchronously confirms the canonical payment reference
   against finalized chain state. It checks the recipient, exact amount, successful dispatch, and
   coldkey ownership of the signing hotkey at the payment block.
5. Only after payment confirmation, the API transactionally stores the extracted proof bytes and
   submission, then returns a durable submission ID. The archive itself is not retained.
6. A leased verification worker passes the stored proof bytes and exact task digest into a fresh,
   immutable, networkless verifier container.
7. Static policy, Comparator, and the Lean kernel determine the result:
   - invalid proofs become terminal verification rejections and cannot receive rewards;
   - valid proofs receive an immutable verifier report.
8. Reward gating applies the review policy captured for that submission:
   - if manual review is enabled, the proof enters `MANUAL_REVIEW_PENDING`;
   - if manual review is disabled, it immediately becomes `REWARD_ELIGIBLE`.
9. A reviewer may approve or reject a held proof for rewards. The decision, reviewer, reason,
   timestamp, and policy version are appended to the audit history.
10. Approved proofs and automatically eligible proofs enter the same reward pipeline.
11. The first approved outcome atomically claims its shared `problem_id`. The reward worker commits
    a unique payout record before signing, submits the intake-frozen exact TAO bounty, waits for
    finality, and records the canonical chain reference. An unresolved `PENDING` record blocks
    automatic retries until an operator reconciles whether broadcast occurred, preventing double
    payment.

```text
                       intake (payment confirmed first, or no submission at all)
                                        |
                                        v
verification_status   UNVERIFIED --> VERIFIED ----------------+
                          |                                  |
                          +------> REJECTED (terminal)        |
                                                             v
manual_review_status                            UNREVIEWED --+--> APPROVED --+
                                                     |                      |
                                                     +--> REJECTED          |
                                                                            v
reward_status                                            INELIGIBLE --> ELIGIBLE --> REWARDED
                                                                                 \-> FAILED
```

The three axes are independent columns, not stages of one machine: a submission always has a
verification status AND a review status AND a reward status. `REJECTED` verification is terminal —
review can reject a Lean-valid proof but can never make a Lean-invalid one valid. When review is
not required the APPROVED decision is still recorded, as an `AUTOMATIC` `review_decisions` row,
rather than being left implicit.


Failures must distinguish terminal policy decisions from retryable infrastructure errors.
Retrying a job must never create a second submission, consume a second payment, or duplicate a
reward.

## Validator repository boundary

All validator source and operational configuration belongs in this repository:

| Component | Responsibility |
| --- | --- |
| Submission API | Authenticate miners, enforce schemas and limits, accept paid proof submissions, expose status |
| Finalized payment reader | Confirm finalized 0.5 TAO transfers synchronously at intake |
| Durable database | Store the authoritative lifecycle, references, decisions, and audit history |
| Artifact store | Store immutable proof bytes and verifier reports by content digest |
| Job workers | Advance verification and reward jobs idempotently |
| Lean verifier | Decide whether the exact submitted proof proves the exact committed task |
| Review service | Hold and decide Lean-valid submissions when manual review is enabled |
| Reward process | Reserve, submit, finalize, and reconcile exact TAO bounty payouts |
| Operator tooling | Migrations, monitoring, backups, restores, reconciliation, and incident response |

These components share a repository, not a security context. Payment keys, validator wallet keys,
the network-facing API, and the database must never be mounted into the hostile-proof verifier.
The verifier receives only a read-only task, bounded proof bytes, an expected task digest, and a
fresh disposable workspace.

## Minimum API

The first production API needs this surface, implemented in
[`../submission_api/`](../submission_api/) and documented in [`API.md`](API.md):

| Operation | Purpose |
| --- | --- |
| `POST /v1/submissions` | Idempotently create one paid Lean-proof submission |
| `GET /v1/submissions/{id}` | Return payment, verification, review, and reward state |
| `GET /v1/submissions/{id}/report` | Return the immutable verifier report when verification finishes |

`GET /v1/tasks` was added alongside these so a miner can discover the exact `task_id` and
`task_bundle_sha256` to commit to without an out-of-band channel.

`POST /v1/submissions` must require an idempotency key. Reusing the key with the same canonical
request returns the original submission. Reusing it with different task, proof, miner, or payment
data is a conflict.

The API should accept a payment reference, not a client-provided `paid: true` assertion. Payment
truth comes only from the validator's finalized-chain reader.

Submission responses must not imply that payment acceptance, Lean validity, manual approval, and
reward issuance are the same event. Each has its own persisted state and timestamp.

## Durable data model

The schema now exists: [`../deploy/migrate/sql/`](../deploy/migrate/sql/) is the source of truth,
applied by Flyway, with [`../conjectures_subnet/db/models.py`](../conjectures_subnet/db/models.py)
as the runtime mirror and `scripts/check_schema_drift.py` as the proof they agree. Read the
migration for the authoritative definitions; what follows is why it is shaped the way it is.

`V001` differs deliberately from the sketch this document originally carried, in four ways worth
knowing:

**Payment is a precondition, not a state.** There is no `payments` table. Every payment column on
`submissions` — reference, sender, amount in rao, finalized block, and the hotkey signature — is
NOT NULL, so a row exists only for a transfer already confirmed on finalized chain state. That
removes the whole unpaid-submission state space rather than modelling it. A refused request
creates no submission and is recorded in `api_rejection_log`, which is the only trace a miner who
paid and was turned away would otherwise leave.

**Proof bytes live in the database.** There is no `artifacts` table and no object store: `proofs`
holds the miner's `Main.lean` with `CHECK (digest = pg_catalog.sha256(content))`, so the bytes and
their digest cannot disagree. The table is separate from `submissions` precisely so it can be made
write-once by revoking UPDATE and DELETE from the service role. The submitted archive is not
retained; only the proof it contained.

**Paired outcomes share one durable problem identity.** The server derives `problem_id` and task
mode from the audited allowlist rather than accepting either from the miner. Reward eligibility
uses an atomic database constraint so the first accepted proof or counterexample closes its
sibling outcome without preventing paid verification attempts from being recorded.

**Four independent status axes, not one lifecycle.** `verification_status`,
`manual_review_status` and `reward_status` each move on their own, which is what makes it
structurally impossible for a response to imply that payment acceptance, Lean validity, manual
approval and reward issuance are the same event. `submission_events` records every transition with
a typed foreign key to whichever run, decision or payout justified it.

**The queue is an index, not a table.** A submission is queued for verification by being
`UNVERIFIED`; workers claim from the partial index `submissions_verification_queue_idx` with
`FOR UPDATE SKIP LOCKED`. `verification_runs` rows are inserted once, on completion, because every
column they require is only known after the verifier has finished.

Uniqueness does the concurrency work: `(hotkey, idempotency_key)` for idempotency,
`payment_reference` so one transfer backs one submission, and a global unique on `proof_digest` so
one proof is payable at most once. Amounts are integers in rao; floating point appears nowhere in
payment accounting.

**The bounty is quoted once.** After payment confirmation, intake records the direct TAO amount,
pricing-policy version, and internal pricing inputs on the submission. Winner selection and the
wallet worker copy that amount rather than consulting mutable live pricing.

## Manual reward review

Manual review is a policy gate after deterministic Lean verification. The validator needs a
configuration flag such as `MANUAL_REWARD_REVIEW_ENABLED`, but each submission must capture the
effective value and policy version when it reaches reward gating. Changing the live flag must not
silently change the treatment of an in-flight submission.

When enabled:

- a Lean-valid proof remains `manual_review_status = UNREVIEWED` and reward-ineligible;
- the reward worker must ignore it;
- only an authorized, audited approval makes it reward-eligible;
- a rejection needs a structured reason and remains visible to the miner.

When disabled:

- a Lean-valid proof receives an automatic approval and becomes `reward_status = ELIGIBLE`;
- the transition is still recorded as an automatic policy decision;
- the same atomic winner and payout deduplication rules apply.

The review interface may inspect mathematical novelty, duplication, task eligibility, abuse, or
other reward policy. It must not rewrite the Lean verdict or the submitted proof.

## Lean verification contract

The existing verifier accepts exactly one bounded UTF-8 `.lean` proof against one immutable task
bundle. The service adapter requires the expected task-bundle SHA-256 and preserves the same
verification stages used by the CLI.

Production acceptance requires:

- an allowlisted production task;
- unchanged trusted task bytes and whole-bundle commitment;
- successful static hostile-input policy checks;
- statement identity and permitted-axiom closure through Comparator;
- Lean kernel acceptance;
- the production Landlock/seccomp sandbox.

For `formalized` mode, the committed target is definitionally equal to the source proposition `P`.
For `counterexample` mode, it is definitionally equal to `Not P`. Both use the same static policy,
axiom closure, Comparator statement check, kernel replay, and sandbox. A counterexample result is a
formal refutation; the generic verifier does not claim to extract a displayable witness.

A plain successful `lake build` is not an accepted result. See [`../SECURITY.md`](../SECURITY.md)
for the exact security boundary and residual risks.

## Current implementation

The repository currently includes:

- deterministic extraction and task generation from the pinned Formal Conjectures revision;
- an audited allowlist of 58 proof/counterexample bundles for 29 whole-problem Erdős rewards;
- immutable task-bundle commitments;
- hardened proof parsing, Comparator checks, Lean kernel replay, and networkless isolation;
- an API-neutral service adapter for bounded proof bytes and exact task digests;
- a finalized-chain reader and pinned Subnet 66 service dependencies;
- the `conjectures-submission/v1` bundle format and its exact-shape archive scanner
  ([`SUBMISSION_BUNDLE.md`](SUBMISSION_BUNDLE.md));
- the miner-facing submission API with hotkey-signature authentication, replay protection,
  idempotency, task admission, and status/report reads ([`API.md`](API.md));
- the shared durable schema in [`../deploy/migrate/sql/`](../deploy/migrate/sql/), applied by
  Flyway, with its runtime mirror and the submission seam in
  [`../conjectures_subnet/db/`](../conjectures_subnet/db/);
- content-addressed proof storage in the `proofs` table, and the `api_rejection_log` record of
  every refused request.

The database is a component in its own right, not the API's: every process resolves one URL
through `conjectures_subnet.db.database_url()`, and the payment, verification, review and reward
components reuse the same models and extend the same migration history rather than defining their
own. Adding a migration is adding a file to `deploy/migrate/sql`; see
[`../deploy/README.md`](../deploy/README.md).

It also includes the read-only finalized transfer reader, leased isolated verification worker,
bearer-authorized audited review endpoint, atomic problem-winner claim, and finalized direct-TAO
payout worker. Production environment setup and staging evidence are deployment responsibilities,
not repository state.

## Implementation sequence

1. ~~Add the relational schema, migrations, and durable proof storage.~~ Done in
   [`../deploy/migrate/sql/`](../deploy/migrate/sql/) and
   [`../conjectures_subnet/db/`](../conjectures_subnet/db/). Backup/restore tests and a retention
   window for `api_rejection_log` remain to write.
2. ~~Add miner authentication, `POST /v1/submissions`, status/report reads, strict limits, and
   idempotency behavior.~~ Done.
3. ~~Add a read-only finalized-transfer reader for payment-gated intake.~~ Done; it accepts only a
   successful direct Balances transfer at a canonical finalized `block-index` reference.
4. ~~Add a queue worker that leases submissions, invokes the verifier in a separate trust domain,
   and persists immutable reports.~~ Done in `conjectures_subnet/verification_worker.py`.
5. ~~Add review authorization and the decision API.~~ Done; decisions are append-only, and review
   cannot override the Lean verdict.
6. ~~Add deterministic reward eligibility, cross-mode duplicate handling, chain payout, and
   reconciliation.~~ Done for direct TAO bounties. A PENDING result lost across broadcast is held
   for manual reconciliation and is never automatically signed again.
7. Add deployment-specific metrics, alerts, edge rate limits, secret isolation, backups, restore
   drills, upgrades, rollbacks, and incident runbooks.
8. Exercise the full staging path from finalized payment to Lean verification, optional review,
   winner selection, payout finality, and chain reconciliation.

## Decisions taken

1. **How is the miner request authenticated?** By hotkey signature over a domain-separated
   envelope containing the canonical request digest and a bounded millisecond timestamp. The
   timestamp and signature are stored; idempotency and payment-reference uniqueness make replay
   non-consuming. See [`API.md`](API.md).
2. **What constitutes payment evidence?** A successful direct Balances transfer found by canonical
   `block-index` in finalized state, for exactly 500,000,000 rao to the configured recipient, from
   the coldkey that owned the signing hotkey at that block.
3. **How do opposite outcomes race?** Formalized and counterexample tasks share a server-derived
   `problem_id`; a primary-key insert selects exactly one approved winner in PostgreSQL.
4. **What does this implementation pay?** One exact direct TAO bounty quoted at intake, frozen on
   the submission, pre-recorded before signing, and exposed to the miner from intake through finality.

## Decisions still required

1. Which configured chain address receives the 0.5 TAO in each deployment?
2. Is the 0.5 TAO consumed by every accepted API submission, including a Lean-invalid proof, and
   are any failure classes refundable?
3. Should manual review remain a global captured policy or become per task?
4. What exact review criteria can reject a Lean-valid proof, and is there an appeal process?
5. Should production economics keep direct TAO bounties, or replace the payout gateway with subnet
   weight setting? The durable winner and finality interfaces support either, but this branch
   implements direct TAO payout.
