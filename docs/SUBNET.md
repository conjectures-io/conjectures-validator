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
- A submission targets one exact immutable task-bundle digest.
- Payment never changes the verification result.
- Only a proof accepted by the hardened Lean verifier may reach reward review or rewards.
- Manual review, when enabled, gates reward eligibility after Lean succeeds.
- Manual review cannot make a Lean-invalid proof valid.
- Every payment, state transition, verification, review, and reward decision is durable and
  auditable.

## End-to-end flow

1. A miner selects an eligible task and produces a candidate `Main.lean`.
2. The miner transfers 0.5 TAO to the configured payment recipient.
3. The miner calls the submission API with:
   - an idempotency key;
   - authenticated miner identity;
   - the task ID and exact task-bundle SHA-256;
   - the payment transaction or extrinsic reference; and
   - the candidate Lean proof.
4. The API validates request limits, stores the proof in durable content-addressed artifact
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
   timestamp, and policy version are appended to the audit history.
10. Approved proofs and automatically eligible proofs enter the same reward pipeline.
11. The reward process applies a versioned scoring policy, builds the Subnet 66 weight decision,
    submits it to Bittensor, and records the chain result.

```text
RECEIVED
   |
   v
PAYMENT_PENDING
   |
   +-- invalid/unfinalized/reused payment --> PAYMENT_REJECTED
   |
   v
PAYMENT_CONFIRMED
   |
   v
VERIFICATION_PENDING --> VERIFYING
                          |
                          +-- Lean rejected --> VERIFICATION_REJECTED
                          |
                          v
                     LEAN_VERIFIED
                          |
             +------------+------------+
             |                         |
     manual review on           manual review off
             |                         |
             v                         |
   MANUAL_REVIEW_PENDING               |
        |              |               |
        v              v               |
 REVIEW_REJECTED  REVIEW_APPROVED       |
                       |                |
                       +----------------+
                                |
                                v
                       REWARD_ELIGIBLE
                                |
                                v
                       REWARD_PROCESSING
                                |
                       +--------+--------+
                       v                 v
                    REWARDED       REWARD_FAILED
```

Failures must distinguish terminal policy decisions from retryable infrastructure errors.
Retrying a job must never create a second submission, consume a second payment, or duplicate a
reward.

## Validator repository boundary

All validator source and operational configuration belongs in this repository:

| Component | Responsibility |
| --- | --- |
| Submission API | Authenticate miners, enforce schemas and limits, accept paid proof submissions, expose status |
| Payment watcher | Confirm finalized 0.5 TAO transfers and reconcile chain state |
| Durable database | Store the authoritative lifecycle, references, decisions, and audit history |
| Artifact store | Store immutable proof bytes and verifier reports by content digest |
| Job workers | Advance payment, verification, review, and reward jobs idempotently |
| Lean verifier | Decide whether the exact submitted proof proves the exact committed task |
| Review service | Hold and decide Lean-valid submissions when manual review is enabled |
| Reward process | Convert eligible results into versioned Subnet 66 scores and weights |
| Operator tooling | Migrations, monitoring, backups, restores, reconciliation, and incident response |

These components share a repository, not a security context. Payment keys, validator wallet keys,
the network-facing API, and the database must never be mounted into the hostile-proof verifier.
The verifier receives only a read-only task, bounded proof bytes, an expected task digest, and a
fresh disposable workspace.

## Minimum API

The first production API needs this surface:

| Operation | Purpose |
| --- | --- |
| `POST /v1/submissions` | Idempotently create one paid Lean-proof submission |
| `GET /v1/submissions/{id}` | Return payment, verification, review, and reward state |
| `GET /v1/submissions/{id}/report` | Return the immutable verifier report when verification finishes |

`POST /v1/submissions` must require an idempotency key. Reusing the key with the same canonical
request returns the original submission. Reusing it with different task, proof, miner, or payment
data is a conflict.

The API should accept a payment reference, not a client-provided `paid: true` assertion. Payment
truth comes only from the validator's finalized-chain reader.

Submission responses must not imply that payment acceptance, Lean validity, manual approval, and
reward issuance are the same event. Each has its own persisted state and timestamp.

## Durable data model

Use a transactional relational database as the source of truth and durable content-addressed
storage for proof and report bytes. At minimum, migrations need these logical records:

### `submissions`

- server-generated submission ID;
- miner hotkey or authenticated identity;
- idempotency key and canonical request digest;
- task ID and exact task-bundle digest;
- proof artifact digest, size, and media type;
- current state and state version;
- captured manual-review policy and policy version;
- created and updated timestamps.

Unique constraints must prevent duplicate idempotency keys for one miner and conflicting reuse of a
payment reference.

### `payments`

- unique chain transaction or extrinsic reference;
- expected and observed sender and recipient;
- amount stored as an integer in the chain's base unit;
- asset and network;
- observed block and finalized block;
- confirmation/finality state;
- linked submission ID;
- reconciliation timestamps and failure reason.

The required amount is the integer representation of 0.5 TAO. Floating-point numbers must not be
used for payment accounting.

### `artifacts`

- content digest;
- durable object-store key;
- byte length and media type;
- creation timestamp;
- retention and integrity-check state.

The database stores references and hashes; the API process must not rely on its local filesystem for
durability.

### `verification_runs`

- submission ID and attempt number;
- task and proof digests;
- verifier code/version and immutable container digest;
- start and finish timestamps;
- verdict, stable reason code, and stage;
- immutable report artifact digest;
- retry or infrastructure-failure metadata.

### `review_decisions`

- submission ID;
- decision (`APPROVED` or `REJECTED`);
- reviewer identity;
- policy version and structured reason;
- created timestamp.

Review records are append-only. Corrections create a new superseding decision rather than silently
editing history.

### `reward_events` and `weight_batches`

- submission and miner identity;
- reward-eligibility reason;
- scoring-policy version and deterministic score inputs;
- deduplication key;
- Subnet 66 weight batch;
- wallet/account used by the validator;
- chain submission reference and finality state;
- retry, failure, and reconciliation metadata.

### `submission_events`

Every lifecycle transition records the submission, previous and next state, actor or worker,
causation ID, timestamp, and relevant record digests. The current state is queryable efficiently,
while the event history remains append-only and sufficient for audit and recovery.

Use a transactional outbox for database-to-queue work. Workers claim jobs with leases and
idempotency keys so a crash between processing and acknowledgement does not lose work or repeat a
financial action.

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

A plain successful `lake build` is not an accepted result. See [`../SECURITY.md`](../SECURITY.md)
for the exact security boundary and residual risks.

## Current implementation

The repository currently includes:

- deterministic extraction and task generation from the pinned Formal Conjectures revision;
- an audited allowlist of 29 whole-problem Erdős reward tasks;
- immutable task-bundle commitments;
- hardened proof parsing, Comparator checks, Lean kernel replay, and networkless isolation;
- an API-neutral service adapter for bounded proof bytes and exact task digests;
- a finalized-chain reader and pinned Subnet 66 service dependencies.

It does not yet include the miner-facing API, durable database and artifact store, payment
allocation/reconciliation workers, manual review service, reward processor, or end-to-end Subnet 66
weight submission.

## Implementation sequence

1. Add the relational schema, migrations, durable artifact interface, transactional outbox, and
   backup/restore tests.
2. Add miner authentication, `POST /v1/submissions`, status/report reads, strict limits, and
   idempotency behavior.
3. Add finalized 0.5 TAO payment lookup, allocation, reconciliation, and operator repair tools.
4. Add queue workers that invoke the existing verifier adapter and persist immutable reports.
5. Add the captured manual-review flag, review authorization, decision API/UI, and audit history.
6. Add deterministic reward eligibility, duplicate handling, scoring-policy versions, weight
   batches, chain submission, and reconciliation.
7. Add metrics, alerts, rate limits, secret isolation, migrations in deployment, backups, restore
   drills, upgrades, rollbacks, and incident runbooks.
8. Exercise the full staging path from finalized payment to Lean verification, optional review,
   weight submission, and chain reconciliation.

## Decisions still required

1. Which chain address receives the 0.5 TAO and how many finalized blocks are required?
2. Is the 0.5 TAO consumed by every accepted API submission, including a Lean-invalid proof, and
   are any failure classes refundable?
3. How is the miner request authenticated: hotkey signature, API credential, or both?
4. Is manual review configured globally, per task, or per submission policy?
5. What exact review criteria can reject a Lean-valid proof, and is there an appeal process?
6. How are duplicate valid proofs, repeat attempts, and multiple solvers scored?
7. What deterministic rule converts a reward-eligible proof into Subnet 66 weights?
8. What result proves to the miner that a reward decision was included and finalized on-chain?
