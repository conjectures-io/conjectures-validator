# TorchLean submission intake

The central API queues one complete `Kernel.lean` in the torchlean competition database.
Proof checking, model-family board selection, benchmarking and promotion remain coordinator
work. This endpoint does not spend conjecture bounty credits or query retired competitions.

Enable it with a separate database and the restricted `platform_api` login:

```dotenv
COMPETITIONS_ENABLED=1
COMPETITIONS=torchlean
COMPETITION_TORCHLEAN_DATABASE_URL=postgresql+psycopg://platform_api:URL_ENCODED_PASSWORD@torchlean-db:5432/torchlean
TORCHLEAN_SUBMISSIONS_ENABLED=1
TORCHLEAN_REGISTRATION_CUTOFF=2026-10-26T00:00:00Z
DEFLATE_SUBMISSIONS_ENABLED=0
FLOATLIB_SUBMISSIONS_ENABLED=0
```

Set the cutoff to the competition's actual launch policy. It must include a timezone.
Uploads default to disabled. `SUBMISSIONS_PAUSED` also pauses torchlean intake.
The bounty routes and their authentication/payment rules remain separate.

Before enabling uploads, apply central Flyway migrations through **V054**, competition
Alembic migrations through **tensor_0002**, and re-run the competition repository's
`deploy/platform/grant.sh`. These are separate databases and separate migration owners.
The central API never migrates the competition database.

## Signed upload

`POST /v1/competitions/torchlean/submissions` takes exactly one multipart file part named
`Kernel.lean`: nonempty UTF-8, at most **256000 bytes**. The API bounds the full incoming body
before multipart parsing. It does not compile the file during the request.

Headers:

- `X-Conjectures-Hotkey`: submitter's SS58 hotkey.
- `X-Conjectures-Timestamp`: Unix timestamp in seconds, within the configured signature window.
- `X-Conjectures-Signature`: hex sr25519 signature, optionally prefixed with `0x`.

Sign the UTF-8 bytes of these five lines, with no trailing newline:

```text
conjectures-competition-submit-v1
competition: torchlean
digest: <lowercase sha256 of the received file bytes>
hotkey: <SS58 address>
timestamp: <Unix seconds>
```

Signatures made for `floatlib` or `deflate` cannot authorize torchlean uploads.
IP request limits apply before parsing; hotkey limits apply only after signature validation.
Their counters live in the central database and are shared across API processes.

Production intake selects the latest recorded registration for the hotkey. Its UID must still
belong to that registration, its date must be strictly after the cutoff, and it must have no
submission. Every proof state consumes the same registration, including rejection and retries.
A later unused registration is eligible; an older unused registration is never substituted.
The upload transaction shares the coordinator's board-activation lock and database uniqueness
constraints prevent concurrent requests from spending one registration twice.

A successful upload returns HTTP **201**:

```json
{
  "competition": "torchlean",
  "submission": 123,
  "source_sha256": "...",
  "proof_state": "queued",
  "status_url": "/v1/competitions/torchlean/submissions/123",
  "intake_mode": "subnet"
}
```

An identical file from the same hotkey returns **409 DUPLICATE_SUBMISSION**, including
`submission` and `state`, even after re-registration. A client recovering from a lost response
can use that ID. Another upload with a spent registration returns **402 NO_ENTITLEMENT**.
Other reason codes include `NOT_REGISTERED`, `REGISTRATION_BEFORE_CUTOFF`,
`SIGNATURE_EXPIRED`, `SUBMISSIONS_CLOSED`, `RATE_LIMITED`, and
`COMPETITION_SCHEMA_UNAVAILABLE`. Errors use the platform's problem response
`{type, title, status, detail, reason_code}`, with optional extra fields.

## Events

Every request is on Axiom's `request_completed` stream (source `api-competitions`) with its
status and `reason_code`. Uploads that reach the handler also emit `submission_accepted`
(submission ID, hotkey, source digest, size, intake mode) or `submission_rejected` (reason code,
HTTP status, the claimed hotkey and, once the file was read, its digest and size), all tagged
`competition: torchlean`. A miner's refusal is a warning and ours (5xx) an error. Refusals
raised before the handler (closed, paused, database unreachable) appear only on
`request_completed`. The file itself is never sent. The coordinator's events are listed in the
competition repository's `docs/OPERATIONS.md`.

## Polling

The public read API is documented in [TORCHLEAN_READ_API.md](TORCHLEAN_READ_API.md).
Submission status includes proof state, derived processing state and per-board evaluation progress.


## Development uploads

For this competition only, on a development API:

```dotenv
APP_MODE=DEV
TORCHLEAN_DEV_SKIP_REGISTRATION=1
```

A cutoff is then optional. Valid hotkey signatures, file limits, rate limits, duplicate checks
and submission-pause controls still apply. Distinct files can be submitted without registration.
These rows have `intake_mode=dev` and a null registration ID; no chain registration is fabricated.
`APP_MODE=PROD` refuses this setting at startup, with a second guard in the handler.

The updated competition CLI keeps the same command:

```bash
python miner/submit.py submit ./my-kernels --hotkey /path/to/hotkey --url https://dev.example
python miner/submit.py status 123 --url https://dev.example
```

Its default URL remains `https://conjectures.io`; the signed competition slug is now `torchlean`.
