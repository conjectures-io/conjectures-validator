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
    """

    hotkey: str
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


class SessionEnvelope(Model):
    """What a successful sign-in returns. The credential itself is in a cookie."""

    account: Account


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
    "Account",
    "ApprovalReason",
    "ConfirmedSubmission",
    "CreditBalance",
    "CreditLedgerEntry",
    "CreditPackage",
    "CreditPricing",
    "CursorPage",
    "Deposit",
    "DisqualificationReason",
    "FundingSummary",
    "IntentBundleResult",
    "LinkedHotkey",
    "LinkedWallet",
    "Model",
    "OwnerVerificationReport",
    "PayoutDestination",
    "PreflightResult",
    "PublicCredit",
    "ReviewDecisionView",
    "RewardItem",
    "RewardSummary",
    "SessionEnvelope",
    "SubmissionDetail",
    "SubmissionEvent",
    "SubmissionIntent",
    "SubmissionSummary",
    "SubmissionTerms",
    "VerificationSummary",
    "WalletChallenge",
]
