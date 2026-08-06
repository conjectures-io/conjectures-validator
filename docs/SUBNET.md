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
- Each exact theorem target has its own reward; its proof/refutation modes share that reward.
- Payment never changes the verification result.
- Only a proof accepted by the hardened Lean verifier may reach reward review or rewards.
- Manual review, when enabled, gates reward eligibility after Lean succeeds.
- Manual review cannot make a Lean-invalid proof valid.
- Validator emissions are deliberately treasury-only: UID 121 receives 100% every epoch.
- Every payment, state transition, verification, review, and reward decision is durable and
  auditable.

## End-to-end flow

1. A miner selects an eligible `formalized` or `counterexample` task and produces a candidate
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
4. The API validates request limits, admits the bundle against its exact shape and the static Lean
   policy, stores the bundle and the extracted proof in durable content-addressed artifact
   storage, creates the database records transactionally, and returns a durable submission ID.
5. The payment worker confirms the transfer against finalized chain state. It checks the recipient,
   asset, exact amount, sender policy, and uniqueness of the payment reference.
6. Once paid, a verification job passes the stored proof bytes and exact task digest through
   `verifier/service_adapter.py` into a fresh isolated verifier container.
7. Static policy, Comparator, and the Lean kernel determine the result:
   - invalid proofs become terminal verification rejections and cannot receive rewards;
   - valid proofs receive an immutable verifier report.
8. Reward gating applies the review policy captured for that submission:
   - if manual review is enabled, the proof enters `MANUAL_REVIEW_PENDING`;
   - if manual review is disabled, it immediately becomes `REWARD_ELIGIBLE`.
9. A reviewer may approve or reject a held proof for rewards. The decision, reviewer, reason,
   timestamp, and policy version are recorded as a `review_decisions` row.
10. Approved proofs and automatically eligible proofs enter the bounty payout pipeline.
11. Independently of individual proof results, the emissions worker observes each Subnet 66 epoch
    and submits one weight: treasury UID 121 receives 100%.

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
| Payment watcher | Confirm finalized 0.5 TAO transfers and reconcile chain state |
| Durable database | Store the authoritative lifecycle, references, decisions, and payout records |
| Artifact store | Store immutable proof bytes and verifier reports by content digest |
| Job workers | Advance payment, verification, review, and reward jobs idempotently |
| Lean verifier | Decide whether the exact submitted proof proves the exact committed task |
| Review service | Hold and decide Lean-valid submissions when manual review is enabled |
| Bounty process | Pay eligible proofs from the treasury under the versioned bounty policy |
| Emissions worker | Set 100% of Subnet 66 validator weight to treasury UID 121 every epoch |
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

**Four independent status axes, not one lifecycle.** `verification_status`,
`manual_review_status` and `reward_status` each move on their own, which is what makes it
structurally impossible for a response to imply that payment acceptance, Lean validity, manual
approval and reward issuance are the same event. What justified a given status is recoverable from
the row that caused it: `verification_runs` for a verdict, `review_decisions` for an approval,
`reward_events` for a payout, each carrying its own timestamps.

**The queue is an index, not a table.** A submission is queued for verification by being
`UNVERIFIED`; workers claim from the partial index `submissions_verification_queue_idx` with
`FOR UPDATE SKIP LOCKED`. `verification_runs` rows are inserted once, on completion, because every
column they require is only known after the verifier has finished.

Uniqueness does the concurrency work: `(hotkey, idempotency_key)` for idempotency,
`payment_reference` so one transfer backs one submission, and a global unique on `proof_digest` so
one proof is payable at most once. Amounts are integers in rao; floating point appears nowhere in
payment accounting.

**Bounty estimates are live, not submission locks.** For open target `i`, the versioned policy is
`b_i = c * B * N * w_i / W`, evaluated with integer arithmetic. `B` is the finalized bounty-wallet
balance; `N` and `W` cover only open stable reward targets; and task age comes from the durable
`bounty_tasks.opened_at` row. The submission stores the estimate shown at intake for audit, while
the payout event stores the amount, policy, and inputs actually used. If another queued proof wins
the target first, the later proof can still verify but the bounty is already solved.

**One reward per exact theorem target is a constraint, not a convention.** The pool issues a
`formalized` and a `counterexample` task for each theorem target. `submissions` carries both its
source-pinned `problem_id` and stable `reward_target_id`, derived from the allowlist at intake so a
miner cannot choose the payout identity. `submissions_reward_target_reward_unique` is a unique
index on `reward_target_id` over every row whose `reward_status` is not `INELIGIBLE`. The two modes
and later source repins therefore share one possible reward. Independently formalized parents,
parts, and variants have different identities and independent rewards. `FAILED` remains inside
that predicate so a failed payout keeps its claim while it is retried.

Two things follow, both handled where eligibility is decided rather than left to the index:

- a Lean-valid proof for a problem already awarded is not a verification failure. It is recorded
  as a rejected reward decision with `PROBLEM_ALREADY_AWARDED` and stays `INELIGIBLE`;
- a problem verified in **both** modes has been proved and refuted, which cannot be right — the
  generated negation is not the negation, or something worse. Nothing automatic pays either side:
  an ADVISORY `PROBLEM_VERIFIED_IN_BOTH_MODES` row is recorded and the submission is left
  `UNREVIEWED`, so it enters the human review queue. `submissions_problem_verified_idx` is what
  makes that check cheap enough to run before every award.

## Manual reward review

Manual review is a policy gate after deterministic Lean verification. The validator needs a
configuration flag such as `MANUAL_REWARD_REVIEW_ENABLED`, but each submission must capture the
effective value and policy version when it reaches reward gating. Changing the live flag must not
silently change the treatment of an in-flight submission.

When enabled:

- a Lean-valid proof remains held in `MANUAL_REVIEW_PENDING`;
- the reward worker must ignore it;
- only an authorized, audited approval makes it reward-eligible;
- a rejection needs a structured reason and remains visible to the miner.

When disabled:

- a Lean-valid proof transitions directly to `REWARD_ELIGIBLE`;
- the transition is still recorded as an automatic policy decision;
- the same reward scoring and deduplication rules apply.

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
- an audited allowlist of 272 proof/counterexample bundles for 136 active theorem targets (118
  Erdős and 18 Green) in 136 stable reward targets, with ten additional audited targets recorded
  as retirements and excluded from admission;
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
  every refused request;
- automatic reward eligibility and one-reward-per-theorem-target enforcement;
- the treasury-only epoch worker in [`../emissions_worker/`](../emissions_worker/), which submits
  100% of Subnet 66 validator weight to UID 121 after every observed epoch.

The database is a component in its own right, not the API's: every process resolves one URL
through `conjectures_subnet.db.database_url()`, and the payment, verification, review and reward
components reuse the same models and extend the same migration history rather than defining their
own. Adding a migration is adding a file to `deploy/migrate/sql`; see
[`../deploy/README.md`](../deploy/README.md).

It does not yet include the payment allocation/reconciliation worker, the reviewer-facing
decision service, or the automated proof-bounty payout processor. Subnet emissions do not depend
on proof scoring: they are intentionally routed in full to treasury UID 121 every epoch.

One open operational question the verification worker raises: whatever launches the verifier
container needs a Docker socket, which is root-equivalent on the host. The boundary above is
satisfied — the verifier itself has neither the socket nor the database — but a socket escape in
the process that also holds database credentials reaches the database one hop later. Rootless
podman, a pre-provisioned `systemd-run` unit, or a separate unprivileged runner holding the socket
and no credentials all close it.

## Implementation sequence

1. ~~Add the relational schema, migrations, and durable proof storage.~~ Done in
   [`../deploy/migrate/sql/`](../deploy/migrate/sql/) and
   [`../conjectures_subnet/db/`](../conjectures_subnet/db/). Backup/restore tests and a retention
   window for `api_rejection_log` remain to write.
2. ~~Add miner authentication, `POST /v1/submissions`, status/report reads, strict limits, and
   idempotency behavior.~~ Done.
3. Add the finalized-transfer reader the payment-gated intake needs, plus reconciliation and
   operator repair tools. The API already refuses a submission whose payment cannot be confirmed;
   until the reader is injected, `SUBMISSION_PAYMENT_VERIFIER=chain` fails closed and refuses
   every submission rather than admitting an unpaid one.
4. ~~Add queue workers that claim unverified submissions, invoke the verifier in a separate trust
   domain, and persist immutable reports.~~ Done in
   [`../verification_worker/`](../verification_worker/). Workers claim `submissions`, not
   `verification_runs` — a run row can only be written once the verifier has finished. Claiming
   uses a committed lease rather than a held `FOR UPDATE SKIP LOCKED` lock, because a production
   task declares `timeout_seconds = 3600` and the database terminates any session idle in a
   transaction after 60 seconds.

   The trust boundary is the CLI, not `verifier/service_adapter.py`: the worker runs
   `python -m verifier verify` in a fresh hardened container and reads the report off stdout. The
   adapter is on the container's side of that line, since the worker holds the database
   credentials. Two things still to do: decide who owns the Docker socket (see below), and pay
   attention to submissions parked at the attempt cap, which may be owed a refund.
5. Add review authorization and the decision API/UI. The per-submission manual-review flag and
   policy version are already captured, and the gate is already applied when a verdict is
   recorded.
6. ~~Add Subnet chain weight submission.~~ Done as the intentionally simple treasury-only policy:
   [`../emissions_worker/`](../emissions_worker/) submits one 100% weight to UID 121 every epoch.
   Proof-specific scoring is not part of the emissions policy; automated bounty payout remains.
7. Add metrics, alerts, rate limits, secret isolation, migrations in deployment, backups, restore
   drills, upgrades, rollbacks, and incident runbooks.
8. Exercise the full staging path from finalized payment to Lean verification, optional review,
   bounty payout, treasury weight submission, and chain reconciliation.

## Decisions taken

3. **How is the miner request authenticated?** By hotkey signature over a domain-separated
   payload that includes a nonce and the bundle digest, with the spent nonce recorded under a
   unique constraint. The API performs no chain query; registration and eligibility are decided
   downstream from durable state. See [`API.md`](API.md).

## Decisions still required

1. Which chain address receives the 0.5 TAO and how many finalized blocks are required?
2. Is the 0.5 TAO consumed by every accepted API submission, including a Lean-invalid proof, and
   are any failure classes refundable?
4. Is manual review configured globally, per task, or per submission policy? The API currently
   captures one global flag per submission at creation time.
5. What exact review criteria can reject a Lean-valid proof, and is there an appeal process?
6. How are duplicate valid proofs, repeat attempts, and multiple solvers scored?
7. What result proves to the miner that a bounty payout was included and finalized on-chain?
