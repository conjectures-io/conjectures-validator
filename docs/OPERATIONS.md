# Production operations

The code path is complete; launch readiness is an operator outcome. Do not accept miner payments
until the staging drill at the end has produced durable database rows and finalized chain
references for both a formalized task and a counterexample task.

## 1. Build and pin the verifier

Build on the production Linux/kernel profile, run the live sandbox probe, and publish by digest:

```bash
docker build -t registry.example/conjectures-verifier:<commit> .
docker run --rm --network none --read-only \
  registry.example/conjectures-verifier:<commit> doctor
docker inspect --format '{{index .RepoDigests 0}}' \
  registry.example/conjectures-verifier:<commit>
```

The verification worker refuses a tag-only image. Record the exact
`registry/name@sha256:<digest>` value in deployment configuration.

## 2. Database and API

Apply Flyway migrations before starting any process, then run the schema-drift check against a
non-production PostgreSQL server:

```bash
docker compose -f docker-compose.db.yml up -d
python3 scripts/check_schema_drift.py \
  --dsn postgresql://conjectures:<password>@127.0.0.1:5432/postgres
```

Set at minimum `APP_MODE=PROD`, the API-role `DATABASE_URL`, `PAYMENT_RECIPIENT_SS58`,
`BOUNTY_AMOUNT_RAO`, and `SUBTENSOR_NETWORK`. Start the API without reviewer secrets, wallet files,
or a Docker socket:

```bash
uvicorn submission_api.asgi:app --host 127.0.0.1 --port 8080
```

Use a different least-privilege URL for each process: `conjectures_api` for the public API,
`conjectures_verifier` for `fc-verification-worker`, `conjectures_reviewer` for the internal review
app, and `conjectures_reward` for the wallet-bearing payout worker. The migration owner must never
be a runtime credential.

Terminate TLS and apply request/body/rate limits at the edge. Do not publish the operator review
route without an additional private-network or identity-aware access boundary; its bearer token is
the application-level check, not the entire perimeter.

## 3. Verification worker

Run this on a host that can start one-shot Docker containers. The worker needs database access and
the public task pool; the verifier containers receive neither the database nor the Docker socket:

```bash
fc-verification-worker \
  --database-url "$DATABASE_URL" \
  --allowlist task_pool/allowlist.json \
  --task-pool tasks/pool \
  --image 'registry.example/conjectures-verifier@sha256:<digest>' \
  --worker-id verifier-prod-1
```

Alert on old `UNVERIFIED` rows, expired leases, repeated infrastructure retries, container runtime
failures, and any accepted report whose sandbox fields differ from the production profile.

## 4. Manual review

When `MANUAL_REWARD_REVIEW_ENABLED=true`, approve or reject only after verification reaches
`VERIFIED`:

```bash
DATABASE_URL="$REVIEW_DATABASE_URL" \
  REVIEW_API_TOKEN="$REVIEW_API_TOKEN" \
  uvicorn --factory submission_api.review_asgi:create_review_app \
  --host 127.0.0.1 --port 8081

curl -X POST "http://127.0.0.1:8081/v1/reviews/<submission-id>" \
  -H "Authorization: Bearer $REVIEW_API_TOKEN" \
  -H 'Content-Type: application/json' \
  --data '{"decision":"APPROVED","reason_code":"REVIEW_APPROVED"}'
```

The operator identity, policy version, reason, notes, timestamp, and resulting winner claim are
append-only. Two approvals for opposite modes may race, but PostgreSQL can commit only one winner
for the shared problem.

## 5. Reward worker

Use a dedicated, funded coldkey and a database credential separate from the API and verifier.
The worker pays the bounty frozen on each submission, applies an SDK spend cap on every operation,
and takes a database advisory lock so a second reward-signer process cannot race the wallet nonce:

```bash
fc-reward-worker \
  --database-url "$DATABASE_URL" \
  --network finney \
  --wallet-name validator-rewards \
  --wallet-path /run/secrets/bittensor-wallets \
  --max-payout-rao <maximum-frozen-bounty-rao> \
  --bounty-commit <reviewed-git-commit>
```

A payout is committed as `PENDING` before signing, then becomes `SUBMITTED` and `CONFIRMED`.
Never restart or manually retry a PENDING payout merely because it has no reference: an RPC failure
can occur after broadcast. Reconcile it against the sending wallet's chain history first. The
unique `reward_events.submission_id` constraint deliberately makes a second automatic attempt
impossible.

After locating the exact finalized transfer, advance the existing row without loading signing
keys:

```bash
fc-reward-reconcile \
  --database-url "$REWARD_DATABASE_URL" \
  --network finney \
  --reward-event-id <id> \
  --extrinsic-reference <block-index> \
  --operator <operator-id>
```

The expected reward-wallet sender was frozen on the payout reservation. The command refuses a
failed, nonfinal, wrong-sender, wrong-destination, or wrong-amount transfer and appends the normal
reward transition event on success.

## 6. Required staging drill

For one formalized task and its paired counterexample task, record evidence for:

1. a signed request and canonical finalized `block-index` payment;
2. durable proof bytes and submission metadata derived from the allowlist;
3. a leased, digest-pinned, networkless container run;
4. an append-only report whose task mode/problem/digests match the submission;
5. optional review with an operator audit row;
6. exactly one `problem_winners` row when both outcomes race;
7. exactly one finalized payout and its miner-visible extrinsic reference; and
8. backup/restore recovery preserving proofs, reports, reviews, winners, payout state, and events.

Also simulate a verifier crash after leasing and a reward RPC failure after reservation. The first
must be reclaimed after lease expiry; the second must remain PENDING and must not sign again.
