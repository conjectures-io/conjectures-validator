# Submission API

The network-facing surface of the Subnet 66 validator, implemented in
[`../submission_api/`](../submission_api/). For the miner's step-by-step path start with
[MINER.md](MINER.md); the bundle format is in [SUBMISSION_BUNDLE.md](SUBMISSION_BUNDLE.md). The
public read surface the website is built on has its own document,
[PUBLIC_API.md](PUBLIC_API.md).

Payment buys one verification attempt. It never changes Lean's verdict and does not guarantee a
reward.

**Intake is funded up front.** A submission row exists only once money has been confirmed.
Since V003 there are two ways for that to be true and `submissions` carries a CHECK that exactly
one holds per row: an extrinsic-funded submission names the finalized transfer that paid for it,
and a credit-funded one names the ledger entry it was debited from. Neither admits an unfunded
row. A refused request creates no submission and is recorded in `api_rejection_log` instead,
which is the only trace a miner who paid and was turned away would otherwise leave.

## Endpoints

One process serves several audiences on one port. The miner-facing surface authenticates with a
hotkey signature; the public surface authenticates nothing and is read by a browser; the account and
reviewer surfaces authenticate with a session cookie, and the reviewer surface additionally requires
a role.

### Miner-facing

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/v1/tasks` | none | List submittable tasks, the price, and the payment address |
| `GET` | `/v1/tasks/{task_id}` | none | One task's published commitment |
| `POST` | `/v1/submissions` | hotkey signature | Idempotently create one paid submission |
| `GET` | `/v1/submissions/{id}` | hotkey signature | Verification, review, and reward state |
| `GET` | `/v1/submissions/{id}/report` | hotkey signature | The immutable verifier report |

Task discovery is unauthenticated because the task pool and its digests are public.

### Public — see [PUBLIC_API.md](PUBLIC_API.md)

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/v1/catalog/conjectures` | Conjecture list with filters and facet counts |
| `GET` | `/v1/catalog/conjectures/{slug}` | Statement, references, and one `Challenge.lean` plus machine contract per attack direction. `slug` is the stable public identity, **not** the `task_id` a bundle commits to; a task-id URL answers `301` to it |
| `GET` | `/v1/catalog/conjectures/{slug}/activity` | Anonymised per-conjecture activity |
| `GET` | `/v1/catalog/meta` | Pool counts, credit price, treasury, bounty model, pins |
| `GET` | `/v1/results/certified` | Certified results, keyset-paginated |
| `GET` | `/v1/results/in-review` | Lean-verified, awaiting manual review |
| `GET` | `/v1/results/submissions` | Every submission in every state, newest first, for a dashboard |
| `GET` | `/v1/results/{id}` | One published result |
| `GET` | `/v1/results/{id}/report` | The published subset of the verifier report |
| `GET` | `/v1/results/{id}/solution` | The verified `Main.lean`, for an approved result only |
| `GET` | `/v1/system/status` | Submissions open/paused, queue depths, pin rotation |

Public result objects expose `bounty_amount_rao` together with `bounty_amount_usd`. The USD field
is a current TaoStats display conversion, returned as a decimal string rounded to cents, and is
null when the external rate is unavailable. `/v1/catalog/meta` exposes the same conversion for
the total bounty-pool balance as `bounty.balance_usd` beside `bounty.balance_rao`.

### Signed-in account — see [ACCOUNT_API.md](ACCOUNT_API.md)

Session cookie, plus — on writes — an `Origin` on the write allowlist or a same-origin
`Sec-Fetch-Site`. `POST /v1/submissions/preflight` is the one exception: free, unauthenticated,
and it writes nothing.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET`/`POST` | `/v1/auth/session`, `/v1/auth/logout` | Read or end the session |
| `POST` | `/v1/auth/email/request-link`, `/v1/auth/email/verify` | Magic-link sign-in |
| `POST` | `/v1/auth/wallet/challenge`, `/v1/auth/wallet/verify` | Coldkey sign-in |
| `POST` | `/v1/auth/google/callback`, `/v1/auth/google/link` | Google sign-in and explicit account linking |
| `GET`/`PATCH` | `/v1/me` | Profile, roles, linked keys, payout |
| `POST` | `/v1/me/hotkeys`, `/v1/me/hotkeys/challenge` | Link a hotkey by signature |
| `PUT` | `/v1/me/payout` | Payout destination: coldkey plus hotkey |
| `GET` | `/v1/me/credits`, `/v1/me/credits/ledger` | Balance and the append-only ledger |
| `POST`/`GET` | `/v1/me/deposits`, `/v1/me/deposits/{id}`, `/v1/me/deposits/claim` | Buy credits |
| `GET` | `/v1/me/submissions[/{id}[/events|/report]]` | The miner panel |
| `GET` | `/v1/me/rewards` | Payouts with explorer links |
| `POST` | `/v1/submissions/preflight` | Free static check; no credit, no auth |
| `POST`/`PUT` | `/v1/submissions/intents[/{id}/bundle|/confirm]` | Spend a credit and submit |

### Reviewer

Session cookie plus the `REVIEWER` role on the account. A CLI bearer token cannot exercise the role
however the account is set up — see `ACCOUNT_API.md` — so this surface is browser-only. Read-only,
and never cached: every response sets `Cache-Control: no-store`, because these bodies are authorised
per caller and carry review material that is not published anywhere else.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/v1/admin/reviews` | Submissions awaiting a reward decision, each with every advisory assessment recorded against it |
| `GET` | `/v1/admin/reviews/{submission_id}` | One submission's full advisory record, decided or not |

The queue lists `UNREVIEWED` submissions and embeds their `attempts`, because the review panel
renders a verdict per stage on the queue itself — a submissions-only list would be followed
immediately by one request per row. An unassessed submission is on the queue with `attempts: []`:
review is required for it whether or not the advisory service has reached it.

Reading one by id serves any Lean-verified submission, including a decided one, so the advisory
record behind a decision stays readable after the fact. Anything Lean has not verified answers `404`,
matching `/v1/results/{id}`.

Both bodies are allowlists rather than passthroughs. `autoreview.stage_results.verdict` is JSONB
written by a separate repository, so `submission_api/schemas_admin.py` names every field it serves:
an unknown key is dropped, a citation's retrieved page text is never served, and `cost_usd` is a
decimal string so six places of `NUMERIC(12, 6)` survive. Nothing here writes — recording a decision
advances `reward_status`, and that transaction belongs to the service that owns it.

### Operations

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/healthz` | none | Liveness |
| `GET` | `/readyz` | none | Readiness: database and task pool |

`/healthz` and `/readyz` are for the orchestrator: outside `/v1`, so they carry no CORS grant and
are never rate-limited — a `429` to a health probe would take the replica out of service.

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
X-Conjectures-Task-Id: fc-379fc029-erdos11-erdos-11-2bde7d8572-formalized-v1
X-Conjectures-Task-Sha256: sha256:31687f…
X-Conjectures-Proof-Sha256: sha256:09da51…
X-Conjectures-Payment-Ref: 8769916-13-151
X-Conjectures-Public-Credit: eyJuYW1lIjoiRW1teSBOb2V0aGVyIiwib3JjaWQiOiIwMDAwLTAwMDItMTgyNS0wMDk3In0

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
| `X-Conjectures-Payment-Ref` | The finalized transfer that funds this attempt, as `block-extrinsic[-event]` |
| `X-Conjectures-Public-Credit` | Optional canonical base64url UTF-8 JSON: `name`, optional HTTPS `url`, optional checksum-valid `orcid` |

`201` returns the submission state; an exact replay returns `200`.

```json
{
  "submission_id": "7ee1de44-3708-47ff-a383-9248cdf2b412",
  "hotkey": "5Grw…",
  "public_credit": {
    "name": "Emmy Noether",
    "url": "https://example.org/emmy-noether",
    "orcid": "0000-0002-1825-0097"
  },
  "task_id": "fc-379fc029-erdos11-…",
  "task_bundle_sha256": "sha256:31687f…",
  "proof_sha256": "sha256:09da51…",
  "request_digest": "sha256:48ecf3…",
  "verification_status": "UNVERIFIED",
  "manual_review_status": "UNREVIEWED",
  "reward_status": "INELIGIBLE",
  "failure_reason": null,
  "manual_review_required": true,
  "review_policy_version": "v1",
  "payment": {
    "reference": "8769916-13-151",
    "sender": "5DAA…",
    "amount_rao": 500000000,
    "block": 4210031
  },
  "bounty": {
    "amount_rao": 1000000000,
    "amount_usd": "52.14",
    "policy_version": "dynamic-age-v2-locked",
    "available": true,
    "reason": "LOCKED_AT_SUBMISSION",
    "as_of": "2026-07-30T15:20:00Z",
    "locked": true
  },
  "verification": { "status": "UNVERIFIED", "report_available": false },
  "created_at": "2026-07-30T15:20:11.025465Z",
  "updated_at": "2026-07-30T15:20:11.025465Z"
}
```

`bounty` is the Subnet Alpha amount fixed when the API accepted this submission. Replays and later
status reads return the same `amount_rao`, policy, and lock timestamp. The lock remains conditional
on verification, review approval, and winning the shared `reward_target_id`; rejection releases
its treasury exposure. `amount_usd` is a current display value, returned as a decimal string and
rounded to cents. It combines TaoStats' Subnet Alpha/TAO
and TAO/USD prices as `(amount_rao / 1e9) * alpha_price_tao * tao_price_usd`; it is null when
`amount_rao` is null, no TaoStats key is configured, or the
external rate is temporarily unavailable. On approval, the automatically generated payout event
must copy the submission's locked amount, policy version, and inputs exactly.

Submissions accepted before the V012 policy boundary are grandfathered. Their `locked` flag remains
false and the payout event records a fresh payout-time quote; they are never retroactively promised
the historical intake estimate.

The three statuses are independent axes, not one lifecycle. A submission always has a
verification status **and** a review status **and** a reward status; collapsing them would imply
that payment acceptance, Lean validity, manual approval and reward issuance are the same event.

Once created, a submission is queued for verification by virtue of
`verification_status = 'UNVERIFIED'` — the partial index `submissions_verification_queue_idx`
is the queue, and a worker claims from it with `FOR UPDATE SKIP LOCKED`. There is no separate
queue table.

## Authentication

The signed message is the canonical **request digest**: the 32 raw bytes of the SHA-256 over
canonical JSON (sorted keys, no spaces, one trailing newline) of the six base fields below and,
when requested, a seventh `public_credit` object.

```json
{"hotkey":"5Grw…","idempotency_key":"ab0002f6-…","payment_reference":"8769916-13-151","proof_sha256":"sha256:09da51…","public_credit":{"name":"Emmy Noether","orcid":"0000-0002-1825-0097"},"task_bundle_sha256":"sha256:31687f…","task_id":"fc-379fc029-…"}
```

```python
from conjectures_subnet.attribution import encode_public_credit_header, public_credit
from conjectures_subnet.db.submissions import canonical_request_digest

credit = public_credit("Emmy Noether", orcid="0000-0002-1825-0097")
digest = canonical_request_digest(
    hotkey=keypair.ss58_address,
    task_id=TASK_ID,
    task_bundle_sha256=TASK_SHA256,
    proof_sha256=PROOF_SHA256,
    payment_reference=PAYMENT_REF,
    idempotency_key=IDEMPOTENCY_KEY,
    public_credit=credit,
)
signature = keypair.sign(bytes.fromhex(digest.removeprefix("sha256:")))
credit_header = encode_public_credit_header(credit)
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
- **Public credit is consensual and bound.** Its exact name, URL, and ORCID are inside the signed
  digest and snapshotted on the submission. Omitting the header omits name credit; an account's
  mutable display name is never substituted later.
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

`SUBMISSION_PAYMENT_VERIFIER=chain` is the production setting, and it is wired:
`submission_api/chain_payments.py` reads finalized Subtensor state through
`conjectures_subnet/transfers.py`, holding no wallet keys and signing nothing. A verifier built
without a reader still **fails closed** — `503 PAYMENT_VERIFIER_UNAVAILABLE` on every submission,
rather than admitting an unpaid one. `SUBMISSION_PAYMENT_VERIFIER=development` accepts configured
references without a chain and is refused in production.

`BITTENSOR_NETWORK` selects the chain (`finney` is mainnet). `BITTENSOR_ARCHIVE_NETWORK` is an
optional fallback for a reference naming a block outside a lite node's pruned-state window. The
deposit watcher reads the same two variables, so one setting configures both and they cannot end up
reading different chains.

### The payment reference

`block-extrinsic-event`, e.g. `8769916-13-151`. Positional, and **not an extrinsic hash**: a
substrate node can resolve a position and cannot resolve a hash — "get extrinsic by hash" is an
indexer's service, not an RPC — so a hash is a reference this validator would have to take somebody
else's word for. An unresolvable reference is refused with `PAYMENT_NOT_FINALIZED`.

`block-extrinsic` is accepted where that extrinsic moved TAO exactly once, which is the form a block
explorer shows. Where it moved TAO more than once — a `utility.batch` — it is refused with `400
PAYMENT_REFERENCE_AMBIGUOUS`, and the message lists the exact references to choose between. Picking
one would be deciding which payment you meant.

**The canonical three-part form is what gets stored**, whichever form you send. `payment_reference`
is unique, so two spellings of one transfer cannot fund two submissions.

### One transfer buys one thing

A transfer that funded a submission cannot also be credited as a deposit, and vice versa. Both paths
arbitrate through `chain_transfers`, whose reference is unique and which both sides lock before
deciding. Citing a transfer the deposit watcher already credited to an account returns `409
TRANSFER_ALREADY_CREDITED`: the money became credits, so spend one of those instead — see
[ACCOUNT_API.md](ACCOUNT_API.md). Citing one that already funded a submission returns `409
DUPLICATE_PAYMENT`.

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
| `429` | Per-client rate ceiling on `/v1` (`RATE_LIMITED`); carries `Retry-After` |
| `503` | Validator misconfigured, not ready, payment confirmation unavailable, or `SUBMISSIONS_PAUSED` |

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
| `CONJECTURES_TASKS_ROOT` | `../conjectures-tasks` | Separate pinned task-repository checkout |
| `TASK_ALLOWLIST_PATH` | `../conjectures-tasks/allowlist.json` | From the separately pinned task checkout |
| `TASK_POOL_ROOT` | `../conjectures-tasks/pool` | Bundles live under `<root>/<tier>/<task_id>` |
| `SUBMISSION_AUTHENTICATOR` | `hotkey-signature` in `PROD` | `development-static-key` refused in `PROD` |
| `SUBMISSION_PAYMENT_VERIFIER` | `chain` in `PROD` | `development` refused in `PROD` |
| `SUBMISSION_DISPATCHER` | `queue` | `in-process` refused in `PROD` |
| `DEVELOPMENT_HOTKEYS` | — | Required by the development authenticator |
| `DEVELOPMENT_COLDKEY` | payment recipient | Sender the development payment verifier reports |
| `DEVELOPMENT_PAYMENT_REFERENCES` | — | If set, the only references the development verifier accepts |
| `NONCE_WINDOW_SECONDS` | `120` | |
| `MAX_BUNDLE_BYTES` | `2097152` | Cannot exceed the verifier policy |
| `MANUAL_REWARD_REVIEW_ENABLED` | `true` | Captured per submission at creation |
| `REVIEW_POLICY_VERSION` | `v2` | Captured at acceptance; v2 expands `NOT_NOVEL` for exact prior public solutions substantially implemented by the submission |
| `BOUNTY_WALLET_COLDKEY_SS58` | payment recipient | Coldkey owning the bounty stake |
| `BOUNTY_WALLET_HOTKEY_SS58` | required in `PROD` | Hotkey identifying the bounty stake position |
| `BOUNTY_NETUID` | `66` | Subnet whose finalized Alpha balance is `B` |
| `BOUNTY_POOL_BALANCE_RAO` | `4000000000` in `DEV` | Development-only deterministic balance; refused in `PROD` |
| `BOUNTY_POLICY_VERSION` | `dynamic-age-v2-locked` | Version written with catalog quotes and submission locks |
| `BOUNTY_CONSTANT_NUMERATOR` | `1` | Numerator of `c` |
| `BOUNTY_CONSTANT_DENOMINATOR` | `4` | Denominator of `c` |
| `BOUNTY_AGE_PERIOD_SECONDS` | `86400` | One increment in the linear age weight |
| `BOUNTY_BALANCE_CACHE_SECONDS` | `60` | Maximum chain-read frequency per API process |
| `BITTENSOR_NETWORK` | `finney` | Network used for the finalized Alpha-stake read |
| `TAOSTATS_API_KEY` | — | Enables `bounty.amount_usd`; sent only to the TaoStats price endpoints |
| `TAOSTATS_PRICE_CACHE_SECONDS` | `60` | Maximum TaoStats price-read frequency per API process |
| `SUBMISSIONS_PAUSED` | `false` | Refuses intake with `503 SUBMISSIONS_PAUSED`; reported on `/v1/system/status` |

The public read surface adds its own variables; they are documented in
[PUBLIC_API.md](PUBLIC_API.md#configuration) and in [`../.env.example`](../.env.example).

Every value is validated at startup, so a misconfigured deployment refuses to boot instead of
failing on the first miner request. The deliberate fail-closed guardrails include the following.
Production will not start with the development authenticator, the development payment verifier,
or the in-process dispatcher — each would otherwise weaken the boundary
[SUBNET.md](SUBNET.md) and [../SECURITY.md](../SECURITY.md) describe. It refuses a static bounty
balance in production, where the finalized Alpha stake must be read live. On the public side
it will not start with a wildcard or plaintext
CORS origin, with rate limiting disabled, or with either public secret left unset or set to the
published development constant.

## Running it

```bash
cp .env.example .env                              # then edit the passwords
docker compose -f docker-compose.db.yml up -d     # Postgres + Flyway migrations

export PAYMENT_RECIPIENT_SS58='5C4h…'
export DATABASE_URL='postgresql+psycopg://conjectures:<pw>@127.0.0.1:5432/conjectures'
uvicorn submission_api.asgi:app --host 127.0.0.1 --port 8080
```

The task pool is loaded once at startup and every entry is checked against the audited
allowlist with `TaskPoolRegistry.assert_bundle`, so a task directory whose bytes have drifted
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
