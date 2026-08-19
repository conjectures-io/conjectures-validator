"""Response models for the signed-in surface.

The third of three model modules, and the split is the whole point:

* ``schemas.py`` — the miner-facing hotkey-signature surface.
* ``schemas_public.py`` — world-readable. It carries only identity explicitly signed for public
  credit, never account or payment identity.
* ``schemas_account.py`` — this file. Served **only** to the authenticated owner of the
  data, so it is the one place where a hotkey, a payout address, a payment reference or a
  balance is allowed to appear.

That inverted rule is why these models live apart. A field on ``Account`` would be a
serious leak on ``PublicResult``; keeping them in separate modules means the mistake has
to be made deliberately rather than by importing the convenient thing.

Every model here is still bounded: no proof bytes, and no verifier `stdout`/`stderr`
except on the owner's own report, which the miner-facing surface already returns to them.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


ItemT = TypeVar("ItemT")


class CursorPage(Model, Generic[ItemT]):
    """One page of a keyset-paginated feed. `next_cursor` is null at the end."""

    items: tuple[ItemT, ...]
    next_cursor: str | None = None


# --- Account -----------------------------------------------------------------------------


class LinkedHotkey(Model):
    """A hotkey this account proved control of.

    Also how a deposit is attributed: a transfer's sender is a coldkey, and that coldkey
    owning one of these is what ties the money to an account.

    `label` is always null today. It is here because the account page wants to show "rig-01"
    rather than a 48-character address, and shipping the field now means the page can be built
    against its final shape; populating it needs a nullable column on `linked_hotkeys` and an
    endpoint to set it, which is a separate change. Null is honest — there is no name — and a
    client should fall back to a truncated `hotkey`.
    """

    hotkey: str
    label: str | None = Field(
        default=None, description="A name the owner gave this key. Always null today."
    )
    linked_at: dt.datetime


class LinkedWallet(Model):
    coldkey: str
    linked_at: dt.datetime


class LinkedIdentity(Model):
    """An external sign-in method. The provider's stable subject is never exposed."""

    provider: str
    email: str
    linked_at: dt.datetime
    last_used_at: dt.datetime


# The ways an account can be reached. Not an enum on the wire: a client should render an
# unknown provider as "some other login" rather than fail to parse the session it is signed in
# with, and this list will grow.
PROVIDER_EMAIL = "email"
PROVIDER_GOOGLE = "google"
PROVIDER_COLDKEY = "coldkey"


class Identity(Model):
    """One way in to this account, as a sign-in page lists them.

    A flattened view of what is already on `Account`: the verified email address, every external
    provider in `identities`, and every linked coldkey. It exists because "how can I get back
    into this account" is one question, and answering it from three differently-shaped arrays is
    work every client would otherwise repeat.

    `label` is what to show: the address for `email` and `google`, the SS58 for `coldkey`. Full
    values, never truncated — where to elide is a layout decision, and a server that shortened
    them would make "is this the key I think it is" unanswerable.

    **The Google subject is deliberately not here.** `LinkedIdentity` withholds it and so does
    this; the label is the provider's email, which is what a person recognises. The subject is
    the login key and belongs in the database, not in a body a browser extension can read.

    **`coldkey` does not say which wallet signed.** Talisman, the tao.com wallet and `btcli`
    produce the same sr25519 signature over the same message, and nothing in the flow records
    which one produced it, so there is no honest field for it here.
    """

    provider: str = Field(description="email | google | coldkey")
    label: str
    linked_at: dt.datetime


class PayoutDestination(Model):
    """Where rewards go. Both keys or neither — alpha is held as stake."""

    coldkey: str
    hotkey: str


class Account(Model):
    id: uuid.UUID
    email: str | None = None
    email_verified: bool
    display_name: str | None = None
    roles: tuple[str, ...]
    payout: PayoutDestination | None = Field(
        default=None,
        description="Null until set. A reward cannot be paid without it.",
    )
    hotkeys: tuple[LinkedHotkey, ...]
    wallets: tuple[LinkedWallet, ...]
    identities: tuple[LinkedIdentity, ...]
    created_at: dt.datetime


class WalletChallenge(Model):
    """A nonce and the exact message to sign.

    `message` is served verbatim and verified verbatim — the server never rebuilds it,
    because rebuilding it differently is the bug that makes a signature meaningless.
    """

    nonce: str
    message: str
    expires_at: dt.datetime


class SessionCredits(Model):
    """The balance in whole credits, for a header badge.

    Deliberately not `CreditBalance`. That model is rao-denominated and is what a purchase page
    needs; this is the two numbers a "3 credits" chip needs, and mixing the units in one place
    is how a page ends up rendering 1500000000 as a credit count.

    The two do not overlap: `balance` is spendable *now*, already net of `held`, and `held` is
    what open submission intents have claimed. So the account's total is `balance + held`, and a
    client must not subtract one from the other again. `GET /v1/me/credits` remains the full
    picture, including the sub-credit remainder this rounds away.
    """

    balance: int = Field(description="Whole credits spendable now, after holds")
    held: int = Field(description="Whole credits claimed by open intents")


class SessionCounts(Model):
    """The badge numbers, so a shell can render its navigation in one request.

    Every one of these is also reachable by paginating the feed it summarises. They are here
    because a count is not a page: rendering "2 in review" by fetching submissions would either
    read one page and undercount, or read all of them to count.

    `rewards_unclaimed` is approved work whose payout has not confirmed. Nothing is *claimed* in
    this system — the payout worker pushes rewards on chain — so read it as "owed", not as an
    action waiting for the account holder.
    """

    submissions_total: int
    submissions_in_review: int
    rewards_unclaimed: int
    review_queue: int | None = Field(
        default=None,
        description="Depth of the shared review queue. Null unless this caller may review.",
    )


class Capability(Model):
    """Whether this session may do one thing, and what is missing if not.

    `missing` is the point of the model. A bare boolean makes a client either grey out a button
    with no explanation or re-derive the rules from `roles`, `hotkeys` and `credits` — and a
    client that re-derives them is a second copy of the authorisation logic that drifts from the
    server's the first time a rule changes.

    The codes are the same `reason_code` strings the corresponding endpoint refuses with, so
    "why is this greyed out" and "why did that 403" answer with the same word.

    **It is advice, never enforcement.** Every gate is checked again at the endpoint, against
    state that may have moved since this was read. A client that trusted `allowed: true` and
    skipped the error path would still be wrong the moment a credit is spent in another tab.
    """

    allowed: bool
    missing: tuple[str, ...] = ()


class Capabilities(Model):
    """What this session may do, evaluated once so the navigation does not have to guess.

    Five actions rather than a permission per endpoint: these are the five things the site
    offers as an affordance. Reads are omitted deliberately — a page the caller may not read
    answers 403 on its own, and listing every readable route here would make this a mirror of
    the router that has to be maintained alongside it.
    """

    submit: Capability
    buy_credits: Capability
    set_payout: Capability
    review: Capability
    manage_roles: Capability


class SessionEnvelope(Model):
    """The whole signed-in state: who, how they got in, what they hold, what they may do.

    Returned by `GET /v1/auth/session` and by both sign-in endpoints, identically populated, so
    a client that has just signed in does not have to immediately re-read the session to render
    its shell.

    `account` is unchanged and stays the canonical record — `identities`, `hotkeys` and `payout`
    are **derived from it in the same call**, never read separately, so the two halves of this
    body cannot disagree. They are flattened here because the account page groups by "ways in"
    and "keys I mine with", which is not how the account row is shaped.

    Everything here is redacted for a CLI bearer session, on the same rule as `Account`: see
    `routers/_account.session_envelope`.
    """

    account: Account
    identities: tuple[Identity, ...] = ()
    hotkeys: tuple[LinkedHotkey, ...] = ()
    payout: PayoutDestination | None = None
    credits: SessionCredits
    counts: SessionCounts
    capabilities: Capabilities


class CliSession(Model):
    """What a successful CLI sign-in returns. The credential is in the body, exactly once.

    Unlike the cookie flow, the token has to cross the API boundary — there is no other way to
    give it to a process that is not a browser. So this is the one response in the codebase that
    contains a live credential, and everything about how it is served follows from that: `POST`
    only, `Cache-Control: no-store`, and never a field on a telemetry event.

    `account` here is the **redacted** view. A bearer session is minted by a hotkey, and a
    hotkey sits unencrypted on a mining machine; handing it the account's email address, payout
    keys and every other linked hotkey would make reading one file the first step of a much
    larger compromise. See `routers/_account.account_response`.
    """

    access_token: str
    token_type: str = Field(description="Always `bearer`")
    expires_at: dt.datetime
    hotkey_scope: str = Field(description="The linked hotkey this token may act as")
    account: Account


class SessionView(Model):
    """One live session, as its owner sees it.

    Deliberately not derived from the row by a generic serialiser: `account_sessions` holds two
    digests, and a model that grew fields automatically would eventually publish one. Every
    field here is named because it is safe to name.

    `last_seen_at` is only advanced once per refresh interval — writing it on every request
    would mean a row lock per page load — so it is accurate to within that interval rather than
    to the second, and it is labelled as approximate for that reason.
    """

    id: uuid.UUID
    kind: str = Field(description="COOKIE (a browser) | BEARER (the CLI)")
    current: bool = Field(description="Whether this is the session making this request")
    hotkey_scope: str | None = Field(
        default=None, description="For a CLI session: the hotkey it may act as"
    )
    issued_at: dt.datetime
    last_seen_at: dt.datetime = Field(
        description="Approximate: advanced at most once per refresh interval"
    )
    expires_at: dt.datetime
    user_agent: str | None = None
    source_ip: str | None = None


# --- Credits -----------------------------------------------------------------------------


class CreditBalance(Model):
    """What the account can spend right now.

    `credits_available` is whole attempts, after subtracting what open intents have
    claimed. `remainder_rao` is the leftover that is not a whole credit, surfaced so a
    reader with nearly two credits' worth of rao does not conclude the rest vanished.
    """

    credits_available: int
    balance_rao: int
    held_rao: int = Field(description="Claimed by open submission intents, not yet spent")
    remainder_rao: int
    credit_price_rao: int
    low_balance: bool


class CreditLedgerEntry(Model):
    """One movement of the balance. The ledger is append-only.

    Amounts are signed integer rao: positive credits the account, negative debits it.
    `credit_price_rao` is the price that was in force, so a later reprice does not restate
    what a past attempt cost.
    """

    id: int
    kind: str = Field(description="DEPOSIT | SPEND | REFUND | ADJUSTMENT | BONUS")
    amount_rao: int
    credit_price_rao: int | None = None
    deposit_id: uuid.UUID | None = None
    submission_id: uuid.UUID | None = Field(
        default=None,
        description="For a SPEND: the submission the attempt produced, via its intent",
    )
    reason: str | None = None
    created_at: dt.datetime


class CreditPackage(Model):
    """A purchasable bundle. `bonus_credits` are granted, not paid for."""

    credits: int
    bonus_credits: int
    total_credits: int
    price_rao: int


class CreditPricing(Model):
    """What a credit costs and how to buy one. No auth: visible before signup.

    `price_usd` is null unless an operator pinned one. Converting TAO to USD needs a live
    external rate this validator does not have, and inventing one would put a number on a
    purchase page that nothing stands behind.
    """

    price_rao: int
    price_usd: str | None = None
    price_usd_asof: dt.date | None = None
    packages: tuple[CreditPackage, ...]
    methods: tuple[str, ...]
    recipient: str = Field(description="The treasury address a deposit is paid to")


class Deposit(Model):
    """A declared transfer and what has been observed for it.

    `status` distinguishes "we cannot see it yet" from "we can see it, we are waiting for
    finality" — `seen_unfinalized` exists so an account is not shown nothing while a
    transfer settles.

    `credited_rao` is the amount **observed on chain**, which is what is credited. It can
    differ from `amount_rao`, which is only what the account declared it would send.
    """

    id: uuid.UUID
    status: str = Field(
        description="AWAITING_TRANSFER | SEEN_UNFINALIZED | CREDITED | EXPIRED | FAILED"
    )
    amount_rao: int
    credited_rao: int | None = None
    treasury_address: str
    credit_price_rao: int
    credits_expected: int
    btcli_command: str = Field(description="Ready to copy; integer rao, rendered exactly")
    extrinsic_reference: str | None = None
    block: int | None = None
    failure_reason: str | None = None
    expires_at: dt.datetime
    created_at: dt.datetime
    updated_at: dt.datetime


class TmcPayOrder(Model):
    """A credit purchase paid through TMC PAY, and what has been observed for it.

    Read this as a payment instruction plus a status. Until `status` is `NEW` or `FAILED` the
    invoice exists and the three fields a buyer needs are populated: `deposit_address`,
    `amount_tao` and `expires_at`.

    `status` mirrors TMC PAY's own invoice lifecycle so that what this API reports and what the
    TMC PAY dashboard shows are the same word. Two of the values are this validator's:
    `NEW` — the invoice is being created — and `FAILED` — it could not be.

    `credits_expected` is what was bought. `credits_credited` is what the balance actually gained,
    which is at least that: the invoice is quoted in fiat and rounded up, so the locked TAO can
    exceed the credit price by a fraction of a percent, and the excess stays in the balance as
    `remainder_rao` rather than being discarded.

    `deposit_address` is TMC PAY's, derived per invoice — **not** the validator's treasury. A
    payment sent anywhere else does not fund this order.
    """

    id: uuid.UUID
    status: str = Field(
        description="NEW | FAILED | CREATED | PENDING | CONFIRMING | UNDERPAID | "
        "CONFIRMED | OVERPAID | CANCELLED | EXPIRED | LATE_PAYMENT"
    )
    credits_expected: int
    credit_price_rao: int
    credits_credited: int = Field(
        description="Credits this order has added to the balance; 0 until it is confirmed"
    )
    amount_rao: int | None = Field(
        default=None, description="The TAO the invoice locked, in integer rao"
    )
    amount_tao: str | None = Field(
        default=None, description="The same amount as a decimal string, for display"
    )
    deposit_address: str | None = Field(
        default=None, description="TMC PAY's per-invoice address; never the treasury"
    )
    btcli_command: str | None = Field(
        default=None, description="Ready to copy; integer rao, rendered exactly"
    )
    fiat_amount: str | None = None
    fiat_currency: str | None = None
    exchange_rate: str | None = Field(
        default=None, description="TAO per one fiat unit, locked at invoice creation"
    )
    commission_amount: str | None = Field(
        default=None, description="TMC PAY's fee, in fiat. Paid by the validator, not the buyer"
    )
    invoice_id: str | None = None
    payment_url: str | None = Field(
        default=None,
        description=(
            "TMC PAY's hosted payment page for this invoice, as TMC PAY reported it. Send the "
            "buyer here to pay. Absent until an invoice exists"
        ),
    )
    needs_review: bool = Field(
        description="An operator has to look at this: over- or underpaid, or paid late"
    )
    failure_reason: str | None = None
    expires_at: dt.datetime | None = None
    confirmed_at: dt.datetime | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class TmcPayPurchase(Model):
    """A new order, and the balance as it stands before anything is paid.

    The balance is returned alongside so a purchase page can show "you have 2 credits, this
    invoice adds 10" without a second request.
    """

    order: TmcPayOrder
    balance: CreditBalance


# --- Submission terms --------------------------------------------------------------------


class DisqualificationReason(Model):
    code: str
    description: str


class ApprovalReason(Model):
    code: str
    description: str


class SubmissionTerms(Model):
    """The terms, and the complete lists of reasons a review may decide.

    The lists are shared with the Stage 3 review page: a reviewer must use one published
    approval or disqualification code and cannot invent a reason.
    """

    version: str
    body_md: str
    effective_from: dt.date
    approval_reasons: tuple[ApprovalReason, ...]
    disqualification_reasons: tuple[DisqualificationReason, ...]


# --- Submission intents ------------------------------------------------------------------


class PublicCredit(Model):
    """Opt-in authorship frozen on the submission and later shown publicly."""

    name: str
    url: str | None = None
    orcid: str | None = None


class PreflightResult(Model):
    """A free static check, before a credit is spent.

    Runs the same bundle admission the paid path runs, so a bundle that passes here fails
    at intake only for a reason preflight cannot see — a task digest that moved, or a
    balance that ran out. `line` and `column` are set when the failure has a location in
    the submitted source.
    """

    ok: bool
    reason_code: str | None = None
    detail: str | None = None
    line: int | None = None
    column: int | None = None
    proof_sha256: str | None = None
    proof_bytes: int | None = None


class SubmissionIntent(Model):
    """A held credit, and once a bundle is attached, the digest to sign.

    `request_digest` is null until a bundle is uploaded. It is computed by the server from
    the bundle, so the client never chooses what it is signing.
    """

    id: uuid.UUID
    status: str = Field(
        description="OPEN | BUNDLE_ATTACHED | CONFIRMED | EXPIRED | CANCELLED"
    )
    hotkey: str
    public_credit: PublicCredit | None = None
    task_id: str
    task_bundle_sha256: str
    credits_held: int
    credit_price_rao: int
    proof_sha256: str | None = None
    request_digest: str | None = Field(
        default=None, description="Sign these 32 raw bytes with the intent's hotkey"
    )
    submission_id: uuid.UUID | None = None
    expires_at: dt.datetime
    created_at: dt.datetime


class IntentBundleResult(Model):
    """What the bundle upload returns: the digest to sign, and what was admitted."""

    intent: SubmissionIntent
    proof_sha256: str
    proof_bytes: int
    request_digest: str


class ConfirmedSubmission(Model):
    """The submission that was written, and the balance after the debit."""

    submission: "SubmissionDetail"
    credits: CreditBalance


# --- Miner panel -------------------------------------------------------------------------


class VerificationSummary(Model):
    status: str
    attempt: int | None = None
    accepted: bool | None = None
    reason_code: str | None = None
    stage: str | None = None
    sandbox_mode: str | None = None
    report_available: bool = False
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None


class FundingSummary(Model):
    """How this submission was paid for.

    Exactly one of the two shapes, which is a schema CHECK rather than a convention:
    `extrinsic` for the direct payment path, `credit` for the intent path.
    """

    source: str = Field(description="extrinsic | credit")
    payment_reference: str | None = None
    payment_amount_rao: int | None = None
    payment_block: int | None = None
    credit_ledger_id: int | None = None
    intent_id: uuid.UUID | None = None


class SubmissionSummary(Model):
    """One of the account's own submissions, as it appears in a list."""

    id: uuid.UUID
    hotkey: str
    public_credit: PublicCredit | None = None
    task_id: str
    proof_sha256: str
    verification_status: str
    manual_review_status: str
    reward_status: str
    failure_reason: str | None = None
    bounty_amount_rao: int = Field(
        description="The accepted quote; authoritative when bounty_locked is true."
    )
    bounty_policy_version: str
    bounty_locked: bool = Field(
        default=True,
        description="True for V012+ locks; false for grandfathered payout-time submissions.",
    )
    created_at: dt.datetime
    updated_at: dt.datetime


class ReviewDecisionView(Model):
    """The review outcome, as the miner is shown it.

    `notes_public` only. A reviewer's internal notes and the advisory evidence stay
    internal; `reason_code` is the machine-readable part, and it is always one of the
    published approval or disqualification codes.
    """

    decision: str
    reason_code: str
    notes_public: str | None = None
    policy_version: str
    decided_at: dt.datetime


class RewardSummary(Model):
    status: str = Field(description="PENDING | SUBMITTED | CONFIRMED | FAILED")
    amount_rao: int
    extrinsic_reference: str | None = None
    finalized_block: int | None = None
    failure_reason: str | None = None
    confirmed_at: dt.datetime | None = None


class SubmissionDetail(Model):
    """One of the account's own submissions, in full."""

    id: uuid.UUID
    hotkey: str
    public_credit: PublicCredit | None = None
    task_id: str
    task_bundle_sha256: str
    proof_sha256: str
    request_digest: str
    verification_status: str
    manual_review_status: str
    reward_status: str
    failure_reason: str | None = None
    manual_review_required: bool
    review_policy_version: str
    bounty_amount_rao: int = Field(
        description="The accepted quote; authoritative when bounty_locked is true."
    )
    bounty_policy_version: str
    bounty_locked: bool = Field(
        default=True,
        description="True for V012+ locks; false for grandfathered payout-time submissions.",
    )
    funding: FundingSummary
    verification: VerificationSummary | None = None
    review: ReviewDecisionView | None = None
    reward: RewardSummary | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class SubmissionEvent(Model):
    """One entry on the timeline: what happened, and when.

    This is what answers "what is happening to my submission in the meantime". The status
    fields say where it is now; these say how it got there.
    """

    id: int
    kind: str
    detail: str | None = None
    context: dict[str, Any] | None = None
    actor: str
    occurred_at: dt.datetime


class OwnerVerificationReport(Model):
    """The complete verifier report, for the submission's owner.

    Unlike `schemas_public.PublicVerificationReport`, nothing is withheld: `stdout_tail` and
    `stderr_tail` quote the owner's own proof back at them, which is what they need to fix it.
    `report` is passed through as the stored JSON rather than modelled field by field, because
    its shape is the verifier's `VerificationReport` and this module must not become a second
    place that has to be updated when the verifier adds a field.
    """

    submission_id: uuid.UUID
    report_sha256: str
    report: dict[str, Any]


class RewardItem(Model):
    """One payout, with the explorer link for the transfer."""

    id: int
    submission_id: uuid.UUID
    task_id: str
    status: str
    amount_rao: int
    destination_coldkey: str
    destination_hotkey: str
    extrinsic_reference: str | None = None
    explorer_url: str | None = None
    submitted_block: int | None = None
    finalized_block: int | None = None
    failure_reason: str | None = None
    created_at: dt.datetime
    confirmed_at: dt.datetime | None = None


ConfirmedSubmission.model_rebuild()


__all__ = [
    "PROVIDER_COLDKEY",
    "PROVIDER_EMAIL",
    "PROVIDER_GOOGLE",
    "Account",
    "ApprovalReason",
    "Capabilities",
    "Capability",
    "ConfirmedSubmission",
    "CreditBalance",
    "CreditLedgerEntry",
    "CreditPackage",
    "CreditPricing",
    "CursorPage",
    "Deposit",
    "DisqualificationReason",
    "FundingSummary",
    "Identity",
    "IntentBundleResult",
    "LinkedHotkey",
    "LinkedIdentity",
    "LinkedWallet",
    "Model",
    "OwnerVerificationReport",
    "PayoutDestination",
    "PreflightResult",
    "PublicCredit",
    "ReviewDecisionView",
    "RewardItem",
    "RewardSummary",
    "SessionCounts",
    "SessionCredits",
    "SessionEnvelope",
    "SubmissionDetail",
    "SubmissionEvent",
    "SubmissionIntent",
    "SubmissionSummary",
    "SubmissionTerms",
    "VerificationSummary",
    "WalletChallenge",
]
