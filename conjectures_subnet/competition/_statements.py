"""The queries both consumers run, built once.

The competition store has two callers with genuinely different concurrency models: the API
drives an async engine, and the gate worker's life is a forty-five-minute blocking
subprocess and wants a sync one. Neither is wrong, and converting either would be worse
than this module -- but writing each query twice would be, because the subtle ones would
drift apart without anything noticing.

So the *statements* live here and the two sides only differ in how they execute them:
`session.execute(...)` in `submissions.py` and `registrations.py`, `await
session.execute(...)` in `queries.py`. Nothing here touches a session, and nothing here is
async, which is what lets both use it.

The three worth reading twice are `available_slots`, which is the entitlement rule; the
leaderboard pair, which is the ranking the competition's rules promise; and
`insert_submission`, whose `ON CONFLICT DO NOTHING` is what makes a resubmission idempotent
rather than a second queue entry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import Insert, insert

from . import models
from .status import PENDING, SubmissionState

if TYPE_CHECKING:  # pragma: no cover - typing only
    import datetime as dt
    import uuid

PENDING_VALUES = [state.value for state in PENDING]


def queue_depth() -> Select[tuple[int]]:
    """How many submissions are queued or being verified, across all hotkeys."""
    return (
        select(func.count())
        .select_from(models.Submission)
        .where(models.Submission.state.in_(PENDING_VALUES))
    )


def pending_count(hotkey: str) -> Select[tuple[int]]:
    """How many of one hotkey's submissions are queued or being verified."""
    return (
        select(func.count())
        .select_from(models.Submission)
        .where(
            models.Submission.hotkey == hotkey,
            models.Submission.state.in_(PENDING_VALUES),
        )
    )


def available_slots(hotkey: str) -> Select[tuple[int]]:
    """Registrations this hotkey holds that no accepted submission has spent.

    The entitlement rule, as a query: one registration buys one accepted submission. The
    scarcity is real -- verifying a submission costs the validator the better part of an
    hour -- so this is what stops the gate being a free service.
    """
    claimed = select(models.EntitlementClaim.registration_id)
    return (
        select(func.count())
        .select_from(models.Registration)
        .where(
            models.Registration.ss58_hot == hotkey,
            models.Registration.id.not_in(claimed),
        )
    )


def is_registered(hotkey: str) -> Select[tuple[int]]:
    """Whether the subnet has ever carried this hotkey."""
    return (
        select(models.Registration.id)
        .where(models.Registration.ss58_hot == hotkey)
        .limit(1)
    )


def registration_for(hotkey: str, coldkey: str) -> Select[tuple[int]]:
    """A registration of this hotkey made by this coldkey.

    How a signed-in account is allowed to submit for a hotkey without signing with it: the
    account owns a `submission_coldkey`, and a registration records which coldkey registered
    which hotkey. A hit means the account's own coldkey put that hotkey on the subnet.

    The two facts live in different databases and there is no foreign key between them, so
    this is one half of a two-read check rather than a join. That is sound here because both
    halves are immutable history: a registration row is never rewritten, and an account's
    coldkey change cannot retroactively un-register anything.
    """
    return (
        select(models.Registration.id)
        .where(
            models.Registration.ss58_hot == hotkey,
            models.Registration.ss58_cold == coldkey,
        )
        .limit(1)
    )


def leaderboard() -> Select[tuple[models.Submission]]:
    """Each hotkey's best accepted submission, one row per hotkey.

    DISTINCT ON picks the best row per hotkey, and its leading ORDER BY term has to be the
    distinct key -- which is why this does not come back in rank order and why
    `order_leaderboard` exists. Splitting it that way rather than hiding a sort inside a
    repository is what lets both callers rank identically.
    """
    return (
        select(models.Submission)
        .distinct(models.Submission.hotkey)
        .where(
            models.Submission.state == SubmissionState.ACCEPTED.value,
            models.Submission.bytes.is_not(None),
        )
        .order_by(
            models.Submission.hotkey,
            models.Submission.bytes,
            models.Submission.submitted_at,
            models.Submission.id,
        )
    )


def order_leaderboard(rows: list[models.Submission]) -> list[models.Submission]:
    """Rank the leaderboard: fewest bytes first, ties to the earlier submission.

    Ties break on `submitted_at` then `id` because the rules promise the earlier
    submission wins a tie, and `id` settles the case where two land in the same instant.
    """
    return sorted(rows, key=lambda row: (row.bytes, row.submitted_at, row.id))


def latest_incumbent_bytes() -> Select[tuple[int | None]]:
    """The incumbent's total as the most recent report measured it.

    Newest rather than smallest: the incumbent moves when an operator promotes one, so the
    latest measurement is the current truth even when an older row recorded fewer bytes.
    """
    return (
        select(models.Submission.incumbent_bytes)
        .where(models.Submission.incumbent_bytes.is_not(None))
        .order_by(models.Submission.id.desc())
        .limit(1)
    )


def submission_by_id(sub_id: int) -> Select[tuple[models.Submission]]:
    return select(models.Submission).where(models.Submission.id == sub_id)


def submission_id_by_digest(hotkey: str, digest: str) -> Select[tuple[int]]:
    """The existing submission for these exact files from this hotkey.

    The other half of `insert_submission`'s idempotency: when the insert conflicts it
    returns nothing, and this is how the caller recovers the id it already has.
    """
    return select(models.Submission.id).where(
        models.Submission.hotkey == hotkey, models.Submission.digest == digest
    )


def submission_by_digest(hotkey: str, digest: str) -> Select[tuple[models.Submission]]:
    """The whole existing submission for these files from this hotkey."""
    return select(models.Submission).where(
        models.Submission.hotkey == hotkey, models.Submission.digest == digest
    )


def submissions_for_account(account_id: uuid.UUID) -> Select[tuple[models.Submission]]:
    """One account's submissions, newest first.

    `account_id` is a plain column with no foreign key, because accounts live in the other
    database. Nothing here can verify it; the API is what sets it and what is trusted for it.
    """
    return (
        select(models.Submission)
        .where(models.Submission.account_id == account_id)
        .order_by(models.Submission.submitted_at.desc(), models.Submission.id.desc())
    )


def touch_rate_window(subject: str, start: dt.datetime) -> Insert:
    """Count one attempt against `subject` in the window beginning at `start`.

    One statement, not a read then a write: two concurrent requests must not both read the
    same count and both conclude they are under the limit. `RETURNING` hands back the new
    total so the caller can decide, having already counted -- a caller hammering the
    endpoint stays refused for the rest of the window rather than being let through the
    moment they stop being counted.
    """
    return (
        insert(models.RateLimitWindow)
        .values(subject=subject, window_start=start, hits=1)
        .on_conflict_do_update(
            index_elements=["subject", "window_start"],
            set_={"hits": models.RateLimitWindow.hits + 1},
        )
        .returning(models.RateLimitWindow.hits)
    )


def insert_submission(
    *,
    hotkey: str,
    digest: str,
    submitted_at: dt.datetime,
    parse_source: bytes,
    proof_source: bytes,
    account_id: uuid.UUID | None = None,
) -> Insert:
    """Queue a submission, or do nothing if these exact files are already queued.

    `ON CONFLICT DO NOTHING` on (hotkey, digest) is what makes a resubmission idempotent:
    the same two files from the same hotkey are the same submission, so a miner retrying a
    dropped response gets their original id back rather than a second place in the queue.
    An empty `returning` is how the caller tells the two apart.
    """
    return (
        insert(models.Submission)
        .values(
            hotkey=hotkey,
            digest=digest,
            submitted_at=submitted_at,
            state=SubmissionState.QUEUED.value,
            parse_source=parse_source,
            proof_source=proof_source,
            account_id=account_id,
        )
        .on_conflict_do_nothing(index_elements=["hotkey", "digest"])
        .returning(models.Submission.id)
    )
