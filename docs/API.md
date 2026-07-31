# Submission API

The miner-facing surface of the Subnet 66 validator, implemented in
[`../submission_api/`](../submission_api/). For the miner's step-by-step path start with
[MINER.md](MINER.md); the bundle format is in [SUBMISSION_BUNDLE.md](SUBMISSION_BUNDLE.md).

Payment buys one verification attempt. It never changes Lean's verdict and does not guarantee a
reward.

**Intake is payment-gated.** The schema has no unpaid state: every payment column on
`submissions` is NOT NULL, so a row exists only for a transfer already confirmed on finalized
chain state. A refused request creates no submission and is recorded in `api_rejection_log`
instead, which is the only trace a miner who paid and was turned away would otherwise leave.

## Endpoints

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/v1/tasks` | none | List submittable tasks, the price, and the payment address |
| `GET` | `/v1/tasks/{task_id}` | none | One task's published commitment |
| `POST` | `/v1/submissions` | hotkey signature | Idempotently create one paid submission |
| `GET` | `/v1/submissions/{id}` | hotkey signature | Verification, review, and reward state |
| `GET` | `/v1/submissions/{id}/report` | hotkey signature | The immutable verifier report |
| `GET` | `/healthz` | none | Liveness |
| `GET` | `/readyz` | none | Readiness: database and task pool |

Task discovery is unauthenticated because the task pool and its digests are public.

## Creating a submission

The bundle is the raw request body; everything else is a header. No multipart, so the request
needs no form parser and the body stays a single content-addressed artifact.

```http
POST /v1/submissions HTTP/1.1
Content-Type: application/zip
Content-Length: 606
Idempotency-Key: ab0002f6-7a99-4352-b478-9da553dcdc1a
X-Conjectures-Hotkey: 5Grw…
X-Conjectures-Timestamp: 1753876543210
X-Conjectures-Signature: 0x4a3f…
X-Conjectures-Task-Id: fc-e923379e-erdos11-erdos-11-7c0303029e-formalized-v1
X-Conjectures-Task-Sha256: sha256:1dfef7…
X-Conjectures-Proof-Sha256: sha256:09da51…
X-Conjectures-Payment-Ref: 0x8b21…

<submission.zip bytes>
```

| Header | Rule |
| --- | --- |
| `Content-Type` | `application/zip`; parameters tolerated |
| `Content-Length` | Required. Over the limit is refused before the body is read |
| `Idempotency-Key` | A **UUID**, matching the column type |
| `X-Conjectures-Hotkey` | The miner's SS58 address, exactly 48 characters |
| `X-Conjectures-Timestamp` | Milliseconds since the Unix epoch, within ±120 s of server time |
| `X-Conjectures-Signature` | 64 bytes of hex, with or without a `0x` prefix |
| `X-Conjectures-Task-Id` | An allowlisted task id |
| `X-Conjectures-Task-Sha256` | That task's published digest |
| `X-Conjectures-Proof-Sha256` | Digest of the archived `Main.lean`, recomputed and compared |
| `X-Conjectures-Payment-Ref` | The finalized extrinsic that funds this attempt |

`201` returns the submission state; an exact replay returns `200`.

```json
{
  "submission_id": "7ee1de44-3708-47ff-a383-9248cdf2b412",
  "hotkey": "5Grw…",
  "task_id": "fc-e923379e-erdos11-…",
  "task_bundle_sha256": "sha256:1dfef7…",
  "proof_sha256": "sha256:09da51…",
  "request_digest": "sha256:48ecf3…",
  "verification_status": "UNVERIFIED",
  "manual_review_status": "UNREVIEWED",
  "reward_status": "INELIGIBLE",
  "failure_reason": null,
  "manual_review_required": true,
  "review_policy_version": "v1",
  "payment": {
    "reference": "0x8b21…",
    "sender": "5DAA…",
    "amount_rao": 500000000,
    "block": 4210031
  },
  "bounty": {
    "amount_rao": 1000000000,
    "policy_version": "flat-v1"
  },
  "verification": { "status": "UNVERIFIED", "report_available": false },
  "created_at": "2026-07-30T15:20:11.025465Z",
  "updated_at": "2026-07-30T15:20:11.025465Z"
}
```

`bounty` is what this submission is owed if it verifies, quoted at intake and frozen on the row.
It is in **alpha** base units, unlike `payment.amount_rao`, which is the TAO rao the miner paid.
A later change to the pricing rule prices later submissions; it never reprices this one, and a
replay returns the same quote. The inputs the rule read are recorded in the database but not
returned — the amount is the miner's business, the treasury state behind it is not.

The three statuses are independent axes, not one lifecycle. A submission always has a
verification status **and** a review status **and** a reward status; collapsing them would imply
that payment acceptance, Lean validity, manual approval and reward issuance are the same event.

Once created, a submission is queued for verification by virtue of
`verification_status = 'UNVERIFIED'` — the partial index `submissions_verification_queue_idx`
is the queue, and a worker claims from it with `FOR UPDATE SKIP LOCKED`. There is no separate
queue table.

## Authentication

The signed message is the canonical **request digest**: the 32 raw bytes of the SHA-256 over
canonical JSON (sorted keys, no spaces, one trailing newline) of exactly these six fields.

```json
{"hotkey":"5Grw…","idempotency_key":"ab0002f6-…","payment_reference":"0x8b21…","proof_sha256":"sha256:09da51…","task_bundle_sha256":"sha256:1dfef7…","task_id":"fc-e923379e-…"}
```

```python
from conjectures_subnet.db.submissions import canonical_request_digest

digest = canonical_request_digest(
    hotkey=keypair.ss58_address,
    task_id=TASK_ID,
    task_bundle_sha256=TASK_SHA256,
    proof_sha256=PROOF_SHA256,
    payment_reference=PAYMENT_REF,
    idempotency_key=IDEMPOTENCY_KEY,
)
signature = keypair.sign(bytes.fromhex(digest.removeprefix("sha256:")))
```

[`../scripts/submit_proof.py`](../scripts/submit_proof.py) does this end to end and
reimplements the digest with the standard library only, so a miner can copy it.

That signature is stored on the submission row (`hotkey_signature`, 64 bytes), so the record
itself carries the proof that this miner authorised this exact request.

Status and report reads sign a different message —
`sha256("conjectures-read-v1:<hotkey>:<submission_id>")` — so a read signature can never be
replayed as a submission.

Three properties worth calling out:

- **The proof is bound.** `proof_sha256` is inside the digest and is compared against the
  archived `Main.lean`, so bytes cannot be substituted under a captured signature.
- **Replay cannot create a second submission.** `payment_reference` is unique, `(hotkey,
  idempotency_key)` is unique, and `proof_digest` is globally unique. A captured request has
  nothing left to consume.
- **Retries still work.** An identical retry returns the original submission, and the body does
  not need re-uploading.

The API performs no chain query for authentication. The hotkey is authenticated here; coldkey
ownership is established by the payment verifier, which reads the chain anyway.

## Payment confirmation

The request supplies a payment *reference*, never an assertion that it paid. Before any write,
the payment verifier must establish that:

- the extrinsic is in a **finalized** block;
- the recipient is the configured payment address;
- the amount is exactly the configured price, in integer rao;
- the sender coldkey **owns the submitting hotkey**; and
- the reference is canonical, so the uniqueness constraint actually prevents reuse.

Amounts are integers in rao, TAO's base unit; 0.5 TAO is `500000000`. No floating point appears
anywhere in payment accounting.

`SUBMISSION_PAYMENT_VERIFIER=chain` is the production setting. The finalized-transfer reader it
needs is the remaining piece of the payment component (see the status table in
[../README.md](../README.md)); until that reader is injected the chain verifier **fails closed**
and refuses every submission with `503 PAYMENT_VERIFIER_UNAVAILABLE`, rather than admitting an
unpaid one. `SUBMISSION_PAYMENT_VERIFIER=development` accepts configured references without a
chain and is refused in production.

## Idempotency

The canonical request digest is the identity of a request, so reusing a key with any field
changed is a conflict rather than a replay.

- Same key, same request → `200` with the original submission; no body needed.
- Same key, anything else different → `409 IDEMPOTENCY_CONFLICT`.
- Concurrent duplicates → exactly one is created; the losers get `200` or `409`.

Uniqueness is enforced by database constraints, not read-then-write checks, so the guarantee
holds under real concurrency.

## Errors

RFC 9457 `application/problem+json`, always carrying a stable `reason_code`:

```json
{
  "type": "about:blank",
  "title": "Submission rejected",
  "status": 422,
  "detail": "submission bundle policy: archive must end with a comment-free end-of-central-directory record",
  "reason_code": "BUNDLE_POLICY_VIOLATION"
}
```

| Status | Cause |
| --- | --- |
| `400` | Malformed header, wrong content type, non-UUID idempotency key |
| `401` | Bad signature, stale timestamp, unknown hotkey (`SIGNATURE_INVALID`) |
| `402` | Payment not confirmed (`PAYMENT_NOT_FINALIZED`) |
| `404` | Unknown task, unknown task digest, or another miner's submission |
| `409` | `IDEMPOTENCY_CONFLICT`, `DUPLICATE_PROOF`, `DUPLICATE_PAYMENT`, or a report requested too early |
| `411` | `Content-Length` missing |
| `413` | Bundle over the size limit |
| `422` | Bundle or proof rejected; see `reason_code` |
| `503` | Validator misconfigured, not ready, or payment confirmation unavailable |

A submission belonging to another miner is `404`, not `403`, so identifiers cannot be probed. A
reason code in the verifier's `CONFIGURATION_REASONS` set means the validator is misconfigured,
not that the miner did anything wrong, and is reported `503`.

Every refusal is written to `api_rejection_log` with its reason code, HTTP status, the claimed
hotkey, the payment reference, the source IP and the user agent. That table has no domains or
foreign keys on purpose: every field is unvalidated client input, and a constraint would refuse
the row precisely when the input is malformed, which is the case most worth logging.

## Configuration

The API configures no database of its own. It reuses the validator's shared store in
[`../conjectures_subnet/db/`](../conjectures_subnet/db/), whose `database_url()` resolves
`DATABASE_URL` or falls back to the `POSTGRES_*` variables from
[`../.env.example`](../.env.example) — the same resolution the migrations use.

| Variable | Default | Notes |
| --- | --- | --- |
| `APP_MODE` | `DEV` | `PROD` hides `/docs`, `/redoc`, `/openapi.json` |
| `DATABASE_URL` | from `POSTGRES_*` | An explicit value wins |
| `POSTGRES_USER` / `PASSWORD` / `HOST` / `PORT` / `DB` | `conjectures`, `conjectures`, `localhost`, `5432`, `conjectures` | Used when `DATABASE_URL` is unset |
| `PAYMENT_RECIPIENT_SS58` | required | The address that must receive the transfer |
| `PAYMENT_AMOUNT_RAO` | `500000000` | 0.5 TAO |
| `GOLD_ALLOWLIST_PATH` | `./gold/allowlist.json` | |
| `GOLD_POOL_ROOT` | `./tasks/gold` | |
| `SUBMISSION_AUTHENTICATOR` | `hotkey-signature` in `PROD` | `development-static-key` refused in `PROD` |
| `SUBMISSION_PAYMENT_VERIFIER` | `chain` in `PROD` | `development` refused in `PROD` |
| `SUBMISSION_DISPATCHER` | `queue` | `in-process` refused in `PROD` |
| `DEVELOPMENT_HOTKEYS` | — | Required by the development authenticator |
| `DEVELOPMENT_COLDKEY` | payment recipient | Sender the development payment verifier reports |
| `DEVELOPMENT_PAYMENT_REFERENCES` | — | If set, the only references the development verifier accepts |
| `NONCE_WINDOW_SECONDS` | `120` | |
| `MAX_BUNDLE_BYTES` | `2097152` | Cannot exceed the verifier policy |
| `MANUAL_REWARD_REVIEW_ENABLED` | `true` | Captured per submission at creation |
| `REVIEW_POLICY_VERSION` | `v1` | |
| `BOUNTY_AMOUNT_RAO` | `1000000000` in `DEV`, required in `PROD` | Alpha base units. Frozen per submission at intake |
| `BOUNTY_POLICY_VERSION` | `flat-v1` | The pricing rule the amount came from |

Every value is validated at startup, so a misconfigured deployment refuses to boot instead of
failing on the first miner request. Four refusals are deliberate fail-closed guardrails:
production will not start with the development authenticator, the development payment verifier,
or the in-process dispatcher — each would otherwise weaken the boundary
[SUBNET.md](SUBNET.md) and [../SECURITY.md](../SECURITY.md) describe — and it will not start
without an explicit `BOUNTY_AMOUNT_RAO`, because inheriting a default there promises money the
operator never chose to promise.

## Running it

```bash
cp .env.example .env                              # then edit the passwords
docker compose -f docker-compose.db.yml up -d     # Postgres + Flyway migrations

export PAYMENT_RECIPIENT_SS58='5C4h…'
export DATABASE_URL='postgresql+psycopg://conjectures:<pw>@127.0.0.1:5432/conjectures'
uvicorn submission_api.asgi:app --host 127.0.0.1 --port 8080
```

The task pool is loaded once at startup and every entry is checked against the audited
allowlist with `GoldTaskRegistry.assert_bundle`, so a task directory whose bytes have drifted
from the published commitment stops the process from starting rather than quietly admitting
submissions against an unaudited task.

## What is not in this module

The durable database is not the API's: it lives in
[`../conjectures_subnet/db/`](../conjectures_subnet/db/) as the runtime view of
[`../deploy/migrate/sql/`](../deploy/migrate/sql/), which is the source of truth. The API holds
no models and no migrations; it borrows a session per request and translates the shared layer's
domain errors into HTTP so that layer stays usable from a worker.

The finalized-payment reader, the verification worker, the review service and the reward
processor are separate components. See [SUBNET.md](SUBNET.md) for the remaining sequence.
