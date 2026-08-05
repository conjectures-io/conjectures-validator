# Observability

Every process in this validator — the submission API, the verification worker, the deposit watcher
and the emissions worker — emits structured events to [Axiom](https://axiom.co). One dataset, one
event shape, three labels that make it queryable:

| Field | What it answers | Values |
| --- | --- | --- |
| `severity` | How bad is it? | `debug`, `info`, `warning`, `error`, `critical` |
| `source` | Which area of the system spoke? | `api-submissions`, `deposit-watcher`, … (below) |
| `event_type` | What happened? | `verdict_recorded`, `transfer_credited`, … (below) |

plus `environ` (the deployment tag), `_time` (stamped by the producer, not at ingest), a
`request_id` when one is in scope, and whatever fields the call site added.

The vocabulary is closed. `conjectures_subnet/axiom/labels.py` declares every `source` and
`event_type` as a `Literal`, and `tests/test_axiom.py::test_every_emitted_label_is_declared` parses
the tree and fails the build on one that is not declared. A typo cannot become a field nobody can
query.

**None of it is required.** With `AXIOM_TOKEN` and `AXIOM_DATASET` unset the sink is a no-op,
ingestion never happens, and stderr logging is exactly what it was. This is the one part of the
configuration that is deliberately not fail-closed — see "Why this is not fail-closed" below.

## Configuration

All four processes read the same variables, so one setting configures the deployment and they
cannot end up writing to different datasets. `.env.example` carries the annotated copy.

| Variable | Default | Meaning |
| --- | --- | --- |
| `AXIOM_TOKEN` | — | An Axiom API token, `xaat-…`. A personal token (`xapt-…`) is not an ingest credential. |
| `AXIOM_DATASET` | — | The dataset events land in. |
| `AXIOM_ENVIRON` | `default` | A free-form deployment tag on every event: `prod`, `staging`, a hostname. |
| `AXIOM_URL` | `https://api.axiom.co` | The API base. `https://api.eu.axiom.co` for the EU region. |
| `AXIOM_LOG_LEVEL` | `INFO` | Severity floor for the stdlib-logging bridge. |
| `AXIOM_REQUEST_EVENTS` | `all` | `all`, `failures` or `off` — how much of the HTTP surface produces a per-request event. |

Both credentials are required together. A token with no dataset and a dataset with no token are
each a half-finished configuration, and guessing a dataset name would send a deployment's events
somewhere nobody chose.

`AXIOM_LOG_LEVEL` can only narrow what reaches Axiom, never widen it: a handler never sees a record
its logger already filtered, so `DEBUG` under `--log-level INFO` still forwards `INFO` and above.

## Two ways events are produced

**Explicit events**, at the moments worth naming:

```python
from conjectures_subnet.axiom import get_axiom

get_axiom().info(
    source="verification-worker",
    event_type="verdict_recorded",
    submission_id=str(submission_id),
    accepted=run.accepted,
    reason_code=run.reason_code,
)
```

**The stdlib bridge**, which forwards the `logger.*` calls already scattered through the API and the
workers — severity from the level, source from the logger name via a longest-prefix map. Entry
points get it by calling `configure_logging` in place of `logging.basicConfig`:

```python
configure_logging(source="deposit-watcher", level=args.log_level)
```

Both are wanted. The explicit events are the ones a dashboard is built on; the bridge is what means
a `logger.exception("verification pass failed; continuing")` — the only record of a worker
swallowing a crash — is not invisible because nobody remembered to add an event for it. Bridged
records arrive as `event_type: log_record` carrying `message`, `logger`, `module`, `function`,
`line`, any `extra=` fields, and the traceback when there is one.

The API does not call `configure_logging`: uvicorn configures logging before lifespan runs, so
`app.py` calls `attach_axiom_handler(source="api")` instead. There is no `basicConfig` of ours to
make.

## Sources

One per API router plus one per background service. The API is split rather than reported as a
single `api` because the routers have genuinely different audiences and failure modes — the public
catalog is read by a browser, `/v1/submissions` is written by miner tooling with a hotkey signature,
`/v1/me` is a signed-in account surface — and one label for all three would make every dashboard
start with a path filter.

| Source | Area |
| --- | --- |
| `api` | The API process itself: startup, shutdown, unhandled errors |
| `api-auth` `api-me` | Sign-in, sessions, wallet linking; the signed-in account surface |
| `api-submissions` `api-intents` | Paid intake: the extrinsic path and the credit-intent path |
| `api-catalog` `api-results` `api-tasks` `api-system` | The unauthenticated public read surface |
| `api-health` | `/healthz`, `/readyz` |
| `api-middleware` | Rate limiting, CORS, CSRF, security headers |
| `api-mail` `api-payments` | Outbound side effects the API owns |
| `verification-worker` | Claiming, verifying, recording verdicts |
| `deposit-watcher` | Reading finalized blocks, attributing arrivals, crediting |
| `emissions-worker` | Setting the treasury weight each epoch |
| `subnet-chain` `database` `verifier` | Shared infrastructure |

## Event types

Grouped by the area that raises them. `labels.py` is the source of truth.

- **Lifecycle** — `service_started`, `service_stopped`, `service_misconfigured`
- **HTTP** — `request_completed`, `readiness_degraded`
- **Intake** — `submission_accepted`, `submission_rejected`, `payment_accepted`, `payment_rejected`
- **Intents** — `intent_opened`, `intent_bundle_stored`, `intent_committed`
- **Accounts** — `login_link_sent`, `login_completed`, `logout`, `wallet_linked`
- **Verification** — `submission_claimed`, `verdict_recorded`, `lease_lost`,
  `insecure_sandbox_accept`, `attempts_exhausted`, `unclassified_reason_code`,
  `verification_operator_failure`
- **Deposits** — `cursor_opened`, `blocks_scanned`, `transfer_credited`, `transfer_unattributed`,
  `transfer_ignored`, `transfer_conflict`
- **Emissions** — `epoch_observed`, `weights_set`, `weights_failed`
- **Catch-alls** — `log_record`, `unexpected_error`

### Severity is a judgement about who has to act

Not a restatement of the log level. The choices worth knowing:

- **`critical` is used exactly once**: `attempts_exhausted`. A submission stuck there is a miner who
  paid, got no verdict, and will not get one without a person looking. It is the one condition that
  should page.
- **`transfer_unattributed` is a `warning` where the log line is `info`.** Every one of them is
  someone who sent this validator money it could not attribute, waiting for credits that will not
  arrive until an operator works the queue.
- **`insecure_sandbox_accept` is a `warning` even though it is a configured allowance.** An accept
  that nothing isolated is a verdict whose provenance has to be findable later, and finding it
  should not depend on having grepped the right container's logs.
- **A retryable failure is a `warning`, not an `error`.** A chain read that fails on a syncing node,
  or a weight extrinsic rejected on a busy block, is ordinary and the loop does not give up. What is
  an error is a streak, which is a rate over these events rather than a severity on one of them.
  `weights_failed` at `error` means something different: the *epoch watch* failed, and an epoch's
  emissions cannot be set retroactively.
- **A `4xx` is a `warning`, a `5xx` is an `error`.** Most `4xx`s are the API working correctly — a
  malformed bundle gets a `422` and that is the job. What makes them worth a non-`info` severity is
  that a *rate* of them is a signal.

## The HTTP request stream

`AxiomRequestMiddleware` emits one `request_completed` per answered request:

```
severity      info | warning | error, by status
source        the area of the API, from the path
endpoint      the route template — /v1/submissions/{submission_id}
method        GET, POST, …
status        the final HTTP status
duration_ms   wall clock
client        the address the request is billed to, honouring TRUSTED_PROXY_HOPS
user_agent    truncated
reason_code   on a refusal, the same code the response body carries
request_id    the correlation id
```

It is the **outermost** middleware layer, which is load-bearing: the rate limiter's `429` and the
CSRF layer's `403` are generated by middleware and never reach a route, so a layer any further in
would not see them.

`endpoint` is the route *template*, never the requested path. Two reasons. It groups — a per-path
breakdown of a UUID-keyed route has one row per request. And it cannot carry a secret: the template
is the shape of the endpoint with every parameter value left out. Query strings never appear at all;
`?token=` on a sign-in callback is a credential. A request that matched no route has no template, so
the raw path is reported instead, bounded, because at that point it is an arbitrary string somebody
sent us.

Liveness, readiness and the interactive docs produce no `request_completed`, in any mode. At one
probe per interval they would drown out everything that matters. The one thing that *is* reported is
a failing `/readyz`, as `readiness_degraded` at `error` — a replica whose database has gone away
removes itself from service, and the handler swallows the traceback, so without that event there
would be no trace of it anywhere.

### Correlating a request with what it logged

Every request is tagged with a 64-bit `request_id`, returned in the `X-Request-Id` response header
and carried on that request's own event *and* on every log record produced underneath it. So "what
did this call actually do" is one query rather than a timestamp correlation:

```
['conjectures-validator'] | where request_id == "a1b2c3d4e5f60718"
```

The id is minted per request by this process. One supplied by a client is ignored — a client-chosen
key would let one caller collide its requests with another's.

A `500` produces two events on purpose, joined by that id: `request_completed` puts the `5xx` on the
request stream where a rate of them is visible, and `unexpected_error` carries the traceback, which
is the only copy a query can reach. The response body still says nothing but `internal error`.

Background workers use the same mechanism through `work_context()`. A verification pass runs inside
one, so `submission_claimed`, `verdict_recorded` and everything the pass logged share a key.

## Why this is not fail-closed

Every other setting in this repository decides whether the validator is *correct* — which chain it
reads, which addresses it trusts, whether a proof was checked under real isolation — and
`submission_api/settings.py` refuses to start on a bad value for any of them. Observability decides
whether anyone is *watching*. A malformed `AXIOM_URL` should cost the operator their dashboard, not
the subnet its verification worker.

So every path through `env_factory.py` returns a working client; what varies is whether it ships
anything. An unparseable `AXIOM_LOG_LEVEL` falls back to `INFO` with a warning. An
`AXIOM_REQUEST_EVENTS` typo reports everything rather than silently reporting nothing.

## Why it cannot slow anything down

`ingest()` puts the event on a bounded queue and returns; a daemon thread batches and POSTs gzipped
NDJSON. This is not an optimisation — the API is async, and a synchronous HTTPS round trip inside a
request handler would park the whole event loop on Axiom's latency. A worker would pay the same cost
per log line.

The consequences are chosen rather than accidental:

- **A full queue drops and counts.** Ten thousand events is seconds of a busy API; past that,
  dropping is correct and `AxiomClient.stats()` reports how much was lost. A dataset with a gap is
  only trustworthy if something says how big the gap was.
- **A failed POST drops the batch and does not retry.** Retrying would hold the batch while newer
  events pile onto the queue, turning one failed flush into a wave of drops — and the next flush is
  two seconds away regardless.
- **A dead backend logs once per failing streak, not once per flush**, so it cannot bury the
  process's real output.
- **Shutdown flushes, with a bound.** `close()` waits up to five seconds. A validator must not hang
  on `SIGTERM` because a telemetry backend is unreachable.

## No new dependencies

The transport is Axiom's REST ingest API written against the standard library — one gzipped NDJSON
POST — rather than `axiom-py`. `requirements-service.lock` is a curated, exactly-pinned set, and
this matters most for the verification worker: its image is the one that runs next to hostile Lean,
and it should not grow a JSON parser, an HTTP session library and a case-conversion library so that
a log line can be shipped.

`AxiomClientInterface` is the seam. Swapping in `axiom-py`, or an OTLP exporter, means writing one
more implementation of it and changing `env_factory.py`.
