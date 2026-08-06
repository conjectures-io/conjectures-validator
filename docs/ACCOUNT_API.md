# Account API

The signed-in surface: sign in, manage an account, buy and spend credits, and submit a proof by
spending one. Served by the same process as the miner-facing intake ([API.md](API.md)) and the
public read surface ([PUBLIC_API.md](PUBLIC_API.md)), with a third set of rules.

| Method | Path | Contract | Purpose |
| --- | --- | --- | --- |
| `GET` | `/v1/auth/session` | `{ account }` | The current account, or `401` |
| `POST` | `/v1/auth/email/request-link` | `202` | Mail a single-use sign-in link |
| `POST` | `/v1/auth/email/verify` | `{ account }` | Exchange the token for a session |
| `POST` | `/v1/auth/wallet/challenge` | `{ nonce, message, expires_at }` | A nonce and the exact message to sign |
| `POST` | `/v1/auth/wallet/verify` | `{ account }` | Verify the signature, open a session |
| `POST` | `/v1/auth/logout` | `204` | Revoke the session and clear the cookies |
| `GET` | `/v1/me` | `Account` | Profile, roles, linked keys, payout address |
| `PATCH` | `/v1/me` | `Account` | Edit `display_name` |
| `POST` | `/v1/me/hotkeys/challenge` | `{ nonce, message }` | A nonce for linking a hotkey |
| `POST` | `/v1/me/hotkeys` | `Account` | Link a hotkey by signature |
| `PUT` | `/v1/me/payout` | `Account` | Payout destination: coldkey plus hotkey |
| `GET` | `/v1/me/credits` | `CreditBalance` | Available credits, balance, holds, remainder |
| `GET` | `/v1/me/credits/ledger` | `CursorPage<CreditLedgerEntry>` | The append-only ledger |
| `POST` | `/v1/me/deposits` | `Deposit` | Declare a deposit, get the `btcli` command |
| `GET` | `/v1/me/deposits/{id}` | `Deposit` | Deposit state |
| `POST` | `/v1/me/deposits/claim` | `Deposit` | Claim a transfer the reconciler missed |
| `GET` | `/v1/me/submissions` | `CursorPage<SubmissionSummary>` | Own submissions |
| `GET` | `/v1/me/submissions/{id}` | `SubmissionDetail` | With review decision and payout state |
| `GET` | `/v1/me/submissions/{id}/events` | `SubmissionEvent[]` | The timeline |
| `GET` | `/v1/me/submissions/{id}/report` | `OwnerVerificationReport` | The full report, nothing withheld |
| `GET` | `/v1/me/rewards` | `CursorPage<RewardItem>` | Payouts with explorer links |
| `POST` | `/v1/submissions/preflight` | `PreflightResult` | Free static check, no credit, no auth |
| `POST` | `/v1/submissions/intents` | `SubmissionIntent` | Open an intent, hold one credit |
| `PUT` | `/v1/submissions/intents/{id}/bundle` | `IntentBundleResult` | Upload, receive the digest to sign |
| `POST` | `/v1/submissions/intents/{id}/confirm` | `{ submission, credits }` | Debit and submit, atomically |
| `GET` | `/v1/submissions/intents/{id}` | `SubmissionIntent` | Intent state |

Response models are in
[`../submission_api/schemas_account.py`](../submission_api/schemas_account.py) — the third of
three model modules, and the one where a hotkey, an email, a payout address or a balance is
*allowed* to appear, because everything here is served only to the authenticated owner of the
data. The separation is the point: a field on `Account` would be a serious disclosure on
`PublicResult`.

## Sessions

An opaque token in an HttpOnly cookie, backed by a row. Deliberately not a JWT: a JWT here would
be either short-lived — meaning a refresh mechanism, meaning a second credential — or long-lived
and unrevocable, meaning a logout that does not log anything out. One `UPDATE` revokes a row.

Two cookies, and the split is intentional:

| Cookie | Flags | Why |
| --- | --- | --- |
| `conjectures_session` | `HttpOnly`, `Secure`, `SameSite=Lax` | The credential. HttpOnly so page script cannot exfiltrate it — an XSS is then confined to acting within the page rather than stealing something durable. |
| `conjectures_csrf` | `Secure`, `SameSite=Lax`, **not** HttpOnly | Readable by script on purpose: the frontend copies it into `X-Conjectures-CSRF`. Only same-origin code can read it, which is exactly what a cross-site attacker cannot do. |

`SameSite=Lax`, not `Strict`: a magic link arrives from a mail client as a cross-site top-level
navigation, and `Strict` would withhold the cookie on precisely the request that has to carry it.
`Secure` is set in production only — a browser on plain-HTTP localhost refuses to send back a
`Secure` cookie, which would make development sign-in silently fail.

**Only digests are stored.** The session token and every login challenge secret are held as raw
SHA-256 bytes. A dump, a backup, a replica, or an over-broad `SELECT` yields nothing replayable.
Nothing in `conjectures_subnet.db.accounts` can return a usable token.

**30-day rolling.** `expires_at` is extended on use, but only once per `SESSION_REFRESH_MINUTES`
— extending on every request would mean a row lock and a WAL record for every page load, and the
website polls. `SessionCookieRefreshMiddleware` re-sends the cookie with a fresh `Max-Age` so the
browser's expiry does not drift behind the row's.

**A sign-in retires every earlier session for that account.** Whatever the account could reach
before, the only live credential afterwards is the one just issued.

## CSRF

Cookies mean the browser sends credentials on cross-origin requests, so every state-changing
request must pass **three independent checks**. All three, not any of three:

1. **`Origin` is on the allowlist.** Browsers send it on state-changing requests and a page
   cannot forge it. Enforced in `CsrfMiddleware`.
2. **`Sec-Fetch-Site` is `same-origin` or `none`.** Set by the browser, unforgeable by script.
   `same-site` is refused too: a subdomain is not this origin, and treating it as one is how a
   single compromised subdomain becomes account access. Not universal across older browsers,
   which is why it is not the only check.
3. **`X-Conjectures-CSRF` matches the session.** Compared against the digest stored on the
   session row, not against the cookie — a bare double-submit compares two client-supplied values
   to each other and fails to subdomain cookie injection. Enforced in the route dependency,
   because only the resolved route knows which session is authenticated.

Which dependency a handler names *is* its access control:

```python
OptionalPrincipalDep   # may be signed in — GET /v1/auth/session only
PrincipalDep           # must be signed in — every read
WriterDep              # signed in AND passed the CSRF check — every write
```

A state-changing handler that names `PrincipalDep` is a CSRF hole, which is why the names are
deliberately not interchangeable-looking.

`POST /v1/auth/email/request-link` is guarded too, even though it is unauthenticated: it sends
mail, so a cross-site page must not be able to trigger it either.

`POST /v1/submissions` and `/v1/submissions/preflight` are exempt by path. They carry no cookie,
so there is no ambient credential to abuse, and miner tooling sends neither header.

## Signing in

### Email

`POST /v1/auth/email/request-link` **always answers `202`** — for a known address, an unknown
one, and one past the rate limit. A different answer for a known address would make this an
account-enumeration oracle, and the address is the one input an attacker varies freely.

The rate limit is **per address**, not per caller: mailing a link is an action taken against
someone else's mailbox, and the IP limiter cannot see who is being mailed.

`POST /v1/auth/email/verify` consumes the token in one conditional `UPDATE`, so a forwarded email
or a double-clicked link signs in once. It is signup and sign-in at once — verifying a token
proves receipt at that address, which is the whole of what an email account proves.

### Wallet

Four things this validator asks a key to sign, each **domain-separated** so a signature harvested
from one is worthless in another:

```
conjectures-login-v1          sign in with a coldkey
conjectures-hotkey-link-v1    attach a hotkey to an account
conjectures-deposit-claim-v1  claim a transfer you made
conjectures-read-v1           read a submission's status
<the request digest>          authorise one submission (32 raw bytes, not text)
```

No prefix is a prefix of another, and each message pins the address it is for, the nonce, and its
own expiry. The message is stored **verbatim** on the challenge row and verification reads it back
rather than rebuilding it — rebuilding it differently is exactly the bug that makes a signature
meaningless.

The signature is verified **before** the nonce is consumed, so a wrong signature does not burn it.
Otherwise one bad request would force the user to start over, and an attacker could grief a known
address by spamming invalid signatures.

## Credits

**Integer rao only. The credit count is derived, never stored.** A credit is one verification
attempt at `credit_price_rao`, so `credits_available` is the balance divided by that price.
Storing a count as well would create two numbers that can disagree — which is the bug a ledger
exists to prevent.

```json
{
  "credits_available": 3,
  "balance_rao": 1750000000,
  "held_rao": 0,
  "remainder_rao": 250000000,
  "credit_price_rao": 500000000,
  "low_balance": false
}
```

`remainder_rao` is the leftover that is not a whole credit. Surfaced because otherwise a reader
with 3.5 credits' worth of rao sees "3 credits" and concludes the rest vanished.

**The ledger is append-only**, and the balance is its sum. Make that true in deployment too:
`REVOKE UPDATE, DELETE` on `credit_ledger` from the service role. Five kinds — `DEPOSIT`,
`SPEND`, `REFUND`, `ADJUSTMENT`, `BONUS` — with the sign fixed per kind by a CHECK, so a debit
cannot be recorded as a credit by passing the wrong one. `ADJUSTMENT` is the only either-way kind
and it must carry a reason.

**Holds are not ledger entries.** An open intent claims part of the balance, but a claim is not a
movement: it either becomes a `SPEND` or evaporates. Writing it to an append-only ledger would put
an entry there that later has to be undone. So `available = balance − live holds`, and the holds
live on `submission_intents`. A hold stops counting the moment it lapses, by timestamp — not when
a sweeper gets round to it.

A negative balance is possible (an operator correcting an over-credit) and clamps to zero
available credits rather than to `-1`, which is what Python's floor division would give.

### Deposits

`POST /v1/me/deposits` records what will be sent and returns a ready-to-copy command:

```json
{
  "status": "AWAITING_TRANSFER",
  "amount_rao": 2000000000,
  "credits_expected": 4,
  "credited_rao": null,
  "btcli_command": "btcli wallet transfer --dest 5C4h… --amount 2"
}
```

Nothing is credited by declaring one. Recording the expected amount and recipient is what lets
confirmation *check* a transfer instead of crediting whatever arrived.

**What is credited is the amount observed on chain**, not the amount declared. Crediting the
declaration would let someone promise 10 TAO, send 1, and be credited for 10.

`SEEN_UNFINALIZED` exists so an account is told "we can see it, we are waiting for finality"
rather than shown nothing while a transfer settles. Only `AWAITING_TRANSFER` ever expires: a
deposit with real money behind it must be resolved by finality or by a human, never timed out.

`POST /v1/me/deposits/claim` proves the claimant controls the sending coldkey, and **issues no
credits**. It records the reference as `SEEN_UNFINALIZED` and stops. Crediting on a caller's
assertion is the one outcome that must never be possible: finality, recipient and amount are chain
reads, and the only thing that performs them is the reconciler.

### The reconciler

`deposit_watcher/` is that reconciler. It reads `Balances.Transfer` events out of finalized blocks
and credits the account whose coldkey sent each one, so the normal path issues credits without a
declared deposit or a claim at all — one arrives, it is recorded, and it is credited. A declared
deposit for exactly the amount that arrives is settled by it rather than shadowed by a second row.

**What is credited is the amount observed on chain**, and the credit count stays derived from the
ledger: 0.7 TAO at a 0.5 TAO price is one credit with 0.2 TAO left towards the next.

An arrival whose sending coldkey belongs to no account is recorded in `chain_transfers` as
`UNATTRIBUTED`, with the reason on the row, and waits for a human — which is what the claim
endpoint above and the operator queue are for. Nothing is lost; nothing is guessed. See the
deposit-watcher section of `README.md` for how it is configured and run.

The `btcli` amount is rendered from integer rao by string arithmetic. `amount_rao / 1e9` is
exactly the step that silently loses a rao.

## Submitting with a credit

Four calls, so that a miner learns their bundle is admissible **before** anything is charged:

```
POST /v1/submissions/preflight              free, no credit, no state, no auth
POST /v1/submissions/intents                holds one credit
PUT  /v1/submissions/intents/{id}/bundle    admits the bundle, returns the digest to sign
POST /v1/submissions/intents/{id}/confirm   debits the credit, writes the submission
```

**Why hold at step 2 rather than charge at step 4.** Without a hold, a miner with one credit could
open any number of intents, upload to all of them, and race the confirmations. `open_intent` locks
the account row before reading the balance, so two concurrent calls cannot both see the last
credit.

**Why the server computes the digest at step 3.** The client must never choose what it is signing.
`request_digest` is canonical JSON over the intent id, the submitting hotkey, the task, the task
digest, and the proof digest **as admitted** — so a captured signature cannot be moved to
different bytes, a different task, or a different attempt. Re-uploading replaces the bundle and
recomputes the digest, invalidating the old signature, so a miner who uploaded the wrong file does
not lose the held credit.

**`confirm` is atomic.** Under an account-level lock: lock, re-read the intent under that lock,
write the proof, append the `SPEND`, insert the submission pointing at it, mark the intent
confirmed. Either the debit and the submission both exist, or neither does. A second confirm is a
`409` carrying the original submission id — the intent is the idempotency key, and the partial
unique index on `credit_ledger.intent_id` would refuse the duplicate debit regardless.

**Preflight is free and unauthenticated on purpose.** It runs the same admission the paid path
runs, writes nothing, and is bounded by the body cap. A paid endpoint that rejects malformed
bundles is a bad deal, and pushing miners to test against the paid path would be worse for both
sides. A refused bundle is `ok: false` with a reason code and a `200` — the question "would this be
accepted" was answered successfully.

## Ownership

Every read under `/v1/me` scopes on the account id **in the query**, not after it. A row belonging
to someone else is reported **absent**, never forbidden, so an identifier cannot be probed for
existence by watching which error comes back. That applies to submissions, deposits, intents, and
the timeline alike.

Nothing under `/v1/me` is cacheable: every response sets `Cache-Control: no-store`. A balance or a
submission list is caller-specific, and the public surface's `Cache-Control: public` would be a
cross-account disclosure through any shared cache.

`/v1/me/submissions/{id}/report` returns the **complete** report, `stdout_tail` and `stderr_tail`
included — the output quotes the owner's own proof back at them, which is what they need to fix it
and is not a disclosure to anyone else. Contrast the public subset in
[PUBLIC_API.md](PUBLIC_API.md#what-is-never-published).

`/v1/me/submissions/{id}` returns the latest binding review with its reason code and
`notes_public`. It never returns the reviewer's internal `notes`, identity, or raw advisory-agent
evidence. Keeping the two note fields separate lets the team write an audit trail without making
those internal bytes part of the API contract.

## Two ways to fund a submission

Both paths still require money to have been confirmed before a submission row exists. What changed
in V003 is *what names the money*:

| | Extrinsic path | Credit path |
| --- | --- | --- |
| Endpoint | `POST /v1/submissions` | the four-call intent flow |
| Auth | hotkey signature | session cookie + CSRF |
| Funded by | one finalized transfer | one `SPEND` ledger entry |
| Row names | `payment_reference`, `payment_sender`, `payment_amount_rao`, `payment_block` | `credit_ledger_id`, `intent_id`, `account_id` |
| Idempotency key | client-supplied UUID | the intent id |

`submissions` carries a CHECK that **exactly one** of the two holds per row
(`submission_funded_exactly_once`), so `FundingSummary.source` on the detail response is a read of
durable state rather than a guess. The invariant weakened from "always has a payment" to "always
has exactly one funding source"; it did not weaken to "may be unfunded".

## Roles

`MINER` on every account, granted at signup. `REVIEWER` and `ADMIN` gate the Stage 3 review queue
and are granted **out of band** — roles are never client input, and `PATCH /v1/me` rejects the
field outright rather than ignoring it.

## Configuration

See [`../.env.example`](../.env.example). The four that production refuses to start without or
with the wrong value:

| Variable | Rule |
| --- | --- |
| `WEBSITE_BASE_URL` | Required, https. Where the sign-in link points — a link to a guessed origin is a credential sent somewhere nobody chose |
| `MAIL_SENDER` | Must be `smtp`. `console` writes sign-in links to the process log |
| `PUBLIC_CURSOR_SECRET` | Required, 32+ chars, and refused if it is the constant published in `settings.py` |
| `PUBLIC_ACTIVITY_SALT` | Same rules |

## Tests

```bash
docker compose -f docker-compose.pytest-db.yml up -d
.venv/bin/pytest tests/test_api_accounts.py
```

Mostly about what must *not* work: a write without a CSRF token, a token borrowed from another
session, a magic link used twice, a signature replayed from the link flow into the sign-in flow, an
account reading another account's rows, one credit spent twice. The signatures are real sr25519 over
the exact messages the server minted.
