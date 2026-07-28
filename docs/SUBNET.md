# conjectures.io — Bittensor Subnet 66

**conjectures.io** is the pay-to-submit API for Bittensor Subnet 66. A customer pays to submit a
conjecture, the service admits eligible submissions into a formal proof-task pipeline, miners work
on those tasks, and validators reward proofs that pass deterministic Lean verification.

The customer product is the submission API. Customers do not interact with Bittensor
infrastructure; any subnet operation required by the final service stays behind the conjectures.io
service boundary.

Payment controls admission to the pipeline. It never changes the verifier's answer and never makes
an invalid proof acceptable.

## What is being submitted

There are two different submissions in the system and the API must name them clearly:

- A **conjecture submission** comes from a paying conjectures.io customer. It contains the problem
  to be admitted, its metadata, and a payment reference.
- A **proof submission** comes from a subnet miner or solver. It contains candidate Lean source for
  one exact task.

The pay-to-submit API described here is for conjecture submissions. The hardened verifier consumes
proof submissions later in the workflow.

## Current status

This repository is the verification foundation for the service, not yet the finished paid API or
complete subnet.

| Component | Status | What is here |
| --- | --- | --- |
| Audited task pipeline | Implemented | Pinned Formal Conjectures source, 29-task gold pool, deterministic bundles, and SHA-256 commitments |
| Proof verifier | Implemented | Static policy, Comparator, Lean kernel replay, and hardened networkless container execution |
| Production proof handoff | Implemented | Bounded proof bytes, exact task digest, and the same production verifier entry point |
| Public conjecture submission API | Not implemented | No quote, paid submission, status, or result endpoints |
| Payment service | Not implemented | No price calculation, confirmation, idempotency, reconciliation, credit, or refund handling |
| Formalization intake | Not implemented | No path from an arbitrary customer conjecture to an audited Lean task |
| Subnet validator loop | Not implemented | No task scheduling, proof queue, deterministic scoring, or weight submission |
| Production service | Not implemented | No deployed control plane, customer support flow, monitoring, or incident runbook |

## Intended product flow

1. A client requests a price or payment instruction.
2. The client pays and creates a conjecture submission using an idempotency key and payment
   reference.
3. The API verifies that the payment is final and unused, then records it exactly once.
4. The service validates the conjecture's format, eligibility, safety, and duplication status.
5. The conjecture is either rejected/refunded, sent for formalization or review, or converted into
   one immutable Lean task bundle.
6. Validators make the task available as subnet work.
7. Miners produce candidate Lean proofs using any solver or research workflow they choose.
8. Validators run eligible proofs in fresh, isolated verifier containers.
9. A versioned scoring policy converts verification results into Bittensor weights.
10. The API exposes the task status, accepted proof, and verification report to the customer.

```text
customer
   |
   v
quote/payment -> conjectures.io submission API -> durable submission record
                                              |
                                              v
                                  admission and formalization
                                              |
                                              v
                                    immutable Lean task
                                              |
                                              v
                                  Subnet 66 proof market
                                     |             |
                                     v             v
                                   miners      validators
                                     |             |
                                     +-- proof ----+
                                                   |
                                                   v
                                      isolated Lean verifier
                                                   |
                                      +------------+------------+
                                      v                         v
                              Bittensor weights          customer result
```

Today the repository primarily implements the immutable-task and isolated-verifier portions of
this flow.

## Draft submission API

The exact URL and schema are still product decisions, but the minimum useful API surface is:

| Operation | Purpose |
| --- | --- |
| `POST /v1/quotes` | Fix the amount, asset, expiry, and payment instructions for one submission |
| `POST /v1/submissions` | Create one paid conjecture submission idempotently |
| `GET /v1/submissions/{id}` | Return payment, admission, task, solving, and result status |
| `GET /v1/submissions/{id}/result` | Return the accepted proof and verifier report when available |

An initial submission record should contain:

- a client-supplied idempotency key;
- the quote ID and payment reference;
- the conjecture payload and declared payload type;
- title, description, references, and optional contact or callback information;
- terms/policy version accepted by the client;
- a server-generated submission ID, timestamps, content digest, and current state.

The API should use an explicit state machine such as:

```text
AWAITING_PAYMENT
  -> PAYMENT_CONFIRMED
  -> VALIDATING
  -> NEEDS_REVIEW | FORMALIZING
  -> QUEUED
  -> ACTIVE
  -> SOLVED | EXHAUSTED

Any pre-activation state may instead become REJECTED, CREDITED, or REFUNDED.
```

Terminal and retryable failures must be distinguishable. Status changes should be append-only and
auditable even when a worker or API process restarts.

## Payment requirements

The payment layer needs stronger guarantees than a simple “paid” Boolean:

- A quote fixes the amount, asset, network or provider, purpose, and expiry.
- One confirmed payment can fund at most one accepted submission.
- Repeating `POST /v1/submissions` with the same idempotency key returns the same result.
- Reusing a payment reference with a different payload is rejected.
- Webhooks or on-chain notifications are authenticated, replay-safe, and reconciled against the
  payment provider or chain.
- Required confirmation depth or provider finality is explicit.
- Underpayments, overpayments, expired quotes, duplicate payments, chargebacks, and reorgs have
  defined outcomes.
- Invalid or unsupported conjectures follow a published refund or account-credit policy.
- Payment secrets, signing keys, and provider credentials never enter the task builder or proof
  verifier.
- The API persists a financial audit trail without logging credentials or unnecessary personal
  data.

Payment confirmation and submission creation should be one idempotent workflow backed by durable
storage. A client timeout must not cause a second charge or a duplicate task.

## Admission and formalization requirements

The current verifier accepts exact, audited Lean task bundles. A customer may instead arrive with
informal mathematics, a paper reference, a Lean theorem statement, or a complete task bundle. The
service needs an explicit contract for which of these it accepts.

Before a paid submission becomes subnet work, the admission pipeline must:

- validate size, encoding, media type, schema, references, and required metadata;
- screen spam, malicious content, duplicate conjectures, already-solved claims, and unsupported
  subject matter;
- determine whether the customer supplied an informal statement or an exact formal target;
- formalize and independently review informal statements before attaching rewards;
- compile and inspect the target in the pinned Lean environment;
- create the immutable task bundle and externally publish or sign its digest;
- record the relationship between the customer's original text and the exact formal statement;
- provide a dispute and retirement process for incorrect formalizations.

The verifier proves only the Lean proposition. Human or separately governed review remains
responsible for saying that the Lean proposition faithfully represents the paid conjecture.

## What remains for a complete Subnet 66

### 1. Finalize the commercial contract

- Decide exactly what the customer submits and what the fee buys.
- Choose the payment asset/provider, pricing model, finality rule, refund policy, and treasury
  destination.
- Decide whether the fee is only an intake fee, directly funds a solver bounty, or does both.
- Publish service limits, expected timelines, unsupported content, and terms for unsolved tasks.

### 2. Build the paid submission control plane

- Implement quote, submission, status, and result endpoints with strict versioned schemas.
- Add durable relational state, idempotency constraints, payment reconciliation, and an append-only
  event log.
- Store submitted artifacts by content digest outside the web process.
- Add bounded asynchronous queues for validation, formalization, task building, solving, and result
  delivery.
- Add API authentication or an explicit accountless access model, rate limits, abuse controls, and
  safe callback/webhook delivery.

### 3. Build the admission and formalization workflow

- Define automatic eligibility checks and the human-review boundary.
- Add a formalization queue and a review interface for mapping customer text to exact Lean.
- Expand beyond the current fixed 29-task pool without weakening deterministic task generation.
- Version and sign task releases, admission policy, formalization decisions, and retirements.

### 4. Connect accepted tasks to the subnet

- Define deterministic task selection and availability rules for paid tasks.
- Collect candidate proofs without exposing the verifier as a network service.
- Specify scoring for valid proofs, duplicate proofs, copied work, timeouts, and no-solution rounds.
- Submit normalized weights for netuid 66 and make each weight decision reproducible from stored
  inputs.
- Decide how customer payments relate to subnet incentives and avoid promising rewards the service
  cannot fund.

### 5. Return useful customer results

- Expose clear admission, formalization, queue, active, solved, exhausted, refund, and error states.
- Return the exact formal statement before or when a task becomes active.
- Publish an accepted proof only after its task digest and verifier report match.
- Define privacy and publication defaults for customer conjectures, proofs, and contact data.
- Support disputes when the formalization or result does not match the submitted conjecture.

### 6. Productionize

- Isolate the public API, payment service, task builder, subnet processes, proof queue, and
  networkless verifier into separate trust domains.
- Add metrics, tracing, capacity limits, alerts, backups, restoration tests, key rotation, upgrades,
  rollbacks, and incident response.
- Exercise retries and crash recovery across every payment and submission state.
- Run an end-to-end staging test from payment through a verified proof and customer result.
- Deploy reviewed images by immutable digest and keep payment and wallet credentials out of build
  artifacts.

## Existing repository boundary

The current repository already provides:

- deterministic extraction and generation of exact Lean tasks from a pinned Formal Conjectures
  revision;
- an audited allowlist of 29 whole-problem Erdős tasks;
- immutable task-bundle and proof-source commitments;
- hostile-submission checks, statement comparison, axiom-closure checks, and Lean kernel replay;
- a hardened, one-shot, networkless verifier container;
- an API-neutral production adapter that accepts bounded proof bytes and requires the exact task
  bundle digest;
- pinned service/Subnet 66 dependencies, a finalized-chain reader, and local Subtensor testing.

Never put payment credentials, payment webhooks, customer databases, wallets, or network access
inside the verifier container. The API should write a bounded artifact to a queue; a separate worker
should invoke one fresh verifier container for each proof.

The removed legacy miner submission protocol is intentionally not part of this foundation. There
are no commitment, reveal, miner upload, or miner-serving routes in the current codebase.

## Decisions needed

1. What does a customer submit: informal text, a Lean theorem statement, a complete task bundle, or
   more than one of these?
2. Which payment rail and asset should the first version support?
3. Is pricing fixed, complexity-based, auction-based, or manually quoted?
4. Does the payment fund a solver reward, pay only for intake/formalization, or get split between
   both?
5. What is refunded when a conjecture is invalid, already solved, cannot be formalized, or receives
   no valid proof?
6. Are submissions public immediately, public only after admission, or private by default?
7. Who approves the formalization before the task becomes reward-eligible?
8. Do clients need accounts/API keys, or should payment receipts be sufficient for access?
9. How long does a task remain active, and what result does the customer receive if it is not
   solved?

## Verification gates

Documentation and verifier changes should continue to pass:

```bash
.venv/bin/pytest
./scripts/run_integration_tests.sh
docker compose build verifier
docker compose run --rm verifier doctor
git diff --check
git status --short
```

The API, payment lifecycle, formalization workflow, validator scoring loop, and customer result
delivery described above remain to be implemented.
