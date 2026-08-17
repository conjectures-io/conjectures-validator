"""Shared serialisation for the signed-in surface. No routes.

Underscore-prefixed because it holds none: it is the one place an `Account` or a `Submission`
becomes a response model, so `/v1/auth/session`, `/v1/me` and the intent confirmation cannot
drift into returning three different shapes for the same row.

Also the shared cursor helpers. The public feeds key on `(created_at, id)` because `created_at`
is not unique there; the ledger and reward feeds key on a single monotonic identity column, so
they reuse the same signed-cursor format with the id in it rather than introducing a second
cursor encoding.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from conjectures_subnet.attribution import public_credit_from_values
from conjectures_subnet.db import accounts as account_store
from conjectures_subnet.db import credits as credit_store
from conjectures_subnet.db import digests
from conjectures_subnet.db import public as public_store
from conjectures_subnet.db import submissions as submission_store
from conjectures_subnet.db.intents import REASON_INSUFFICIENT_CREDITS
from conjectures_subnet.db.models import (
    ADMIN_ROLE,
    REVIEWER_ROLE,
    Account,
    PayoutState,
    ReviewDecision,
    ReviewerKind,
    RewardEvent,
)
from submission_api import schemas_account as schemas
from submission_api.dependencies import (
    BEARER_ROLES,
    REASON_BROWSER_SESSION_REQUIRED,
    REASON_ROLE_NEEDS_BROWSER,
    REASON_ROLE_REQUIRED,
)
from submission_api.login import REASON_HOTKEY_NOT_LINKED
from submission_api.pagination import decode_cursor, encode_cursor
from submission_api.routers.submissions import REASON_SUBMISSIONS_PAUSED
from submission_api.settings import Settings


def _utc(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo else value.replace(tzinfo=dt.UTC)


def utc(value: dt.datetime | None) -> dt.datetime | None:
    return None if value is None else _utc(value)


# --- Account -----------------------------------------------------------------------------


async def account_response(
    session: AsyncSession, account: Account, *, bearer_scope: str | None = None
) -> schemas.Account:
    """The account, with its linked keys, as the credential in hand is entitled to see it.

    Served only to the account's owner. Every field here — the email, the payout keys, the
    hotkeys — would be a disclosure on the public surface, which is why those models live in a
    different module with the opposite rule.

    **`bearer_scope` redacts it for a CLI session**, and the asymmetry is deliberate. A browser
    session is opened by a coldkey signature or by proving control of a mailbox; a CLI session is
    opened by a hotkey, which Bittensor stores unencrypted on disk by design. The two are not
    equally strong evidence of "this is the account holder", so they do not see the same thing.
    What a bearer session gets back is what it needs to operate — who it is, what it may act as,
    and how much it can spend — and not:

      * `email`, which is the recovery channel for the whole account and, on a shared or
        compromised mining box, the first thing worth stealing;
      * `payout`, which names where the money goes;
      * `wallets`, which names the coldkey that can sign in;
      * `identities`, which names the Google account that can sign in — the same recovery-channel
        argument as `email`, and for a provider that needs no mailbox access to use;
      * the *other* linked hotkeys, which map out the rest of the operation.

    `roles` and `email_verified` stay: neither is a secret, both are facts about capability
    rather than about a person, and the CLI needs the former to explain a role refusal.

    A redacted response is still an honest one — the fields are absent or empty, never wrong.
    `hotkeys` narrows to the single key in scope, which is a true statement about what this
    token may do, and `email_verified` is reported as it actually is.
    """
    redacted = bearer_scope is not None
    hotkeys = await account_store.hotkeys_for(session, account.id)
    if redacted:
        hotkeys = [item for item in hotkeys if item.hotkey == bearer_scope]
    wallets = [] if redacted else await account_store.wallets_for(session, account.id)
    # Withheld from a bearer session on the same rule as `email` and `wallets`: a linked Google
    # account is a way back *into* this account, so disclosing it to a token minted by a hotkey
    # on a mining box would hand an attacker the next door to try.
    identities = (
        [] if redacted else await account_store.identities_for(session, account.id)
    )
    payout = None
    if not redacted and account.payout_coldkey and account.payout_hotkey:
        payout = schemas.PayoutDestination(
            coldkey=account.payout_coldkey, hotkey=account.payout_hotkey
        )
    return schemas.Account(
        id=account.id,
        email=None if redacted else account.email,
        email_verified=account.email_verified,
        display_name=account.display_name,
        roles=tuple(account.roles or ()),
        payout=payout,
        hotkeys=tuple(
            schemas.LinkedHotkey(hotkey=item.hotkey, linked_at=_utc(item.linked_at))
            for item in hotkeys
        ),
        wallets=tuple(
            schemas.LinkedWallet(coldkey=item.coldkey, linked_at=_utc(item.linked_at))
            for item in wallets
        ),
        identities=tuple(
            schemas.LinkedIdentity(
                provider=item.provider,
                email=item.email,
                linked_at=_utc(item.linked_at),
                last_used_at=_utc(item.last_used_at),
            )
            for item in identities
        ),
        created_at=_utc(account.created_at),
    )


# --- The session envelope ----------------------------------------------------------------
# What `GET /v1/auth/session` and both sign-in endpoints answer with: the account, plus the
# four things a signed-in shell needs before it can draw anything — how the person got in, what
# they hold, how much work is waiting, and which buttons are live.
#
# Assembled here rather than in the router for the same reason `account_response` is: it is
# served by three handlers, and three handlers building it separately is three chances for the
# redaction rules below to be applied twice and forgotten once.


def _identities(account: schemas.Account) -> tuple[schemas.Identity, ...]:
    """The ways in, flattened out of the account that was just built.

    Derived from the `schemas.Account` rather than re-read from the database, which is what
    makes the two halves of the envelope incapable of disagreeing — and means a CLI session's
    redaction is inherited rather than re-implemented: `account_response` has already dropped
    the email and the wallets, so this yields an empty list for a bearer caller without knowing
    that it is one.

    **Only verified email counts as an identity.** An address that has not been proved is not a
    way in, and listing it as one would tell someone they have a recovery channel they do not.

    **`linked_at` on the email row is the account's creation time, and that is a lower bound
    rather than an exact answer.** It is exact for a magic-link signup, where setting the address
    *is* what creates the account. It is early when a coldkey-first account later linked Google
    and that link adopted the provider's address — `accounts.email` has no companion timestamp,
    so there is nothing more accurate to report. The fix is a column recording when the address
    was attached, and it belongs with whatever flow first lets an address be attached on its own.
    The Google row beside it carries its own true `linked_at`, so the honest reading of the pair
    is "this address has worked since at least here".

    Ordered email, then external providers, then coldkeys — the order an account acquires them in
    the common case, and the order the account page lists them.
    """
    identities: list[schemas.Identity] = []
    if account.email and account.email_verified:
        identities.append(
            schemas.Identity(
                provider=schemas.PROVIDER_EMAIL,
                label=account.email,
                linked_at=account.created_at,
            )
        )
    # `provider` is passed through rather than mapped to a constant: the column is CHECK-limited
    # to 'google' today, and a second provider should appear here the moment the database accepts
    # one, not on whatever later day someone remembers to extend a mapping in this file.
    identities.extend(
        schemas.Identity(
            provider=item.provider,
            label=item.email,
            linked_at=item.linked_at,
        )
        for item in account.identities
    )
    identities.extend(
        schemas.Identity(
            provider=schemas.PROVIDER_COLDKEY,
            label=wallet.coldkey,
            linked_at=wallet.linked_at,
        )
        for wallet in account.wallets
    )
    return tuple(identities)


def _capabilities(
    account: schemas.Account,
    *,
    settings: Settings,
    credits_available: int,
    is_bearer: bool,
) -> schemas.Capabilities:
    """Evaluate the five gated actions against exactly the rules their endpoints enforce.

    Each list below is the same sequence of checks the handler makes, in the same order, so the
    first entry in `missing` is the refusal the caller would actually have received. Where the
    endpoint reuses a shared dependency the code is imported from it rather than retyped —
    a capability that says `ROLE_REQUIRED` while the router refuses with something else is worse
    than no capability at all, because a client will have built its copy around the wrong word.

    This is advisory. Nothing here authorises anything; the endpoint checks again.
    """
    has_hotkey = bool(account.hotkeys)
    roles = set(account.roles)

    submit: list[str] = []
    if settings.submissions_paused:
        submit.append(REASON_SUBMISSIONS_PAUSED)
    if not has_hotkey:
        submit.append(REASON_HOTKEY_NOT_LINKED)
    if credits_available < 1:
        submit.append(REASON_INSUFFICIENT_CREDITS)

    # Both funding paths — the declared deposit and the TMC PAY invoice — are `CookieWriterDep`,
    # so the credential is the only gate. TMC PAY being unconfigured is deliberately *not* a
    # reason: the deposit path is always there, so credits are still buyable.
    buy_credits = [REASON_BROWSER_SESSION_REQUIRED] if is_bearer else []

    set_payout: list[str] = []
    if is_bearer:
        set_payout.append(REASON_BROWSER_SESSION_REQUIRED)
    if not has_hotkey:
        set_payout.append(REASON_HOTKEY_NOT_LINKED)

    return schemas.Capabilities(
        submit=schemas.Capability(allowed=not submit, missing=tuple(submit)),
        buy_credits=schemas.Capability(
            allowed=not buy_credits, missing=tuple(buy_credits)
        ),
        set_payout=schemas.Capability(
            allowed=not set_payout, missing=tuple(set_payout)
        ),
        review=_role_capability(REVIEWER_ROLE, roles=roles, is_bearer=is_bearer),
        manage_roles=_role_capability(ADMIN_ROLE, roles=roles, is_bearer=is_bearer),
    )


def _role_capability(
    role: str, *, roles: set[str], is_bearer: bool
) -> schemas.Capability:
    """The two-part role gate, in `require_role`'s order: the role, then the credential.

    Both codes can appear at once, and that is the useful case rather than an edge one: an
    admin on the CLI is told they hold the role *and* that this credential cannot exercise it,
    which is the distinction `require_role` exists to keep from collapsing into one message.
    """
    missing: list[str] = []
    if role not in roles:
        missing.append(REASON_ROLE_REQUIRED)
    if is_bearer and role not in BEARER_ROLES:
        missing.append(REASON_ROLE_NEEDS_BROWSER)
    return schemas.Capability(allowed=not missing, missing=tuple(missing))


async def session_envelope(
    session: AsyncSession,
    account: Account,
    *,
    settings: Settings,
    now: dt.datetime,
    bearer_scope: str | None = None,
) -> schemas.SessionEnvelope:
    """The complete signed-in state, redacted for the credential in hand.

    `bearer_scope` is passed straight through to `account_response`, and everything else is
    built from what that returns — so the redaction is decided in exactly one place. A CLI
    session therefore sees no email identity, no coldkey identity, no payout and no hotkey but
    its own, without this function branching on it.

    Two things are *not* inherited and are decided here:

    * **`credits`** stays. A CLI session spends credits — that is most of what it does — and
      `account_response` already reasons that "how much it can spend" is within a bearer
      token's business. Withholding it would mean the CLI could only discover an empty balance
      by being refused.
    * **`counts.review_queue`** is null unless the caller may actually open the queue. It is a
      number about *other people's* submissions, and while the depth is hardly a secret, a field
      that is populated for callers who cannot act on it invites a client to render a queue
      badge that leads to a 403. `capabilities.review` is the same predicate, evaluated once.

    Four queries beyond the account read, all indexed and all on the load path of every page:
    the balance, the two intent-held sums behind it, this account's counts, and — only for a
    reviewer — the shared queue depth.
    """
    body = await account_response(session, account, bearer_scope=bearer_scope)
    is_bearer = bearer_scope is not None

    balance = await credit_store.credit_balance(
        session,
        account.id,
        credit_price_rao=settings.payment_amount_rao,
        now=now,
    )
    counts = await submission_store.counts_for_account(session, account.id)
    capabilities = _capabilities(
        body,
        settings=settings,
        credits_available=balance.credits_available,
        is_bearer=is_bearer,
    )

    review_queue = None
    if capabilities.review.allowed:
        review_queue = (await public_store.queue_depths(session)).awaiting_review

    return schemas.SessionEnvelope(
        account=body,
        identities=_identities(body),
        hotkeys=body.hotkeys,
        payout=body.payout,
        credits=schemas.SessionCredits(
            balance=balance.credits_available,
            # Floored to whole credits, like the balance beside it. A hold is always a whole
            # credit — `open_intent` holds `credits_held` of them at the price in force — so
            # this division is exact rather than lossy, unlike the balance's.
            held=balance.held_rao // balance.credit_price_rao,
        ),
        counts=schemas.SessionCounts(
            submissions_total=counts.submissions_total,
            submissions_in_review=counts.submissions_in_review,
            rewards_unclaimed=counts.rewards_unclaimed,
            review_queue=review_queue,
        ),
        capabilities=capabilities,
    )


def session_view(row, *, current_id) -> schemas.SessionView:
    """One `account_sessions` row as its owner sees it.

    Field by field on purpose. The row carries `token_sha256`, and a serialiser that walked the
    columns would publish the digest of a live credential the first time someone added a field
    to the table.
    """
    return schemas.SessionView(
        id=row.id,
        kind=str(row.kind),
        current=row.id == current_id,
        hotkey_scope=row.hotkey_scope,
        issued_at=_utc(row.issued_at),
        last_seen_at=_utc(row.last_seen_at),
        expires_at=_utc(row.expires_at),
        user_agent=row.user_agent,
        source_ip=None if row.source_ip is None else str(row.source_ip),
    )


# --- Submissions -------------------------------------------------------------------------


def funding_summary(submission) -> schemas.FundingSummary:
    """Which of the two funding paths paid for this submission.

    Exactly one, guaranteed by `submission_funded_exactly_once` in the schema rather than by
    checking here — so this branch reads durable state instead of guessing.
    """
    if submission.payment_reference is not None:
        return schemas.FundingSummary(
            source="extrinsic",
            payment_reference=submission.payment_reference,
            payment_amount_rao=submission.payment_amount_rao,
            payment_block=submission.payment_block,
        )
    return schemas.FundingSummary(
        source="credit",
        credit_ledger_id=submission.credit_ledger_id,
        intent_id=submission.intent_id,
    )


def submission_summary(submission) -> schemas.SubmissionSummary:
    credit = public_credit_from_values(submission)
    return schemas.SubmissionSummary(
        id=submission.id,
        hotkey=submission.hotkey,
        public_credit=None if credit is None else credit.to_dict(),
        task_id=submission.task_id,
        proof_sha256=digests.to_prefixed(submission.proof_digest),
        verification_status=str(submission.verification_status),
        manual_review_status=str(submission.manual_review_status),
        reward_status=str(submission.reward_status),
        failure_reason=submission.failure_reason,
        bounty_amount_rao=submission.bounty_amount_rao,
        bounty_policy_version=submission.bounty_policy_version,
        bounty_locked=submission.bounty_locked_at is not None,
        created_at=_utc(submission.created_at),
        updated_at=_utc(submission.updated_at),
    )


def verification_summary(view) -> schemas.VerificationSummary:
    run = view.verification
    submission = view.submission
    if run is None:
        return schemas.VerificationSummary(status=str(submission.verification_status))
    return schemas.VerificationSummary(
        status=str(submission.verification_status),
        attempt=run.id,
        accepted=run.accepted,
        reason_code=run.reason_code,
        stage=run.stage,
        sandbox_mode=run.sandbox_mode,
        report_available=run.report is not None,
        started_at=utc(run.started_at),
        finished_at=utc(run.finished_at),
    )


async def submission_detail(session: AsyncSession, view) -> schemas.SubmissionDetail:
    submission = view.submission
    credit = public_credit_from_values(submission)
    return schemas.SubmissionDetail(
        id=submission.id,
        hotkey=submission.hotkey,
        public_credit=None if credit is None else credit.to_dict(),
        task_id=submission.task_id,
        task_bundle_sha256=digests.to_prefixed(submission.task_bundle_sha256),
        proof_sha256=digests.to_prefixed(submission.proof_digest),
        request_digest=digests.to_prefixed(submission.request_digest),
        verification_status=str(submission.verification_status),
        manual_review_status=str(submission.manual_review_status),
        reward_status=str(submission.reward_status),
        failure_reason=submission.failure_reason,
        manual_review_required=submission.manual_review_required,
        review_policy_version=submission.review_policy_version,
        bounty_amount_rao=submission.bounty_amount_rao,
        bounty_policy_version=submission.bounty_policy_version,
        bounty_locked=submission.bounty_locked_at is not None,
        funding=funding_summary(submission),
        verification=verification_summary(view),
        review=await latest_review(session, submission.id),
        reward=await latest_reward(session, submission.id),
        created_at=_utc(submission.created_at),
        updated_at=_utc(submission.updated_at),
    )


async def latest_review(
    session: AsyncSession, submission_id: uuid.UUID
) -> schemas.ReviewDecisionView | None:
    """The binding decision, and only the miner-visible part of it.

    ADVISORY decisions are excluded: an LLM pre-check is recorded as evidence and is never
    binding, so showing one to a miner would present a non-decision as one. The reviewer's free
    -text `notes` are not returned either; only the separately reviewed and redacted
    `notes_public` field may cross the API boundary.
    """
    statement = (
        select(ReviewDecision)
        .where(
            ReviewDecision.submission_id == submission_id,
            ReviewDecision.kind != ReviewerKind.ADVISORY,
        )
        .order_by(ReviewDecision.id.desc())
        .limit(1)
    )
    decision = (await session.execute(statement)).scalar_one_or_none()
    if decision is None:
        return None
    return schemas.ReviewDecisionView(
        decision=str(decision.decision),
        reason_code=decision.reason_code,
        notes_public=decision.notes_public,
        policy_version=decision.policy_version,
        decided_at=_utc(decision.created_at),
    )


async def latest_reward(
    session: AsyncSession, submission_id: uuid.UUID
) -> schemas.RewardSummary | None:
    """The latest payout state the chain has actually observed.

    ``PENDING`` reward events are internal obligations: the notifier has prepared a command, but
    no successful chain event exists yet.  Returning one made the website say "Paying" before a
    signer had submitted anything.  ``SUBMITTED`` is now written only from the best chain and
    ``CONFIRMED`` only from finalized events, so those are the first states exposed here.
    """
    statement = (
        select(RewardEvent)
        .where(
            RewardEvent.submission_id == submission_id,
            RewardEvent.chain_observed.is_(True),
            RewardEvent.status.in_((PayoutState.SUBMITTED, PayoutState.CONFIRMED)),
        )
        .order_by(RewardEvent.id.desc())
        .limit(1)
    )
    event = (await session.execute(statement)).scalar_one_or_none()
    if event is None:
        return None
    return schemas.RewardSummary(
        status=str(event.status),
        amount_rao=event.amount_rao,
        extrinsic_reference=event.extrinsic_reference,
        finalized_block=event.finalized_block,
        failure_reason=event.failure_reason,
        confirmed_at=utc(event.confirmed_at),
    )


# --- Cursors -----------------------------------------------------------------------------


def encode_id_cursor(settings: Settings, last_id: int) -> str:
    """A signed cursor over a single monotonic identity column.

    Signed for the same reason the public feed cursors are: the handler never parses an
    attacker-chosen value into a query predicate, and a tampered cursor is one clean 400. The
    id rides in the UUID half so there is one cursor format in the codebase, not two.
    """
    return encode_cursor(
        settings.cursor_secret,
        created_at=dt.datetime.fromtimestamp(0, tz=dt.UTC),
        id=uuid.UUID(int=last_id & ((1 << 128) - 1)),
    )


def decode_id_cursor(settings: Settings, cursor: str | None) -> int | None:
    if not cursor:
        return None
    return decode_cursor(settings.cursor_secret, cursor).id.int


def page_of(items: list[Any], *, limit: int) -> tuple[list[Any], bool]:
    """Split a `limit + 1` read into the page and whether another exists.

    Reading one extra row is what makes `next_cursor` null exactly at the end, rather than
    handing back a cursor that turns out to address an empty page.
    """
    return items[:limit], len(items) > limit


__all__ = [
    "account_response",
    "decode_id_cursor",
    "encode_id_cursor",
    "funding_summary",
    "latest_review",
    "latest_reward",
    "page_of",
    "session_envelope",
    "session_view",
    "submission_detail",
    "submission_summary",
    "utc",
    "verification_summary",
]
