# BendTT API

The centralized service serves `/v1/competitions/bendtt` using a distinct `bendtt`
competition kind and database. It does not import the checker or run Lean in HTTP handlers.
The committed OpenAPI document contains the actual request/response schemas.

## Provisioning

In the BendTT competition repository, apply Alembic migration **0003**, then provision a
limited login with an explicit database intake mode:

```bash
just db-migrate
just api-login bendtt_api --mode dev --output /private/path/bendtt-api.env
```

Use `--mode subnet --network finney --netuid 66 --minimum-registered-block BLOCK` for
registered intake. This writes credentials with mode 0600; do not commit or print the file.
It grants allowlisted reads and atomic intake functions, not worker/coordinator authority.
If a separately restricted setter role is used, it additionally needs INSERT/UPDATE on
`api_reward_policy` for the new policy publication. No database role is provisioned by
starting the API. The coordinator publishes the active profile/corpus; the setter publishes
its effective reward policy. Start upgraded services only after migration.

Load the generated settings in the centralized API's environment and add:

```dotenv
COMPETITIONS_ENABLED=true
COMPETITIONS=bendtt
BENDTT_SUBMISSIONS_ENABLED=true
```

Retain other competition slugs if serving them too. Each must have its own database URL.
A dev bypass must match the database's provisioned mode and is forbidden under APP_MODE=PROD.
The bypass skips subnet registration only: signatures, source bounds and rate limits remain.
Dev uploads store `intake=dev` and are excluded from live rewards. Fixture chain namespaces
must match the configured network; dev bypass does not require a running watcher.

Production registration eligibility uses the watcher's active observations on the configured
network/subnet, a strict `registered_block > BENDTT_MIN_REGISTERED_BLOCK` cutoff, and one
upload per canonical registration event. Coldkey observations do not create a new entitlement.
A stale chain yields 503 rather than incorrectly declaring the hotkey unregistered.

## Submitting

Send **one Lean source file** containing `Submission.check` and `Submission.correct`.
The local filename is arbitrary; the multipart **field** must be `Submission.lean`.
The correctness contract is `Pilot.Contract Submission.check`; HTTP acceptance does not
mean the proof passed.

Sign this exact UTF-8 text, without a trailing newline:

```text
conjectures-competition-submit-v2
competition: bendtt
digest: <lowercase SHA256 of exact file bytes>
hotkey: <SS58 hotkey>
timestamp: <integer Unix seconds>
request_id: <canonical lowercase UUID>
```

POST `/v1/competitions/bendtt/submissions`, with headers `X-Conjectures-Hotkey`,
`X-Conjectures-Timestamp`, `X-Conjectures-Signature` (64 bytes in hex), and
`Idempotency-Key` (the signed UUID). Reusing the key with the same file returns the original
submission and current state (200); a new upload returns 201; changing its digest gives 409.
A fresh timestamp/signature may retry an older request ID. Use another UUID for a repeated
dev experiment. The digest is checked from received bytes, never a caller-provided digest.

## Public reads

- Overview, `/policy`, `/eligibility?hotkey=...`, `/health`.
- `/submissions`, `/submissions/{id}`, `/proof`, `/evaluations`, `/report`, `/source`.
  The latter four are relative to `/submissions/{id}`.
- `/evaluations/{id}`, `/evaluations/{id}/report`, `/evaluations/{id}/attempts`.
- `/attempts/{id}`, `/attempts/{id}/measurements`.
- `/measurements/{id}/members`, `/champions`, `/corpus`.
- `/rewards`, `/weight-sets`, `/weight-sets/{id}/allocations`.

Growing collections have `{items,next_cursor,as_of}`, default 25/max 100 items. Cursors are
signed and bound to endpoint/filters/parent/competition and an insertion high-water mark.
Restart page one for current mutable state. Measurements support `after_id` for tailing,
mutually exclusive with `cursor`. Unknown/repeated query parameters are rejected.
BIGINT IDs/nanosecond counters are decimal strings. Speedup > 1 is faster; 3% time reduction
means >= 1/0.97 speedup. Whole-request wall time is scored; inner checking/parsing/memory
are telemetry. Baseline grounding does not affect admission. Failed attempts retain partial
observations and completed batch members; incomplete runs do not receive invented scores.

Only former champions whose source is no longer current are downloadable. Raw diagnostics,
receipts, host paths, lease tokens, wallet/DB credentials and local logs are never public.
Error messages are operator-owned classifications; legacy failures lacking structured
telemetry are explicitly unknown. Public health cannot prove idle workers are online.
Weight vectors are plans/SDK results, not measured emissions. Source visibility policy
currently follows the previous competition and can be revised before release.

## Tests

`tests/test_bendtt_api.py` has pure protocol tests and disposable PostgreSQL tests. Set
`BENDTT_TEST_ADMIN_DSN` to an explicitly local PostgreSQL admin URL (plain `postgresql://`);
it creates/drops randomly named databases and test roles, leaving existing databases alone.
CI sets this from its existing isolated PostgreSQL service. Fixtures mirror BendTT migrations
0001/chain/0003 and must be refreshed when that schema changes.

For the optional native end-to-end test, also set `BENDTT_NATIVE_ROOT` to a built BendTT
checkout and select `-m integration`. It sends a real signed API upload, runs the actual
Lean admission and two local benchmark workers, and reads the final report through HTTP.
It may correctly abstain on a busy host; it never disables load-quality checks. It does not
establish production hardware performance or live chain/reward behavior.
