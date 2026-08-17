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

from conjectures_subnet.db import accounts as account_store
from conjectures_subnet.db import digests
from conjectures_subnet.db.models import (
    Account,
    ReviewDecision,
    ReviewerKind,
    RewardEvent,
)
from submission_api import schemas_account as schemas
from submission_api.pagination import decode_cursor, encode_cursor
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
    identities = [] if redacted else await account_store.identities_for(session, account.id)
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


def session_view(row, *, current_id) -> schemas.SessionView:
    """One `account_sessions` row as its owner sees it.

    Field by field on purpose. The row carries `token_sha256` and `csrf_sha256`, and a
    serialiser that walked the columns would publish digests of live credentials the first time
    someone added a field to the table.
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
    return schemas.SubmissionSummary(
        id=submission.id,
        hotkey=submission.hotkey,
        task_id=submission.task_id,
        proof_sha256=digests.to_prefixed(submission.proof_digest),
        verification_status=str(submission.verification_status),
        manual_review_status=str(submission.manual_review_status),
        reward_status=str(submission.reward_status),
        failure_reason=submission.failure_reason,
        bounty_amount_rao=submission.bounty_amount_rao,
        bounty_policy_version=submission.bounty_policy_version,
        bounty_locked=False,
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
    return schemas.SubmissionDetail(
        id=submission.id,
        hotkey=submission.hotkey,
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
        bounty_locked=False,
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
    statement = (
        select(RewardEvent)
        .where(RewardEvent.submission_id == submission_id)
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
    "session_view",
    "submission_detail",
    "submission_summary",
    "utc",
    "verification_summary",
]
