# Account API

The signed-in surface: sign in, manage an account, buy and spend credits, and submit a proof by
spending one. Served by the same process as the miner-facing intake ([API.md](API.md)) and the
public read surface ([PUBLIC_API.md](PUBLIC_API.md)), with a third set of rules.

| Method | Path | Contract | Purpose |
| --- | --- | --- | --- |
| `GET` | `/v1/auth/session` | `SessionEnvelope` | The whole signed-in state, or `401` |
| `POST` | `/v1/auth/email/request-link` | `202` | Mail a single-use sign-in link |
| `POST` | `/v1/auth/email/verify` | `SessionEnvelope` | Exchange the token for a session |
| `POST` | `/v1/auth/google/callback` | `303` | Verify Google's redirect-mode ID token, open a session |
| `POST` | `/v1/auth/google/link` | `SessionEnvelope` | Attach Google to the signed-in account — browser only |
| `POST` | `/v1/auth/wallet/challenge` | `{ nonce, message, expires_at }` | A nonce and the exact message to sign |
| `POST` | `/v1/auth/wallet/verify` | `SessionEnvelope` | Verify the signature, open a session |
| `POST` | `/v1/auth/cli/challenge` | `{ nonce, message, expires_at }` | A nonce for a hotkey to sign |
| `POST` | `/v1/auth/cli/verify` | `CliSession` | Verify the hotkey signature, mint a bearer token |
| `POST` | `/v1/auth/logout` | `204` | Revoke **this** session; clear the cookies if it is one |
| `GET` | `/v1/me` | `Account` | Profile, roles, linked keys, payout address |
| `PATCH` | `/v1/me` | `Account` | Edit `display_name` — browser only |
| `GET` | `/v1/me/sessions` | `SessionView[]` | Every live session, both kinds |
| `DELETE` | `/v1/me/sessions/{id}` | `204` | Revoke one session |
| `DELETE` | `/v1/me/sessions?kind=` | `204` | Revoke every *other* session, optionally of one kind |
| `POST` | `/v1/me/wallets/challenge` | `{ nonce, message }` | A nonce for linking another coldkey — browser only |
| `POST` | `/v1/me/wallets` | `Account` | Link another coldkey by signature — browser only |
| `POST` | `/v1/me/hotkeys/challenge` | `{ nonce, message }` | A nonce for linking a hotkey — browser only |
| `POST` | `/v1/me/hotkeys` | `Account` | Link a hotkey by signature — browser only |
| `PUT` | `/v1/me/payout` | `Account` | Payout destination: coldkey plus hotkey — browser only |
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
| `GET` | `/v1/admin/accounts/{id}` | `Account` | One account — `ADMIN`, browser only |
| `PUT` | `/v1/admin/accounts/{id}/roles` | `Account` | Replace an account's roles — `ADMIN`, browser only |
| `GET` | `/v1/admin/accounts/{id}/sessions` | `SessionView[]` | An account's live sessions — `ADMIN`, browser only |
| `DELETE` | `/v1/admin/accounts/{id}/sessions` | `204` | Cut every credential an account holds — `ADMIN`, browser only |

Response models are in
[`../submission_api/schemas_account.py`](../submission_api/schemas_account.py) — the third of
three model modules, and the one where a hotkey, an email, a payout address or a balance is
*allowed* to appear, because everything here is served only to the authenticated owner of the
data. The separation is the point: a field on `Account` would be a serious disclosure on
`PublicResult`.

`PENDING` reward events are internal instructions and are not returned by `SubmissionDetail.reward`
or `/v1/me/rewards`: no successful chain event exists yet, so calling them Paying would be false.
The payout watcher exposes `SUBMITTED` only while a matching event exists on the best chain and
`CONFIRMED` only after that block finalizes. A best-chain reorganization removes `SUBMITTED` and
returns the tracker to pending. `extrinsic_reference`, `submitted_block`, `finalized_block`, and
`confirmed_at` therefore come from chain events rather than an operator-maintained flag.
Rows created before chain reconciliation carry no observation provenance and are hidden until the
watcher replays and verifies them; a database `CONFIRMED` assertion by itself is never exposed as
Paid.

## The session envelope

`GET /v1/auth/session` is what a client calls on load, and it answers with everything the
signed-in shell needs to draw itself. Both sign-in endpoints return the identical body, so a
client that has just signed in does not have to immediately read the session back.

```jsonc
{
  "account":  { "id": "…", "email": "…", "email_verified": true, "display_name": null,
                "roles": ["MINER"], "payout": null, "hotkeys": [], "wallets": [],
                "identities": [ … ], "created_at": "2026-07-02T09:14:00Z" },

  "identities": [ { "provider": "email",   "label": "db@dendrite.holdings",
                    "linked_at": "2026-07-02T09:14:00Z" },
                  { "provider": "google",  "label": "db@dendrite.holdings",
                    "linked_at": "2026-08-14T08:02:00Z" },
                  { "provider": "coldkey", "label": "5Fh3…9xQ",
                    "linked_at": "2026-08-01T11:20:00Z" } ],

  "hotkeys":  [ { "hotkey": "5Gk2…7aP", "label": null,
                  "linked_at": "2026-08-03T18:44:00Z" } ],
  "payout":   { "coldkey": "5Fh3…9xQ", "hotkey": "5Gk2…7aP" },   // null until set

  "credits":  { "balance": 3, "held": 1 },                        // whole credits
  "counts":   { "submissions_total": 12, "submissions_in_review": 2,
                "rewards_unclaimed": 1, "review_queue": null },

  "capabilities": {
    "submit":       { "allowed": false, "missing": ["INSUFFICIENT_CREDITS"] },
    "buy_credits":  { "allowed": true,  "missing": [] },
    "set_payout":   { "allowed": true,  "missing": [] },
    "review":       { "allowed": false, "missing": ["ROLE_REQUIRED"] },
    "manage_roles": { "allowed": false, "missing": ["ROLE_REQUIRED"] }
  }
}
```

`account` is the canonical record and is unchanged. **`identities`, `hotkeys` and `payout` are
derived from it in the same call**, never read separately, so the two halves of the body cannot
disagree. They are flattened because the account page groups by "ways in" and "keys I mine with",
which is not how the account row is shaped.

**`identities` is every way back in: a verified mailbox, each linked external provider, and each
linked coldkey.** An unverified address is not listed — it is not a way in, and saying otherwise
would tell someone they have a recovery channel they do not have. `provider` is a plain string
rather than an enum on the wire, so a client renders an unrecognised value as "some other login"
rather than failing to parse the session it is signed in with; `google` is the only external
provider the database accepts today, and the column's CHECK is what will admit the next one.

`account.identities` is the provider-shaped record — the same rows with `last_used_at`. The
top-level array is the flattened union across all three kinds, which is what a "how do I get back
in" list actually needs. Neither exposes the Google **subject**: that is the login key, and it
belongs in the database rather than in a body page script can read.

**`linked_at` on the `email` row is a lower bound, not an exact answer.** It is the account's
creation time, which is exact for a magic-link signup — setting the address *is* what creates the
account — and early for a coldkey-first account that later linked Google and adopted the
provider's address. `accounts.email` has no companion timestamp, so there is nothing more
accurate to report; the Google row beside it carries its own true `linked_at`.

A `coldkey` identity does **not** record which wallet produced the signature. Talisman, the
tao.com wallet and `btcli` all emit the same sr25519 signature over the same message, and nothing
in the sign-in flow observes the difference, so there is no honest field for it. If wallet
provenance is ever needed, it has to be captured at sign-in — it cannot be recovered later.

**`credits` is whole credits, and the two numbers do not overlap.** `balance` is spendable *now*,
already net of `held`; `held` is what open submission intents have claimed. The account's total
is `balance + held` — do not subtract again. `GET /v1/me/credits` remains the rao-denominated
picture, including the sub-credit remainder this rounds away.

**`counts` are badge numbers.** `rewards_unclaimed` is approved work whose payout has not
confirmed: nothing is *claimed* in this system, since the payout worker pushes rewards on chain,
so read it as "owed" rather than as an action waiting for the account holder. `review_queue` is
the shared queue depth and is `null` unless this caller may actually open the queue — a populated
badge for someone who cannot act on it only leads to a `403`.

**`capabilities` is advice, never enforcement.** Every gate is checked again at the endpoint,
against state that may have moved since this was read; a client that trusted `allowed: true` and
dropped its error path would still be wrong the moment a credit is spent in another tab. What it
buys is that a greyed-out button has a reason: `missing` carries the same `reason_code` strings
the corresponding endpoint refuses with, in the order that endpoint checks them, so "why is this
disabled" and "why did that `403`" answer with the same word. Without it, every client
re-implements the authorisation rules from `roles`, `hotkeys` and `credits` — and drifts from the
server the first time one changes.

| Capability | Gated on |
| --- | --- |
| `submit` | `SUBMISSIONS_PAUSED`, `HOTKEY_NOT_LINKED`, `INSUFFICIENT_CREDITS` |
| `buy_credits` | `BROWSER_SESSION_REQUIRED` — both funding paths are cookie-only |
| `set_payout` | `BROWSER_SESSION_REQUIRED`, `HOTKEY_NOT_LINKED` |
| `review` | `ROLE_REQUIRED` (`REVIEWER`), `ROLE_REQUIRES_BROWSER_SESSION` |
| `manage_roles` | `ROLE_REQUIRED` (`ADMIN`), `ROLE_REQUIRES_BROWSER_SESSION` |

Both role codes can appear at once, and that is the useful case rather than an edge one: an admin
on the CLI is told the role is held *and* that this credential cannot exercise it.

The whole envelope is redacted for a CLI bearer session on the same rule as `Account`, and
inherits it rather than re-implementing it — everything is built from the already-redacted
account, so a bearer caller gets `identities: []`, `payout: null` and only its own hotkey without
the builder branching on the credential. `credits` is the deliberate exception: spending them is
most of what the CLI does, and discovering an empty balance by being refused would be worse.

`Cache-Control: no-store` on all of it. The body was already caller-dependent; it now also
carries a balance and a set of permissions, and a shared cache serving one account's capabilities
to another would be an authorisation bug wearing a caching bug's clothes.

`hotkeys[].label` is always `null` today. The field ships now so the account page can be built
against its final shape; populating it needs a nullable column on `linked_hotkeys` and an endpoint
to set it. Null is honest — there is no name — and a client should fall back to a truncated
`hotkey`.

## Sessions

An opaque token backed by a row. Deliberately not a JWT: a JWT here would be either short-lived —
meaning a refresh mechanism, meaning a second credential — or long-lived and unrevocable, meaning
a logout that does not log anything out. One `UPDATE` revokes a row.

**Two kinds, in one table.** A browser gets an HttpOnly cookie; the miner CLI gets a bearer token
in an `Authorization` header. Everything that matters is shared — 256 bits from the OS CSPRNG,
stored only as a digest, an expiry, revocation in one `UPDATE` — so they are one table with a
`kind` discriminator rather than two tables that would duplicate the authenticate/revoke/expire
logic and then drift.

| | `COOKIE` | `BEARER` |
| --- | --- | --- |
| Opened by | coldkey signature, or a mailbox | a **linked** hotkey's signature |
| Held in | `conjectures_session` cookie | `~/.config/conjectures/session.json`, mode `0600` |
| Ambient (browser attaches it unasked) | yes — so writes must prove their initiator | no — nothing to prove |
| Scoped to | the account | one hotkey (`hotkey_scope`) |
| Lifetime | `SESSION_DAYS` rolling, uncapped | `CLI_SESSION_DAYS` rolling, capped at `CLI_SESSION_MAX_DAYS` |
| May take over the account | yes — it is the account holder | **no**, see below |

The two are **not interchangeable**. `accounts.authenticate` takes the kind it expects and puts
it in the predicate, so a cookie token replayed in an `Authorization` header resolves to nothing,
and a bearer token planted in the session cookie resolves to nothing. Neither is reachable by an
attacker who does not already hold the secret — the cookie is HttpOnly — but the two carry
different write obligations, and a credential that can change which rules apply to it by changing
where it is presented is much cheaper to forbid than to reason about at every call site.

**A bearer token is the weaker credential, and the API treats it that way.** Bittensor stores a
hotkey unencrypted on disk by design — that is the point of the coldkey/hotkey split — so a token
minted by one is roughly as protected as a file on a mining box. Three consequences, all
enforced rather than advised:

* The writes that change *who the account is* or *where its money goes* require a browser
  session: linking a hotkey, setting the payout destination, editing the profile, declaring or
  claiming a deposit. Left open, those compose into full account takeover from one stolen file —
  link an attacker's hotkey, repoint the payout, collect. The refusal is `403`
  `BROWSER_SESSION_REQUIRED`.
* Reads are **redacted**: no email address, no payout keys, no coldkey, and only the one hotkey
  the token is scoped to. `GET /v1/me` and `GET /v1/auth/session` both apply it, so the full
  record is not reachable by asking a different endpoint.
* `REVIEWER` and `ADMIN` cannot be exercised from one at all — `403`
  `ROLE_REQUIRES_BROWSER_SESSION`, even when the account genuinely holds the role.

One cookie, and nothing script can read:

| Cookie | Flags | Why |
| --- | --- | --- |
| `conjectures_session` | `HttpOnly`, `Secure`, `SameSite=Lax` | The credential. HttpOnly so page script cannot exfiltrate it — an XSS is then confined to acting within the page rather than stealing something durable. |

There used to be a second, `conjectures_csrf`, deliberately readable by script so the frontend
could echo it into a header. It is gone; see [Cross-site writes](#cross-site-writes). Sign-in and
sign-out both send a `Max-Age=0` header for that name so a browser holding one from the previous
version is rid of it rather than carrying it for the rest of its 30-day lifetime.

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

**A sign-in retires every earlier *browser* session for that account.** Whatever that browser
could reach before, the only live cookie afterwards is the one just issued. CLI tokens are
deliberately outside that scope: they live on other machines and represent long-running work, and
an unscoped revoke would mean that every visit to the website silently killed every rig's session
— a failure nobody would attribute to having opened a web page.

**Every session is listable and individually revocable.** `GET /v1/me/sessions` shows both kinds
with the caller's own marked, `DELETE /v1/me/sessions/{id}` kills one, and
`DELETE /v1/me/sessions?kind=BEARER` kills every other one of a kind while sparing the caller.
Without these a leaked CLI token would have no remedy short of waiting out its expiry. A session
id belonging to another account answers `404`, the same as one that never existed — session ids
name live credentials and must not be probeable.

An account holds at most `CLI_SESSIONS_PER_ACCOUNT` live CLI tokens. Reaching the ceiling evicts
the oldest rather than refusing the newest: a stale token on a decommissioned rig must not be
able to lock a miner out of the machine they are sitting at.

## Cross-site writes

A cookie is an **ambient** credential: the browser attaches it to any request to this origin,
including one a page on another site caused. That is the whole of cross-site request forgery, and
it is why a write authenticated by a cookie has to prove where it was initiated.

The proof is two request headers the browser writes and **no page can set** — both are on the
Fetch spec's forbidden-header list, so `fetch`, `XMLHttpRequest`, `<form>` and `sendBeacon` are
all barred from setting or overriding them:

| Header | What it says | Where it is blind |
| --- | --- | --- |
| `Origin` | The origin of the document that initiated the request. Sent by every current browser on every state-changing request, cross-origin *and* same-origin, in all three form encodings. A document with an opaque origin — sandboxed iframe, `data:` URL, `file://` — sends the literal `null`, which is refused by name. | Can be stripped by an intermediary; absent from browsers old enough not to matter. |
| `Sec-Fetch-Site` | How the initiator relates to the target: `same-origin`, `same-site`, `cross-site`, or `none`. | Chrome 76+, Firefox 90+, **Safari 16.4+** (March 2023). Older Safari sends nothing. |

The rule, in `submission_api/origin_policy.py`, is an **OR** and produces three outcomes:

* **`Origin` is on the write allowlist** → allowed, whatever `Sec-Fetch-Site` says. This is not a
  gap; it is what lets the website live on an origin other than the API's. `conjectures.io`
  calling `api.conjectures.io` is `same-site`, and `www.conjectures.io` calling the apex is too.
  Demanding `same-origin` as well would mean the API can only ever be reverse-proxied under the
  website's own origin.
* **`Origin` is present and *not* on the allowlist** → refused. Likewise `Origin: null`.
* **No `Origin`, but `Sec-Fetch-Site` is `same-origin` or `none`** → allowed. The fallback for a
  stripped or absent `Origin`. `same-site` is refused alongside `cross-site`: a sibling subdomain
  is not this origin, and treating it as one is how one subdomain takeover becomes account access.
* **Neither header** → *unproven*, which is neither of the above and is the reason the result is
  not a boolean.

Unproven is handled in two places that fail in **opposite directions**, and the pair is the
design:

| Layer | On unproven | Why |
| --- | --- | --- |
| `CrossOriginWriteGuard` (middleware, all `/v1` writes) | lets it through | A request with neither header is not from a browser, and a non-browser has no ambient credential for a hostile page to ride on. Miner tooling, the CLI, `curl` and the TMC PAY webhook all land here. It still catches the unauthenticated writes that have no principal to inspect — `POST /v1/auth/email/request-link` sends mail, so a cross-site page must not be able to trigger it. |
| `require_writer` (route dependency, authenticated writes) | **refuses** | It knows the request authenticated with a cookie. Silence is not proof, and this is the load-bearing half: with no token as a backstop, absence has to be a refusal. |

Refusals are `403` with `reason_code: CROSS_SITE_WRITE_REFUSED`.

Which dependency a handler names *is* its access control:

```python
OptionalPrincipalDep   # may be signed in — GET /v1/auth/session only
PrincipalDep           # must be signed in — every read
WriterDep              # signed in AND proved where the request was initiated — every write
CookieWriterDep        # all of that, from a browser session — the writes a CLI may not make
```

A state-changing handler that names `PrincipalDep` is a forgery hole, which is why the names are
deliberately not interchangeable-looking.

### The allowlist

`WRITE_ALLOWED_ORIGINS`, which defaults to `CORS_ALLOWED_ORIGINS` when unset. They are separable
because reading the public catalog and spending an account's credits are different grants; most
deployments have one website and want one list, and then the distinction is invisible. Set-but-
empty is meaningful and not the same as unset: it means no browser may write here at all.

Both lists take the same validation — exact `scheme://host[:port]`, no wildcard and no `http://`
in production, and `null` is unrepresentable.

### Why there is no CSRF token

There was one until recently: a second cookie, `conjectures_csrf`, deliberately readable by page
script so the frontend could echo it into `X-Conjectures-CSRF`, compared against a digest on the
session row. It is gone, and the reasoning is worth keeping written down.

* **It defended against nothing the headers do not.** Both are unforgeable by a cross-site page,
  and a cross-site page is the entire threat model of CSRF.
* **It never defended against XSS on an allowlisted origin.** It could not: it had to be readable
  by same-origin script in order to be echoed. Script that can read the token can also just make
  the request. The header check has exactly the same property, and neither is a mitigation for
  XSS — that is what the CSP, the HttpOnly session cookie and the exact-origin allowlist are for.
* **It cost a second cookie, a database column, a biconditional CHECK, and a contract the
  frontend had to implement correctly on every write.** Each of those is a place to get it wrong.

What was *not* free about the removal: the check must now **fail closed** when neither header
arrives, and non-browser clients holding a cookie session must send one of them themselves.
`scripts/link_hotkey.py` sends `Sec-Fetch-Site: same-origin`. That is not a bypass — outside a
browser the header is an ordinary string anybody can type, and it does not matter, because the
guard exists to stop a hostile *page* from riding on a cookie the browser attached by itself. A
local process that can set arbitrary headers is already holding the cookie deliberately, and a
token would have been no different: whatever can send the cookie can send the token beside it.

`X-Conjectures-CSRF` remains in `CORS_REQUEST_HEADERS`, deprecated and unread, only so that a
frontend build predating the change does not fail preflight during a rolling deploy. Delete it
once no deployed frontend still sends it.

### The bearer exemption

**A bearer session does not have to prove anything, because there is nothing for it to prove.** A
bearer token is not ambient: it is sent only by code that deliberately set the header, and code on
this origin that can set it can already make the request directly. A CLI sends neither initiator
header, so a fail-closed check would refuse every CLI write for no security gain.

The exemption is read off the authenticated **session row**, never off the shape of the request —
a caller cannot claim it by presenting a header. Two things keep it sound, and both are
load-bearing:

* `authenticate` matches on `kind`, so a cookie credential offered in an `Authorization` header
  does not resolve at all and therefore cannot inherit the exemption.
* `Authorization` is **not** in `CORS_REQUEST_HEADERS` and must never be added. That allowlist is
  the only thing preventing a page on an allowlisted origin from sending the header cross-origin,
  and adding it is the single change that would make the exemption browser-reachable. The CLI is
  not a browser, sends no `Origin`, and is not subject to CORS, so it loses nothing.

### Path exemptions

`POST /v1/submissions` and `/v1/submissions/preflight` carry no cookie and authenticate a hotkey
signature instead, so there is no ambient credential to abuse. `POST /v1/auth/google/callback` is
exempt because it is a genuine cross-site POST from `accounts.google.com`; it performs Google's
own `g_csrf_token` double-submit before reading the ID token, and `SameSite=Lax` means no session
cookie rides along with it anyway. The TMC PAY webhook is exempt because its caller is a payment
processor authenticated by an HMAC over the raw body.

The CLI session endpoints are deliberately **not** on that list. An unproven request already
passes the middleware, which is exactly the non-browser case, and a path exemption would also
exempt a browser holding a cookie session on those routes.

### What this does not cover

`SameSite=Lax` on the session cookie is a third, independent layer: a current browser does not
attach a `Lax` cookie to *any* cross-site POST, so the header checks are the belt to its braces.
What none of them address is script running **on an allowlisted origin** — an XSS on the website
passes every check here, because the browser is truthfully reporting a trusted initiator. Nothing
in this section is an XSS mitigation, and the previous token was not one either.

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

### Google

The frontend uses Google Identity Services in redirect mode and posts the credential to
`POST /v1/auth/google/callback`. That callback is the one cross-site write exempt from the normal
session CSRF middleware: Google supplies `g_csrf_token` in both a cookie and the form body, and the
route compares them before it reads the ID token. `google-auth` then verifies the token signature,
issuer, expiry, and exact `GOOGLE_CLIENT_ID` audience. No Google access or refresh token is stored.

The provider's stable `sub` claim identifies the account. Email is bounded metadata and may
change. A callback whose email already belongs to a wallet or magic-link account never merges it
silently; it redirects to `/login?reason=GOOGLE_ACCOUNT_LINK_REQUIRED`. The person signs into the
account they intend to keep and calls `POST /v1/auth/google/link`, which goes through the normal
write guard. One Google subject can belong to one local account, and one local account can have at
most one Google subject; both rules are database unique constraints.

`Account.identities` exposes provider, observed email, linked time, and last-used time to the
account owner. The stable provider subject is deliberately never returned.

### Wallet

Six things this validator asks a key to sign, each **domain-separated** so a signature harvested
from one is worthless in another:

```
conjectures-login-v1          sign in with a coldkey
conjectures-coldkey-link-v1   attach another coldkey to an account
conjectures-hotkey-link-v1    attach a hotkey to an account
conjectures-cli-session-v1    open a CLI session with an already-linked hotkey
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
address by spamming invalid signatures. The cost of that choice is that an open challenge would
otherwise accept unlimited signature attempts, so a challenge is spent after
`LOGIN_CHALLENGE_ATTEMPTS` failures.

**An account may hold several coldkeys.** `POST /v1/me/wallets/challenge` mints a nonce bound to
both the signed-in account and the coldkey; `POST /v1/me/wallets` redeems it with the nonce echoed
back beside the signature:

```
POST /v1/me/wallets/challenge  { coldkey }                      -> { nonce, message, expires_at }
POST /v1/me/wallets            { coldkey, nonce, signature }    -> Account
```

The nonce is echoed for the reason the CLI flow echoes its own: it names the row the signature is
checked against, so two challenges for one coldkey coexist instead of the newer invalidating the
one being signed, and the attempt ceiling applies per challenge rather than per address. A coldkey
belongs to exactly one account, and there is deliberately no unlink or rebind — moving a login
credential between accounts needs a recovery policy, not a silent ownership change.

### CLI

The miner CLI cannot open a browser and does not hold a coldkey in normal operation, so it signs
with a **hotkey** — and only one that has already been linked to an account in the browser. That
prerequisite is the whole security story: a hotkey can never create an account or attach itself to
one, so compromising a hotkey never produces a new identity, only a session on an identity that
already chose to include it.

```
POST /v1/auth/cli/challenge  { address }                      -> { nonce, message, expires_at }
POST /v1/auth/cli/verify     { address, nonce, signature }    -> CliSession
```

**The prerequisite has its own command**, `conjectures auth register`, which walks the four calls a
website would — coldkey challenge, coldkey verify, hotkey challenge, hotkey link — creating the
account on first sign-in, because proving control of an unclaimed coldkey *is* signing up. It opens
a cookie session, makes the one write, and revokes it before returning, so the browser credential
never reaches disk. `scripts/link_hotkey.py` does the same four calls from this repo, for testing a
deployment without installing the miner CLI.

```
conjectures auth register --wallet default --hotkey default   # the miner's route
python3 scripts/link_hotkey.py --api http://localhost:8000    # the validator's own
```

**The challenge endpoint does not say whether the hotkey is linked.** Hotkeys are published on
chain, so anyone can ask about anyone's key; a differing answer would be a free oracle mapping
hotkeys to accounts on this deployment. The linkage is checked at verify, once a signature has
proved the caller controls the key — at which point they are entitled to know, and an unlinked
hotkey is `403 HOTKEY_NOT_LINKED`.

**The nonce is echoed back at verify**, unlike the coldkey flow, and this is the one place the two
differ in shape. The coldkey flow resolves "the latest open challenge for this address", which is
a denial-of-service primitive whenever the address is public: request a challenge for someone
else's hotkey once a minute and their own signature is never over the latest message, so they can
never log in. Addressing the challenge by its own nonce removes the race — two challenges for one
address coexist, each redeemable by whoever holds its nonce. The nonce is not the proof; the
signature is, checked against the message stored on that row.

The five steps of verify are ordered deliberately, and each boundary answers a specific failure:

1. Find the challenge **by its nonce**, not by recency.
2. Verify the signature over the **stored** message — before anything is consumed or disclosed.
3. On failure, count an attempt and refuse. The challenge survives one wrong signature, but not
   many.
4. Resolve the account, and refuse an unlinked hotkey **with the nonce still unspent**. This is
   the common first-run error, and burning the nonce would cost a fresh challenge, a fresh
   passphrase prompt and a fresh signature for a condition the miner must fix in a browser anyway.
5. Consume, then issue — so the nonce is spent exactly when a token comes into existence.

`CliSession` is the only response in the API that carries a live credential. It is `POST`-only,
`Cache-Control: no-store`, and the token is never a field on a telemetry event. It is prefixed
`conj_cli_` — worth nothing cryptographically, but it makes a token that leaks into shell history,
CI logs or a committed dotfile findable by a secret scanner.

**A note for client authors.** The CLI must sign the server's message *bytes* verbatim, never a
locally rebuilt copy — but it must also **check what it is about to sign** before unlocking the
key: that the first line is exactly `conjectures-cli-session-v1`, that `address:` is its own
hotkey, and that `domain:` is the validator it meant to talk to. Signing whatever a server sends
turns the CLI into a blind signing oracle for the other four prefixes, and a typo'd `--api` or a
poisoned environment variable is then enough to collect a hotkey-link signature for someone else's
account. Validate the shape, then sign the bytes as received.

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
  "amount_rao": 500000000,
  "credits_expected": 1,
  "credited_rao": null,
  "btcli_command": "btcli wallet transfer --dest 5C4h… --amount 0.5"
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

### Buying credits through TMC PAY

The second funding path, and the price is the same: `CREDIT_PRICE_RAO` per credit, 0.5 TAO. Instead
of transferring to the treasury and waiting for the watcher, the buyer pays a TMC PAY invoice in
TAO; the processor confirms it and settles to the treasury later, in batches, net of commission.

```
POST /v1/me/credits/tmc-pay/orders          browser session + write guard; creates an invoice
GET  /v1/me/credits/tmc-pay/orders          this account's purchases
GET  /v1/me/credits/tmc-pay/orders/{id}     poll; refreshes from TMC PAY while it is open
POST /v1/webhooks/tmc-pay                   TMC PAY only, authenticated by HMAC
```

**This path is processor-trusted, and that is a real difference.** Everywhere else credits exist
only for a transfer this validator read off finalized Subtensor state itself. Here the deposit
address belongs to TMC PAY, so at the moment of purchase there is nothing of ours on chain to read
— the evidence is a signed webhook plus a re-readable invoice. Three things follow, and all three
are enforced rather than remembered:

* **The ledger says which kind of rao it is.** A `DEPOSIT` entry names *either* `deposit_id` (chain)
  *or* `tmc_pay_order_id` (processor), never both and never neither — `ledger_deposit_names_its_deposit`
  is an exclusive-or. So `WHERE tmc_pay_order_id IS NOT NULL` separates the two, and the on-chain
  deposit invariants are untouched.
* **A webhook decides *whether* to credit, never *how much*.** What is credited is
  `crypto_amount_rao`, the TAO the invoice locked when it was created. A forged body cannot mint
  credits even if the signing secret leaks — at worst it can settle an invoice that already exists.
* **Crediting is idempotent three ways over**: a status check under a row lock, `UNIQUE` on
  `tmc_pay_orders.credited_ledger_id`, and a partial `UNIQUE` on `credit_ledger.tmc_pay_order_id`.
  A duplicate delivery racing the reconciler conflicts instead of paying twice.

**Why the invoice is quoted in fiat.** TMC PAY accepts a fiat amount and derives the crypto amount
from a rate it locks at creation, so the TAO figure is a consequence rather than a request. The
conversion runs like this, all in `Decimal`, never `float`:

```
crypto_per_fiat    ← the rate ladder below      # TAO per one fiat unit, TMC PAY's own semantics
required_rao       = credits × CREDIT_PRICE_RAO                     # 10 credits → 5 000 000 000
fiat_amount        = ⌈ required_rao/1e9 ÷ crypto_per_fiat × (1 + margin_bps/10000) ⌉   to the cent
```

**The rate is TMC's own, at every rung that matters.** Three of TMC PAY's own rates are readable,
and all of them beat a third-party feed:

| Rung | `rate_source` | Source | When |
| --- | --- | --- | --- |
| 1 | `invoice` | `exchange_rate` on our last invoice | It is newer than `TMC_PAY_RATE_TTL_SECONDS` (300) and in this currency |
| 2 | `tmc-pay` | TMC PAY's `GET /api/v1/rates` | No fresh locked rate, merchant currency is USD |
| 3 | `taomarketcap` | TaoMarketCap 5-minute candles | The rate endpoint is unreachable |
| 4 | `taostats` | TaoStats, if `TAOSTATS_API_KEY` is set | Both of the above unreachable |
| 5 | `invoice-stale` | Any locked rate we have | Every feed down, or currency is not USD |
| 6 | `<source>-currency-mismatch` | A USD feed, used for a non-USD merchant | First-ever non-USD purchase. Warns |
| — | `503 TMC_PAY_RATE_UNAVAILABLE` | — | Nothing to price from. The sale is refused |

Rung 1 is the best because it is the rate the platform *actually locked*, spread and rounding
included, already in the merchant's currency. Rung 2 is TMC PAY quoting the rate it prices invoices
with, which is the same thing one step before the lock; it publishes fiat per whole crypto unit, so
it is the reciprocal of rung 1's figure and `_seed_rate` inverts it. Rung 3 is the same platform's
market data rather than its quote. Rungs 2 and 3 both need no API key, so a deployment can price
invoices with no rate configuration at all — and rung 2 sends **no** merchant credential, because
the endpoint requires none.

Rung 5 is deliberate: because the band below cannot be fooled by a bad seed, an hour-old rate is a
far better answer to a feed outage than refusing to sell credits. `rate_source` and `quote_attempts`
are on the `tmc_pay_order_created` event, so "which source are we actually running on" and "how
often does a purchase cost two invoices" are each one query.

**Candle caching.** `rates.TaoMarketCapPriceReader` caches each price **to the candle boundary, not
for a fixed duration**: a price fetched at 13:12 is the freshest that will exist until 13:15, so it
is held for exactly three minutes. A rolling five-minute TTL would hold it until 13:17 and drift
further with each refresh. A failed refresh is **never** cached — the last good price keeps being
served (bounded by `rates.MAX_STALE_SECONDS`, one hour) and every call retries until one succeeds.
Caching the failure would refuse purchases for five minutes over one timeout.

Then the invoice that comes back is **checked rather than trusted**: TAO on Bittensor, the
configured merchant, and a locked amount inside a band —

| Edge | Value | Why |
| --- | --- | --- |
| Floor | `required_rao` | Below it, a credit is sold for less than `CREDIT_PRICE_RAO` |
| Ceiling | `required_rao × (1 + TMC_PAY_MAX_SLIPPAGE_BPS) + one minor fiat unit in rao` | Above it, **the buyer is overcharged** |

The ceiling has two parts and they differ in kind. `TMC_PAY_MAX_SLIPPAGE_BPS` (100, i.e. 1%) is
**policy** — what counts as an acceptable overcharge is a business decision, so it is an operator
setting rather than something derived. The extra minor fiat unit is **arithmetic**: the fiat ask must
round up to the currency's minor unit, so that cent is unavoidable and sits on top of the policy.
Slippage must be at least `TMC_PAY_QUOTE_MARGIN_BPS`, since the margin is added to every ask; the API
refuses to start on a tighter pair rather than serving purchases that can never succeed.

Outside the band, the next attempt reprices from `invoice.exchange_rate` — the rate that invoice
actually locked, so it is arithmetic rather than an estimate, and it corrects an estimate that was
too high as readily as one that was too low. After `TMC_PAY_QUOTE_ATTEMPTS` the sale is refused and
the order is `FAILED` with the reason on it.

Both edges matter, and the ceiling is not symmetry for its own sake: a stale TaoStats quote, a
merchant onboarded in a currency TaoStats does not price, or a mistyped margin all produce an
invoice that clears the floor and overcharges. The tolerance is derived rather than a flat
percentage, because the same cent of rounding is noise on a $2000 invoice and a fifth of a $0.05
one. Rounding overshoot inside the band is never lost — it lands in the buyer's own balance as
`remainder_rao`.

**`TMC_PAY_FIAT_CURRENCY` other than `USD` costs one extra round trip, once.** The external feed
only prices dollars, so the first-ever non-USD purchase is seeded from a dollar figure, the band
catches it and the requote fixes it. From the second purchase on, rung 1 or 3 supplies a rate
already in the right currency and there is nothing to correct.

This is why the path needs `TAOSTATS_API_KEY`. Without a live rate there is no honest fiat figure,
and the endpoint answers `503 TMC_PAY_RATE_UNAVAILABLE` rather than inventing one.

**Status is TMC PAY's word, not a translation.** `NEW` and `FAILED` are ours; the other eight —
`CREATED`, `PENDING`, `CONFIRMING`, `UNDERPAID`, `CONFIRMED`, `OVERPAID`, `EXPIRED`,
`LATE_PAYMENT` — are TMC PAY's invoice lifecycle label for label, so what this API reports and what
the TMC PAY dashboard shows are the same word.

Only `CONFIRMED` and `OVERPAID` issue credits. `OVERPAID` credits the invoice amount and flags the
order `needs_review`, because only a person with the dashboard can settle the surplus. `UNDERPAID`
credits nothing and flags too: real money arrived, and part-crediting a whole credit is not a
decision to automate. `LATE_PAYMENT` credits nothing unless an operator sets
`TMC_PAY_CREDIT_LATE_PAYMENTS` — TMC PAY documents it as a manual reconciliation case, and from
outside there is no way to tell whether such a payment settles to the treasury or returns to the
sender.

**TMC PAY dispatches each webhook once and never retries automatically.** A delivery lost to a
deploy is lost, so two things back it up: `GET .../orders/{id}` refreshes from the processor while
the buyer is watching (bounded by `TMC_PAY_POLL_SECONDS`, and never for a settled order), and
`scripts/reconcile_tmc_pay.py` sweeps everything else. Run it on a schedule — every minute or two
is ample against a 30-minute TTL. Both reach the same decision through the same function; there is
one place a status becomes money.

**The commission is the validator's cost, not the buyer's.** Credits still cost 0.5 TAO each, so
what reaches the treasury per credit is 0.5 TAO minus TMC PAY's fee. Raise `CREDIT_PRICE_RAO` if the
full amount has to net.

`GET /v1/catalog/credit-pricing` lists `tmc_pay` in `methods` only when the deployment is configured
for it — a method the page renders and the API refuses is worse than one it never offers.

## Submitting with a credit

Four calls, so that a miner learns their bundle is admissible **before** anything is charged:

```
POST /v1/submissions/preflight              free, no credit, no state, no auth
POST /v1/submissions/intents                holds one credit
PUT  /v1/submissions/intents/{id}/bundle    admits the bundle, returns the digest to sign
POST /v1/submissions/intents/{id}/confirm   debits the credit, writes the submission
```

The intent creation body may include the same opt-in authorship used by the direct-payment path:

```json
{
  "task_id": "fc-…",
  "task_bundle_sha256": "sha256:…",
  "hotkey": "5Grw…",
  "public_credit": {
    "name": "Emmy Noether",
    "url": "https://example.org/emmy-noether",
    "orcid": "0000-0002-1825-0097"
  }
}
```

It is optional. When present, it is frozen on the intent, included in the server-generated digest
the hotkey signs, and copied unchanged to the submission. The account's mutable `display_name` is
not used for result credit.

**Why hold at step 2 rather than charge at step 4.** Without a hold, a miner with one credit could
open any number of intents, upload to all of them, and race the confirmations. `open_intent` locks
the account row before reading the balance, so two concurrent calls cannot both see the last
credit.

**Why the server computes the digest at step 3.** The client must never choose what it is signing.
`request_digest` is canonical JSON over the intent id, the submitting hotkey, the task, the task
digest, the proof digest **as admitted**, and any public credit — so a captured signature cannot be
moved to different bytes, a different task, a different author credit, or a different attempt.
Re-uploading replaces the bundle and recomputes the digest, invalidating the old signature, so a
miner who uploaded the wrong file does not lose the held credit.

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
| Auth | hotkey signature | session cookie + write guard |
| Funded by | one finalized transfer | one `SPEND` ledger entry |
| Row names | `payment_reference`, `payment_sender`, `payment_amount_rao`, `payment_block` | `credit_ledger_id`, `intent_id`, `account_id` |
| Idempotency key | client-supplied UUID | the intent id |

`submissions` carries a CHECK that **exactly one** of the two holds per row
(`submission_funded_exactly_once`), so `FundingSummary.source` on the detail response is a read of
durable state rather than a guess. The invariant weakened from "always has a payment" to "always
has exactly one funding source"; it did not weaken to "may be unfunded".

## Roles

`accounts.roles` is a `TEXT[]` constrained to `{MINER, REVIEWER, ADMIN}`, and that is deliberately
not a permission system. Three values, no attributes on the relation, and every read wants all of
them at once, so a join table and a policy engine would both be machinery with nothing to hold.
What a role *means* is decided where it is used — `require_role(...)` in a route signature —
rather than in a table of grants that then has to be kept in step with the code consulting it.

`MINER` is on every account, granted at signup and re-added on every role change: an account that
exists can mine, and an "admin only" account that could not submit is a state nothing expects.
`REVIEWER` and `ADMIN` gate the review queue and the operator surface.

**Roles are never client input.** `create_account` hardcodes `[MINER]`, and `PATCH /v1/me` rejects
a `roles` field outright rather than ignoring it.

Five rules on the admin surface, each a decision rather than an accident:

* **`PUT` the whole set, not a delta.** The set is what the column stores and what every read
  wants; a grant/revoke API over a three-element array would invent a lost-update problem that
  replacing the value does not have. Unknown roles are `409 UNKNOWN_ROLE`.
* **Neither `ADMIN` nor `REVIEWER` can be exercised from a CLI session** — `403
  ROLE_REQUIRES_BROWSER_SESSION`, even for an account that genuinely holds the role.
  `dependencies.BEARER_ROLES` is `{MINER}`: a hotkey-minted token in a file must not be a route to
  the surface that decides whether a proof earns money. Anything privileged needs the cookie, so a
  reviewer being tested against needs a cookie session and not just a bearer token.
* **There is no bootstrap endpoint.** The first `ADMIN` is granted with
  [`../scripts/grant_admin.sql`](../scripts/grant_admin.sql), by someone with database access. An
  endpoint that could mint the first admin could mint the second, and its access control would
  then be some other secret needing its own rotation story.

Three scripts do this from the database side, for a development deployment only. A session token
is an opaque string stored as a SHA-256 digest, so a row written by hand authenticates exactly as
a minted one does — which is why each of them refuses to run without `-v allow_dev_seed=1`.

| Script | What it does | Credentials |
| --- | --- | --- |
| [`grant_admin.sql`](../scripts/grant_admin.sql) | Grants `ADMIN` to an existing account | none |
| [`seed_dev_accounts.sql`](../scripts/seed_dev_accounts.sql) | Creates a `MINER` and a `REVIEWER` account, each with a linked hotkey | bearer + cookie |
| [`seed_dev_admin.sql`](../scripts/seed_dev_admin.sql) | Creates an `ADMIN`, or adds `ADMIN` to an account named by `-v email=` | cookie only |

The admin script issues no bearer token on purpose: a bearer caller cannot exercise `ADMIN`, so
minting one would mean linking a hotkey to an admin account to produce a credential that cannot
do admin work.
* **An admin cannot remove their own `ADMIN`.** With no other admin it is unrecoverable without
  database access, and the failure is silent until the next time someone needs it.
* **Every grant is an Axiom `roles_changed` event naming both accounts.** `accounts.roles` is
  overwritten in place, so without the event there is no answer to "who made this account a
  reviewer, and when".

There is deliberately no `GET /v1/admin/accounts` listing. Nothing here needs one, and it would be
the single most valuable object in the system to anyone who obtained an admin session. Accounts
are addressed by id only — an operator acting on one already has it, from a support request or an
event.

## Configuration

See [`../.env.example`](../.env.example). Google remains fail-closed when its client ID is absent;
the other four values below are ones production refuses to start without or with the wrong value:

| Variable | Rule |
| --- | --- |
| `WEBSITE_BASE_URL` | Required, https. Where the sign-in link points — a link to a guessed origin is a credential sent somewhere nobody chose |
| `MAIL_SENDER` | Must be `smtp`. `console` writes sign-in links to the process log |
| `SMTP_HOST`, `SMTP_FROM_ADDRESS` | Required with SMTP. The provider host and verified sender address |
| `SMTP_PORT`, `SMTP_SECURITY` | Defaults to port 587 with `starttls`; `implicit-tls` supports port 465. Production refuses plaintext |
| `SMTP_USERNAME`, `SMTP_PASSWORD` | Must both be set or both omitted for a trusted network relay. The password is excluded from settings representations |
| `GOOGLE_CLIENT_ID` | Google OAuth web-client ID. Empty disables Google sign-in; accepted tokens must have this exact audience |
| `PUBLIC_CURSOR_SECRET` | Required, 32+ chars, and refused if it is the constant published in `settings.py` |
| `PUBLIC_ACTIVITY_SALT` | Same rules |

The CLI session knobs, none of which production requires but all of which it should think about:

| Variable | Default | Meaning |
| --- | --- | --- |
| `CLI_SESSION_DAYS` | 14 | The rolling window on a bearer token. Shorter than the browser's 30 |
| `CLI_SESSION_MAX_DAYS` | 90 | The ceiling rolling may not pass, from `issued_at`. Refused if below `CLI_SESSION_DAYS` |
| `CLI_SESSIONS_PER_ACCOUNT` | 10 | Live CLI tokens per account; the oldest is evicted at the ceiling |
| `LOGIN_CHALLENGE_ATTEMPTS` | 5 | Failed signatures before a challenge is spent |

TMC PAY is off unless all three of these are set, and a deployment that sets only some of them
**refuses to boot** — half-configured is the dangerous state, being the shape in which a purchase
page appears and a confirmation never does:

| Variable | Rule |
| --- | --- |
| `TMC_PAY_API_BASE_URL` | Absolute http(s); https in production. The published docs quote `api.example.com`, so there is nothing to guess |
| `TMC_PAY_API_KEY` | The merchant API key. Shown once at merchant creation; it can create invoices payable to this merchant account |
| `TMC_PAY_WEBHOOK_SECRET` | 16+ chars. The only thing standing between an unauthenticated endpoint and the credit ledger |
| `TAOSTATS_API_KEY` | Optional. The last external rate source, behind TMC PAY's own rate endpoint and TaoMarketCap's keyless candle feed; the ladder prefers TMC PAY's own locked rate over all three |
| `TMC_PAY_MERCHANT_ID` | Optional. When set, a webhook naming another merchant is ignored rather than matched on invoice id alone |

The rest have defaults chosen so that setting only the required values behaves correctly:
`TMC_PAY_QUOTE_MARGIN_BPS` (25), `TMC_PAY_QUOTE_ATTEMPTS` (2), `TMC_PAY_TTL_MINUTES` (30),
`TMC_PAY_MAX_OPEN_ORDERS` (3), `TMC_PAY_MAX_CREDITS` (1000), `TMC_PAY_POLL_SECONDS` (5),
`TMC_PAY_RATE_TTL_SECONDS` (300), `TMC_PAY_TIMEOUT_SECONDS` (10),
`TMC_PAY_CREDIT_LATE_PAYMENTS` (false), `TMC_PAY_FIAT_CURRENCY` (USD), `TMC_PAY_FIAT_DECIMALS` (2),
`TMC_PAY_HOSTED_BASE_URL` (unset).

## Tests

```bash
docker compose -f docker-compose.pytest-db.yml up -d
.venv/bin/pytest tests/test_api_accounts.py tests/test_api_auth.py tests/test_api_cli_sessions.py \
    tests/test_api_tmc_pay.py
```

Mostly about what must *not* work: a write a hostile page could have caused, a magic link used
twice, a signature replayed from the link flow into the sign-in flow, an account reading another
account's rows, one credit spent twice. The signatures are real sr25519 over the exact messages
the server minted.

`tests/test_origin_policy.py` needs no database. It is the truth table for the cross-site rule —
including the three-way `ALLOWED`/`REFUSED`/`UNPROVEN` result, which is the part that would be
easy to collapse into a boolean and thereby either break every non-browser client or reopen the
hole the rule closes.

`test_api_tmc_pay.py` is negative in the same spirit, because the processor path is the one whose
evidence is a signed message rather than chain state: an unsigned, wrongly-signed, tampered or
id-less webhook credits nothing; a *correctly* signed webhook claiming a hundred times the amount
still moves the ledger by exactly what the invoice locked; the same delivery applied twice credits
once; an invoice worth less than the credits it sells is refused rather than sold; and another
account can neither read nor poll somebody else's order. It also drives the reconciler through its
own `_pass`, so what cron runs is what is tested.

`test_api_cli_sessions.py` covers the boundary between the two credentials, and is mostly negative
too: a cookie token offered as a bearer and a bearer token planted in the cookie are both `401`; a
bearer request is never answered with `Set-Cookie`; a second challenge does not invalidate the
first; a hotkey-link signature is not a CLI login; a CLI token cannot link a hotkey, repoint the
payout, act as another of the account's hotkeys, or exercise `ADMIN`; and signing in to the website
leaves live CLI tokens alone while still retiring the previous browser session.
