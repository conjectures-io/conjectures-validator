# Public read API

The unauthenticated surface the conjectures.io website is built on: what is on offer, what an
attempt costs, what has been verified, and whether submissions are open. Served by the same
process as the miner-facing intake ([API.md](API.md)), on the same port, with a different
middleware stack and a different set of response models.

Everything here is a `GET`, needs no credential, and is safe to cache.

| Method | Path | Contract | Purpose |
| --- | --- | --- | --- |
| `GET` | `/v1/catalog/conjectures` | `ConjectureListResponse` | List with filters and facet counts; one entry per conjecture |
| `GET` | `/v1/catalog/conjectures/{slug}` | `ConjectureDetail` | Statement, references, bounty, pins, and one `Challenge.lean` plus machine contract per attack direction |
| `GET` | `/v1/catalog/conjectures/{slug}/activity` | `ConjectureActivity` | Anonymised activity stream |
| `GET` | `/v1/catalog/meta` | `PoolMeta` | Counts, credit price, treasury, bounty model, pins |
| `GET` | `/v1/results/certified` | `CursorPage<PublicResult>` | Approved and paid out |
| `GET` | `/v1/results/in-review` | `CursorPage<InReviewResult>` | Lean-verified, awaiting manual review |
| `GET` | `/v1/results/submissions` | `CursorPage<PublicResult>` | Both of the above in one feed, for a dashboard |
| `GET` | `/v1/results/{id}` | `PublicResult` | One published result |
| `GET` | `/v1/results/{id}/report` | `PublicVerificationReport` | The published subset of the verifier report |
| `GET` | `/v1/results/{id}/solution` | `PublicSolution` | The proof itself — only once review has approved it |
| `GET` | `/v1/system/status` | `SystemStatus` | Submissions open/paused, queue depths, pin rotation window |

Response models are in [`../submission_api/schemas_public.py`](../submission_api/schemas_public.py),
kept separate from the miner-facing `schemas.py` so that the rules below are enforced by which
module a field lives in rather than by remembering to leave it out.

## What is published, and what is not

Three rules, each enforced structurally rather than by convention.

**Solver credit, but no money trail.** Every result names the `hotkey` that submitted it: a result
is credited to its solver. Not published: the paying coldkey, the payment reference, the funding
extrinsic. [`../conjectures_subnet/db/public.py`](../conjectures_subnet/db/public.py) is the only
query layer these endpoints use, and its row types have no column for any of those three — a router
cannot leak what it was never handed.

> Publishing the hotkey weakens the activity pseudonyms below, and the two are no longer
> independent. A verified result names its solver and carries `verified_at`; an activity event
> carries the same transition, on the same conjecture, at hour resolution. Matching them names the
> solver behind a pseudonym — and then that solver's failed attempts on that conjecture too. The
> pseudonyms still protect solvers with no verified result, and nothing more than that.

**Proof bytes only after approval.** `Main.lean` is served by `GET /v1/results/{id}/solution`, and
only once review has approved the submission. An in-review result carries no artifact: the proof
has passed the Lean kernel but not the reward decision, and publishing it before that decision
would hand a pending result to anyone who wanted to resubmit it elsewhere. Approval *is* that
decision, so the gate is approval rather than payout — a confirmed transfer is not a disclosure
question. The check lives in `accepted_solution` in
[`../conjectures_subnet/db/public.py`](../conjectures_subnet/db/public.py), i.e. in the query, so a
handler cannot serve an unapproved proof by forgetting it. Both "not approved yet" and "not
published at all" answer `404`, so the endpoint cannot be used to detect a pending submission.

`PublicResult.solution_available` says which rows have one, so a client does not discover it by
collecting `404`s.

**No verifier output.** The public report is built by *allowlisting* fields
(`PUBLIC_REPORT_FIELDS` in [`../submission_api/routers/results.py`](../submission_api/routers/results.py)),
not by removing the sensitive ones. What that buys is the default: a field added to
`VerificationReport` later is withheld until someone deliberately adds it to the allowlist.

| Withheld | Why |
| --- | --- |
| `stdout_tail`, `stderr_tail` | Lean's output quotes the submitted proof back verbatim |
| `submission_sha256` | `submissions.proof_digest` is globally `UNIQUE`, so publishing it would let anyone test a candidate proof for prior submission and get a definitive answer |
| `workspace_retained`, `comparator_exit_code` | Operator debugging detail; says nothing about the result |
| `problem_id` | The per-revision identity of the conjecture. It moves on every pin rotation, so publishing it in a report would invite a client to key on it; the response's own `slug` is the stable identity, and `ConjectureDetail` publishes `problem_id` alongside it for anyone who needs the revision-specific name |

`report_sha256` is the digest of the **full** report, not of the reduced projection, so it still
matches the immutable bytes on the run and the copy the submitting miner can read.

A submission that is on no public feed is `404`, never `403`. Distinguishing "not published" from
"does not exist" would turn `/v1/results/{id}` into a probe for the state of work that has not
been published yet.

## Conjectures

### The slug is stable, and it is not the task id

A slug appears in a website URL, so it gets bookmarked, cited and indexed. It has to outlive what
produced it. `task_id` cannot do that job — it is seeded with the pinned source revision, twice:

```
fc-{repository_commit[:8]}-{task_slug(theorem)}-{digest}-{mode}-v{adapter_version}
```

where `digest` hashes the commit as well. Under the weekly drain-and-rotate pin policy, **every
task id in the pool changes on every rotation**, even for a conjecture whose statement, docstring
and Lean bytes are byte-identical. A URL built from one would break weekly.

So the slug is derived from `reward_target_id` (`fc-target:<theorem>`), which names the bounty
rather than the build — see [`../submission_api/slugs.py`](../submission_api/slugs.py). That
identity is not new: `verifier.task_pool` already mints it so a re-pin cannot pay the same theorem
twice. This extends it to the URL space, where the durability requirement is the same.

`Erdos11.erdos_11` → `erdos11-erdos-11`. The *whole* dotted path is used, unlike
`task_generator.task_slug` which keeps two segments — safe inside a task id because a digest
disambiguates there, unsafe here.

Two consequences, both deliberate:

* **A refined statement keeps its URL.** If upstream tightens a theorem's phrasing, the page
  updates and the link still works. A reader following a years-old link about Erdős 11 wants the
  current Erdős 11, not a 404 next to a new page.
* **Stability rests on the upstream theorem name.** A rename upstream moves the slug. Far rarer
  than a weekly pin, but derivation gives no recourse; the durable fix is an explicit slug alias
  table, which is not built yet.

Slug uniqueness is checked, not assumed. Slugification is lossy — `A.b_c` and `A.b.c` both reduce
to `a-b-c` — so the grouping refuses to start on a collision. A startup failure during a rotation
is bad; serving one conjecture at another's cited URL is worse.

**Task-id URLs are redirected, not 404ed.** A `GET` on a task-id-shaped slug answers `301` to the
stable slug, preserving the `/activity` suffix. Two callers need this: a link minted before slugs
existed, and a solver who pasted an id out of a bundle or a report. It works for task ids from
*past* rotations too, because the theorem fragment inside a task id does not depend on the commit.
An ambiguous fragment answers `404` rather than guessing — a wrong redirect is worse than a dead
link.

### One entry per conjecture, not per task

Every theorem is issued as one task per production mode: `formalized` to prove it,
`counterexample` to refute it. Listing tasks would show each conjecture twice, with two unrelated
ids and near-identical statements, and would make `attempts` mean attempts in one direction.

So both fold into one conjecture, and the per-task facts live under `tasks`:

| Level | Fields |
| --- | --- |
| Conjecture | `slug`, `title`, `statement`, `summary`, `category`, `classification`, `tier`, `ams_subjects`, `is_open`, `problem_id`, `reward_target_id`, `task_modes`, `attempts` |
| Task (one per direction) | `task_id`, `task_mode`, `task_bundle_sha256`, `attempts`, and on the detail endpoint `challenge_lean` and `machine_contract` |

`meta.conjectures` counts conjectures, so it is half what a task count would report.

`attempts` at the conjecture level counts submissions in either direction, and it is keyed on
`reward_target_id` — so it does not reset to zero at each rotation, which a task-keyed counter
would. The per-task `attempts` keeps the breakdown a solver choosing a direction actually wants.

### The rest of a conjecture

`ConjectureDetail` carries both halves. The human half is `summary` (the source docstring) and
`statement` (Lean's pretty-printed type). `title` is the fully-qualified source theorem: an
identifier a mathematician can cite, not prose — no human title exists in the upstream catalog, and
inventing one here would put a name on the website that appears in no audited artifact.

Each task's `challenge_lean` is the exact `Challenge.lean` whose bytes are hashed into that task's
published `task_bundle_sha256`. It is held in memory from startup, not re-read per request, so the
published statement cannot drift from the audited one between boot and a request — and a reader can
verify it against the commitment without trusting the response.

`machine_contract` is the solver-facing contract: the identifiers the proof must define, the axioms
it may depend on, the imports it may not use, the stable `reward_target_id`, and the limits it is
checked against. The reward identifier belongs to one exact theorem target and is shared only by
its proof/refutation pair and later source repins. Parents, parts, and variants have independent
identifiers and independent rewards. A solver that satisfies the contract offline is checked
against the same values on submission.

Each conjecture carries a live `bounty` object. `amount_rao` is null when `available` is false;
`amount_usd` is a TaoStats-backed decimal string rounded to cents, or null when the bounty or the
external rate is unavailable. It is `(amount_rao / 1e9) * alpha_price_tao * tao_price_usd`;
the Alpha amount remains authoritative when this display-only conversion is absent. `reason`
distinguishes an open target from one already solved, and `locked` is always false. The
pool-wide `/v1/catalog/meta` response publishes `bounty.balance_rao` and its display-only
`bounty.balance_usd` conversion, plus the open-target count, total age weight, and rational policy
constant behind the task estimates. An accepted submission does not reserve the amount displayed
on the website.

### Filters and facets

`category`, `classification`, `task_mode`, `tier` and `ams_subject` are repeatable and ANDed
across fields, ORed within one. A conjecture matches `task_mode` if any of its tasks does. `is_open`
is its own boolean, and `q` is a case-folded substring test over the slug, **every task id**, the
theorem, module, statement and docstring — so pasting an identifier from a report or an old URL into
the search box finds its conjecture. A substring test and not a pattern, so a catastrophically
backtracking regular expression is data rather than free CPU.

Facet counts follow the usual faceted-search rule: each facet is counted over the results matching
every filter **except its own**. Without that, selecting `category=research open` would collapse
the category facet to one row and a reader could filter in but never out.

Paging is `limit` (capped at 100) and `offset` (capped at 10000). Offset paging is safe here and
only here: the catalog is a fixed list of a few hundred entries held in memory, so there is no scan
to amortise and no insert to shift the window.

## Result feeds

Cursor-paginated, newest first:

```
GET /v1/results/certified?limit=25
→ { "items": [...], "next_cursor": "MS4xNzU0MjI…" }
GET /v1/results/certified?limit=25&cursor=MS4xNzU0MjI…
→ { "items": [...], "next_cursor": null }
```

`next_cursor` is null exactly when the feed is exhausted — the handler reads `limit + 1` rows and
discards the extra — so a client loops until null rather than making a wasted request to discover
the end. There is deliberately no total: `COUNT(*)` over a growing table on every page read is a
scan an anonymous caller should not be able to ask for.

**Keyset, not `OFFSET`.** The predicate is a row-value comparison `(created_at, id) < (cursor)`
over a partial index ([`../deploy/migrate/sql/V002__public_feeds.sql`](../deploy/migrate/sql/V002__public_feeds.sql)),
so it reads one index range whatever page you are on. `OFFSET 50000` reads and discards fifty
thousand rows. It is also correct under concurrent inserts, which an offset is not: a result
certified between two page reads shifts every subsequent offset by one and silently hides a row.
The pair rather than the timestamp alone because `created_at` is not unique — two submissions
committed in one transaction share it.

**Cursors are opaque and signed.** The contents are not secret; the HMAC is there so the handler
never parses an attacker-chosen value into a query predicate, and so a tampered cursor is one clean
`400 INVALID_CURSOR` rather than a database error. Every failure mode returns the same reason code:
a client learns nothing about whether it was the shape or the signature that was wrong.

`certified` means paid out — `reward_status = 'REWARDED'` with the review approved. `in-review`
means Lean-verified and awaiting the reward decision.

Every `PublicResult` carries the payout's `bounty_amount_rao` and a current
`bounty_amount_usd` display conversion using the same formula as catalog bounties. The USD value
is null when TaoStats is unavailable and is not a historical fiat valuation of the payout.

A result carries both identities: `slug` names the conjecture, `task_id` names the task it was
produced against. The slug is derived from the row's own `reward_target_id` rather than looked up
in the catalog, so a result produced under an earlier pin still links to the live conjecture page
instead of to a task id the current pool no longer carries.

## Activity

`GET /v1/catalog/conjectures/{slug}/activity` answers "is anyone working on this" without
answering "who" — but only for a solver who has no verified result on that conjecture. Since the
results feed names the submitting hotkey, the properties below hold against a reader who works only
from this endpoint, not against one who joins it to `/v1/results` on `verified_at`. Read this
section as the construction's design, not as a guarantee the surface as a whole still makes.

`solver` is
`HMAC(PUBLIC_ACTIVITY_SALT, len(reward_target_id) || reward_target_id || len(hotkey) || hotkey)`,
truncated to 12 hex characters. Two properties follow from where the conjecture's identity sits:

* **Stable within a conjecture.** Repeat attempts read as the same solver, so `solvers` is a
  meaningful count of distinct participants.
* **Unlinkable across conjectures.** The conjecture's identity is inside the MAC, so the same miner
  gets a different pseudonym on every conjecture and the pseudonyms cannot be joined across the
  catalog to rebuild one miner's history. Length-prefixed, so `(conjecture, key)` pairs cannot be
  chosen to collide by shifting the boundary between them.

Keyed on `reward_target_id` rather than on a task id, matching the stream itself. A task-keyed MAC
would give one miner two pseudonyms on a page that shows both attack directions — making one person
look like two, and inflating `solvers` — and would rename every solver at each pin rotation.

`occurred_at` is truncated to the hour. An attempt is funded by a transfer that is visible on chain
with its sender at a known block time; a per-second timestamp would let anyone join the two and
undo the pseudonym. An hour bucket makes that join ambiguous whenever more than one transfer landed
in the hour, and costs a reader nothing.

`PUBLIC_ACTIVITY_SALT` is therefore load-bearing, not decorative. A hotkey is a 48-character
address from a known alphabet, so an unsalted digest — or one salted with the constant published in
`settings.py` — is reversible by enumeration for anyone holding a list of subnet hotkeys.
Production refuses to start with either.

`event` is the furthest state the submission has reached (`attempt`, `rejected`, `verified`,
`certified`) rather than a per-transition stream, because the schema keeps current state and not
transitions. Saying `attempt` for a submission that has since been certified would be wrong.

## System status

`submissions_open` is authoritative, not descriptive. `SUBMISSIONS_PAUSED` is read by
`POST /v1/submissions` too, which refuses with `503 SUBMISSIONS_PAUSED` while it is set. A status
endpoint that reported a pause the intake path did not honour would be worse than no status
endpoint, because a solver would trust it and spend a payment.

`pin_rotation` is the weekly drain-and-rotate window from
[../README.md](../README.md#pins-cache-and-reproducibility): operators pause submissions, wait for
every accepted submission to reach a terminal state, then update the pin set atomically. The window
is configured — only the operator knows when they take the system down — but `drained` is computed
from the queues, because whether the window may actually start is a fact, not a setting.

`status` is `paused` when submissions are closed, `degraded` when a rotation window is in progress
while submissions are still open (the policy says those should not coincide, and an operator should
see the disagreement rather than have it smoothed over), and `ok` otherwise.

Never cached: this is the endpoint a client polls to learn whether what it was told is still true.

## Caching

| Endpoint | `Cache-Control` | `ETag` |
| --- | --- | --- |
| `/v1/catalog/meta` | `public, max-age=PUBLIC_CACHE_SECONDS` (60 by default) | yes |
| Other catalog reads | `public, max-age=PUBLIC_CACHE_SECONDS` | no |
| Results | `public, max-age=PUBLIC_CACHE_SECONDS / 2` | no |
| `/v1/system/status` | `no-store` | no |

`public` is deliberate: none of these endpoints varies by caller and none carries a credential, so
a shared cache in front of the API may serve one copy to everyone. It is also the cheapest
rate-limit relief available. Anything that ever becomes caller-dependent has to lose the header in
the same change.

`/v1/catalog/meta` carries a strong `ETag` and honours `If-None-Match` with a `304`. It is the one
public endpoint whose payload comes entirely from startup state — the catalog, the pin set and the
settings, none of which move while the process runs — and the one a website hits on every page
load. The tag is hashed from the serialised payload rather than assembled from its inputs, so it
cannot drift from the body.

The list, detail and result endpoints carry no `ETag` on purpose: their payloads include live
attempt counters, so any honest validator would have to be recomputed from the database on every
request and would change constantly. A validator that has to be recomputed to be checked saves
nothing.

## Browser access

CORS applies to `/v1` and nowhere else, from an exact allowlist in `CORS_ALLOWED_ORIGINS`. No
wildcard and no pattern matching: a subdomain wildcard turns one XSS on any subdomain into read
access to this API, and there is no origin here that is not known at deploy time. An empty
allowlist is valid and fail-closed — no browser may read the API, which is correct until a site
exists. Production refuses `*` and refuses plaintext origins.

`allow_credentials` is **false**. Stage 2 introduces a session cookie; the moment credentials are
allowed, an allowlisted origin can read authenticated responses and the allowlist becomes the only
thing between an XSS on any listed site and a reader's account. Until there is a session to send,
sending none is free.

Methods are `GET`, `HEAD` and `OPTIONS`. `POST /v1/submissions` is deliberately absent: miners call
it from their own tooling and no browser ever calls it, so leaving it out means no page on any
origin can spend a miner's payment even if that origin is compromised. A preflight for it fails
with `400`.

`ETag`, `RateLimit-*` and `Retry-After` are in `expose_headers`, so a page can actually read the
budget it is subject to.

### Response hardening

Every response from this process carries `X-Content-Type-Options: nosniff`,
`Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, `Cross-Origin-Opener-Policy: same-origin`,
`Cross-Origin-Resource-Policy: cross-origin`, a `Permissions-Policy`, and a
`Content-Security-Policy` of `default-src 'none'` — right for a JSON API, since there is nothing to
load and a response that somehow rendered as HTML could not fetch or execute anything. The CSP is
not applied to `/docs`, which is development-only and needs its own script and stylesheet.

`Strict-Transport-Security` is production-only. An HSTS header from a localhost development server
pins the developer's browser to HTTPS for every other service on localhost too.

`Alt-Svc` advertises an HTTP/3 authority for clients to retry over QUIC. HTTP/3 is a transport
concern an ASGI application never sees, so this is an advertisement of what the deployment's edge
terminates, not a claim about this process — set `ALT_SVC` to match the real edge, or leave it
unset if it does not terminate QUIC.

The stack order is load-bearing:

```
SecurityHeaders  →  ScopedCORS  →  RateLimit  →  routes
```

Two layers generate responses of their own, and a response only passes through the layers *outside*
the one that made it. Security headers outermost so the `429` and the preflight are hardened too;
CORS outside the limiter so a `429` still carries `Access-Control-Allow-Origin` — a browser that
cannot read the body reports an opaque CORS failure instead of the rate limit.

## Rate limiting

A sliding window per client over `/v1`, reported on every response:

```
RateLimit-Limit: 120
RateLimit-Remaining: 118
RateLimit-Reset: 47
```

Emitted on admitted responses too, not only on `429`, so a client can back off before being
refused. A refusal is RFC 9457 `application/problem+json` with `reason_code: RATE_LIMITED` and a
`Retry-After`. Refused requests are not themselves counted — counting them would extend the penalty
every time a client retried, turning a burst into an unbounded lockout.

Sliding rather than fixed, because a fixed window lets a client spend the whole budget in the last
second of one window and the whole budget again in the first second of the next, so the real
short-term rate is twice the configured one.

**It is in-process, and that is a real limitation.** There is no shared store, so N replicas admit
N times `RATE_LIMIT_REQUESTS`. That is a deliberate trade against adding Redis to
[`../requirements-service.lock`](../requirements-service.lock) and to the deployment: this is a read
surface where the purpose is to stop one client from monopolising a process, not to meter a quota
exactly. Set `RATE_LIMIT_REQUESTS` to the per-replica share, and put a shared limiter at the edge if
an exact global rate is ever required.

The key table is bounded by `RATE_LIMIT_MAX_CLIENTS`. Client keys are attacker-chosen — one per
source address — so an unbounded dictionary would be a memory-exhaustion primitive. Eviction only
ever reclaims entries whose windows have already expired, because dropping an active client would
hand it back a full budget.

`/healthz` and `/readyz` are exempt: a `429` to an orchestrator probe takes the replica out of
service.

### Client identity

`TRUSTED_PROXY_HOPS` is how many rightmost `X-Forwarded-For` entries this deployment's own proxies
added. Each proxy appends the peer it received from, so with `n` trusted hops the originating client
is the `n`-th entry from the right; anything further left was written by the client and is ignored.
A chain shorter than the configured count means the request did not arrive through the expected
proxies, and nothing in it is trusted.

The default of `0` ignores the header entirely and uses the peer address. It is the only setting
that cannot be spoofed, and it is correct for a directly exposed process. Getting this wrong breaks
the limiter in both directions: trusting an untrusted header lets one client mint unlimited keys,
and ignoring a real one collapses every visitor behind a CDN onto a single budget.

## Configuration

See [`../.env.example`](../.env.example) for the annotated set.

| Variable | Default | Notes |
| --- | --- | --- |
| `CORS_ALLOWED_ORIGINS` | — | Exact origins, comma-separated. `*` and plaintext refused in `PROD` |
| `PUBLIC_CURSOR_SECRET` | development constant | **Required in `PROD`**, 32+ chars, refused if it is the published constant |
| `PUBLIC_ACTIVITY_SALT` | development constant | **Required in `PROD`**, same rules |
| `RATE_LIMIT_ENABLED` | `true` | Cannot be `false` in `PROD` |
| `RATE_LIMIT_REQUESTS` | `120` | Per client, per window, **per replica** |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | |
| `RATE_LIMIT_MAX_CLIENTS` | `50000` | Bounds the limiter's own memory |
| `TRUSTED_PROXY_HOPS` | `0` | `0` ignores `X-Forwarded-For` entirely |
| `PUBLIC_CACHE_SECONDS` | `60` | `0` sends `no-store` |
| `ALT_SVC` | `h3=":443"; ma=86400` in `PROD` | Describes the edge, not this process |
| `HSTS_MAX_AGE` | `31536000` | Sent in `PROD` only; `0` disables |
| `PINS_LOCK_PATH` | `./pins.lock.json` | Read once at startup |
| `SUBMISSIONS_PAUSED` | `false` | Also refuses intake |
| `STATUS_BANNER` | — | Max 500 characters |
| `PIN_ROTATION_WEEKDAY` | `1` | Monday is 0, Sunday is 6 |
| `PIN_ROTATION_START_UTC` | `02:00` | UTC `HH:MM` |
| `PIN_ROTATION_DURATION_MINUTES` | `240` | |

The pin lock and the audited allowlist are cross-checked at startup: a lock naming a different
source revision than the allowlist would publish statements from one revision under the pins of
another, so it stops the process rather than showing up as a mismatched detail page.

## Tests

```bash
docker compose -f docker-compose.pytest-db.yml up -d
.venv/bin/pytest tests/test_api_catalog.py tests/test_api_results.py tests/test_api_public.py
```

[`../tests/test_api_results.py`](../tests/test_api_results.py) is largely about absence — that no
hotkey, coldkey, payment reference or verifier output appears in any public payload, and that a
field added to the verifier report is withheld by default. The proof and its digest are the one
deliberate exception, and only for an approved submission: the tests assert that an unverified,
rejected, or still-in-review submission publishes neither.
