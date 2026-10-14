# Historical bonus payments and private delivery

V048 adds manual bonus obligations, finalized transfer reconciliation, buyer agreements,
private downloads, export records and revocation/takedown records. These operations never
sign a transfer, send a message to a buyer, or publish a package. New administrative writes
require an ADMIN session and the normal same-origin write guard. Review remains a separate
REVIEWER capability. All routes below start with `/v1` and return `Cache-Control: no-store`.

## Bonus preparation and confirmation

Only accepted historical claims can receive the $500/$250 bonus. New-bounty evidence has no
separate bonus. An administrator cannot prepare their own claim. A current full-bounty proof
approval, unchanged claim state, and the contributor's current account payout destination are
required. The destination evidence records the operator's verification; no wallet signature
is demanded from hardware-wallet or exchange destinations.

POST `/admin/trajectories/{claim_id}/payments` with:

```json
{
  "expected_state_version": 2,
  "alpha_usd": "1.25",
  "price_source": "Taostats Subnet 66 quote, with the dated source/reference",
  "price_observed_at": "2026-10-13T20:00:00Z",
  "destination_coldkey": "<current account payout address>",
  "destination_evidence": "<reference to the destination verification>"
}
```

Use a real current price and reference, never this example. The quote must be at most ten
minutes old. Conversion uses exact decimal arithmetic and rounds up to an integer Alpha base
unit. Preparation reserves the amount from available treasury under the same transaction lock
used for proof bounty reservations. The immutable record includes USD cents, Alpha amount,
price/source/time, destination, treasury, operator and claim version. There can be only one
reserved or confirmed payment for a claim. Retrying preparation returns a conflict; inspect
the claim/payment record instead of creating another transfer.

GET `/admin/trajectory-payments/{payment_id}/instruction` immediately before signing externally.
It returns the transfer fields, `unsigned: true`, and `valid_until`. It refuses stale quotes,
changed account destinations, changed/revoked approvals, or completed instructions. Sign and
send only after the ordinary treasury authorization; this API never signs or submits funds.

After the existing payout watcher records the finalized transfer, POST
`/admin/trajectory-payments/{payment_id}/confirm` with `{"treasury_payout_id": 123}`. The exact
treasury keys, recipient, Subnet 66, integer Alpha amount and chronology must match. The same
chain observation cannot pay a proof reward or a second bonus. Confirmation is idempotent and
records chain reference, block and operator; it still records money actually sent if approval
changed after signing. Owners see PREPARED/CONFIRMED/CANCELLED in their private claim status.
GET `/admin/trajectory-payments/{payment_id}` returns the immutable instructions and outcome.
GET `/admin/trajectory-payments` lists the ledger (100 per page, `after`/`next_cursor`); the
private claim response also carries `payment_id` to recover an interrupted preparation.

For an unsigned stale instruction, POST `/admin/trajectory-payments/{payment_id}/cancel` with
`note` and `chain_evidence`. Evidence uses the existing payout-cancellation format:

```json
{
  "note": "<why this unsigned instruction is being cancelled>",
  "chain_evidence": {
    "checked_at": "<current ISO timestamp with timezone>",
    "network": "finney",
    "multisig_address": "<the recorded treasury coldkey>",
    "pending_multisig_count": 0,
    "no_matching_chain_transfer": true,
    "finalized_block_hash": "<verified finalized block hash>"
  }
}
```

The chain/multisig check must be within two minutes. A matching finalized observation blocks
cancellation. Cancellation releases the reservation but preserves its record; a fresh price
can then create a new obligation. Never cancel a signed or pending multisig transfer. The
database cannot recall an external transaction. Confirmed obligations remain reserved through
the existing balance-cache grace period so a stale chain balance cannot overstate funds.

## Private buyer delivery

Arrange the sale and signed private agreement separately. Record its exact SHA-256 and a
reference the buyer recognizes. The agreement must forbid open publication and require the
buyer to stop using/delete copies when acceptance is revoked.

POST `/admin/trajectories/{claim_id}/deliveries` with `buyer_id`, `agreement_reference`,
`agreement_sha256` and `agreement_prohibits_open_publication_and_requires_takedown: true`.
Only currently accepted packages with a current proof decision are grantable. The grant binds
the exact package digest and claim state; it cannot silently revive after later reapproval.

The buyer uses `/account/chat-log-deliveries` on the site, backed by:

- GET `/me/chat-log-deliveries`: only that account's grants, with the required assent.
- POST `/me/chat-log-deliveries/{id}/accept`: `agreement_sha256` and the exact returned `assent`.
- GET `/me/chat-log-deliveries/{id}/package`: an attachment, only after that buyer's assent.

Every download rechecks proof approval and claim state while holding the submission/claim
locks, and commits a append-only export record before returning bytes. No public object URL or
shared bearer link is generated. Attachment, sandbox CSP and nosniff headers keep package HTML
from being rendered as a site page.

Administrators inspect GET `/admin/trajectory-deliveries` (100 per page; pass `after` equal to
`next_cursor` until null). `takedown_required` identifies previously exported packages whose
authorization changed and whose takedown is not recorded. Review this queue daily and after a
proof correction; external notification is an operator task.

POST `/admin/trajectory-deliveries/{id}` with `action: "REVOKE"` and an explanatory `reference`
to permanently stop that grant. Notify the buyer through the agreed private channel and obtain
deletion confirmation. Then POST `action: "RECORD_TAKEDOWN"` with the reference to that
confirmation. Neither action erases exports or previous consent. A fresh grant requires a new
explicit agreement/acceptance after a valid reapproval. Confirmed payment records and outstanding takedowns keep their minimal claim evidence.
Cancelled instructions, unused grants and revoked, completed delivery records expire after their own
seven-year window and the claim's audit window. Non-accepted package bytes still expire
according to the contributor terms.

## Database roles

Apply V048 before the new API or payout watcher. Keep its SECURITY DEFINER functions under a
trusted non-superuser owner with SELECT on `trajectory_payments`. Grant EXECUTE on
`public.trajectory_bonus_transfer_reserved(text,text,text,bigint,timestamptz)` to the payout
watcher/reconciliation role and verify `has_function_privilege`. The boolean projection keeps
the watcher away from private claims and packages. Preserve the V046 owner/EXECUTE grants too.
The API needs ordinary access to the three fulfillment tables, accounts, and the existing
finalized payout ledger. No buyer receives database access.

The watcher also needs EXECUTE on `public.trajectory_bonus_oldest_pending(text,text)` under
the same trusted owner with SELECT on `trajectory_payments`. This opens chain tracking when a
historical bonus is the only outstanding obligation.
