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

The three worth reading twice are `available_slots`, which is the entitlement rule;
`leaderboard`, which is the ranking the competition's rules promise; and
`insert_submission`, whose `ON CONFLICT DO NOTHING` is what makes a resubmission idempotent
rather than a second queue entry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import Select, Update, func, select, tuple_, update
from sqlalchemy.dialects.postgresql import Insert, insert
from sqlalchemy.orm import aliased

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


def _best_per_hotkey():
    """Each hotkey's best accepted submission, as a subquery -- one row per competitor.

    DISTINCT ON picks the best row per hotkey, and Postgres requires its leading ORDER BY
    term to be the distinct key. That is why this is a subquery rather than the whole
    ranking: the rank order (`bytes`, then the earlier submission) can only be applied
    outside it.
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
        .subquery()
    )


def leaderboard(
    *,
    after: tuple[int, dt.datetime, int] | None = None,
    limit: int | None = None,
) -> Select[tuple[models.Submission]]:
    """The standings: fewest bytes first, ties to the earlier submission.

    The whole ranking, in SQL, including the sort -- which it did not used to be. The sort
    lived in a Python helper beside this statement, and that was fine while one endpoint
    read the whole board; it stopped being fine the moment the board had to be paged,
    because a keyset predicate has to be expressed in the same order the rows come back in.
    Sorting in Python and slicing in SQL would have been two orderings that agree until one
    of them is edited.

    Ties break on `submitted_at` then `id` because the rules promise the earlier submission
    wins a tie, and `id` settles the case where two land in the same instant. The keyset
    cursor is that same triple, which is what makes paging stable while submissions arrive:
    a new accept inserts itself at its rank and never shifts a page already served.

    `limit=None` is the whole board, for the callers that want it in one read.
    """
    best = _best_per_hotkey()
    entry = aliased(models.Submission, best)
    statement = select(entry).order_by(entry.bytes, entry.submitted_at, entry.id)
    if after is not None:
        statement = statement.where(
            tuple_(entry.bytes, entry.submitted_at, entry.id) > tuple_(*after)
        )
    if limit is not None:
        statement = statement.limit(limit)
    return statement


def leaderboard_position(point: tuple[int, dt.datetime, int]) -> Select[tuple[int]]:
    """How many leaderboard entries rank at or before `point`.

    Two callers, one question. Given a page cursor it is the number of rows already served,
    so a page can report absolute ranks without counting them client-side; given a row's own
    key it is that row's rank. Both are "count the entries that come first", and the `<=`
    is what makes the second one 1-based.

    A count over the same subquery the ranking itself is built from, rather than a second
    description of who is on the board -- the filter that decides which submissions rank has
    to be one thing, or a rank and the row at that rank eventually disagree.
    """
    best = _best_per_hotkey()
    return (
        select(func.count())
        .select_from(best)
        .where(tuple_(best.c.bytes, best.c.submitted_at, best.c.id) <= tuple_(*point))
    )


def best_for_hotkey(hotkey: str) -> Select[tuple[models.Submission]]:
    """One hotkey's best accepted submission: their row on the leaderboard.

    Ordered exactly as `leaderboard` orders within a hotkey, which is what makes this the
    same row the board would show them.
    """
    return (
        select(models.Submission)
        .where(
            models.Submission.hotkey == hotkey,
            models.Submission.state == SubmissionState.ACCEPTED.value,
            models.Submission.bytes.is_not(None),
        )
        .order_by(
            models.Submission.bytes,
            models.Submission.submitted_at,
            models.Submission.id,
        )
        .limit(1)
    )


def competitor_count() -> Select[tuple[int]]:
    """How many distinct hotkeys hold an accepted, scored submission."""
    return select(func.count(func.distinct(models.Submission.hotkey))).where(
        models.Submission.state == SubmissionState.ACCEPTED.value,
        models.Submission.bytes.is_not(None),
    )


def state_counts(hotkey: str | None = None) -> Select[tuple[str, int]]:
    """(state, count) for every state present, across all hotkeys or for one.

    Absent states are absent, not zero -- the caller fills those in, because the set of
    states is a code constant and a query cannot invent a row for one nobody has reached yet.
    """
    statement = select(models.Submission.state, func.count())
    if hotkey is not None:
        statement = statement.where(models.Submission.hotkey == hotkey)
    return statement.group_by(models.Submission.state)


def best_bytes() -> Select[tuple[int | None]]:
    """The fewest bytes any accepted submission has reached."""
    return select(func.min(models.Submission.bytes)).where(
        models.Submission.state == SubmissionState.ACCEPTED.value
    )


def last_accepted_at() -> Select[tuple[dt.datetime | None]]:
    """When the board last moved. None before the first accept."""
    return select(func.max(models.Submission.submitted_at)).where(
        models.Submission.state == SubmissionState.ACCEPTED.value
    )


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


def submissions_for_account(
    account_id: uuid.UUID,
    *,
    after: tuple[dt.datetime, int] | None = None,
    limit: int | None = None,
) -> Select[tuple[models.Submission]]:
    """One account's submissions, newest first.

    `account_id` is a plain column with no foreign key, because accounts live in the other
    database. Nothing here can verify it; the API is what sets it and what is trusted for it.
    """
    return _by_arrival(
        select(models.Submission).where(models.Submission.account_id == account_id),
        after=after,
        limit=limit,
    )


def _by_arrival(
    statement: Select[Any],
    *,
    after: tuple[dt.datetime, int] | None,
    limit: int | None,
) -> Select[Any]:
    """Newest first, with the keyset predicate that pages it.

    `(submitted_at, id)` rather than `submitted_at` alone: two submissions can share a
    timestamp, and a cursor on the timestamp alone would either repeat them or skip them.
    Descending, so `<` is what "the page after this one" means here.
    """
    statement = statement.order_by(
        models.Submission.submitted_at.desc(), models.Submission.id.desc()
    )
    if after is not None:
        statement = statement.where(
            tuple_(models.Submission.submitted_at, models.Submission.id) < tuple_(*after)
        )
    if limit is not None:
        statement = statement.limit(limit)
    return statement


def submissions_page(
    *,
    after: tuple[dt.datetime, int] | None = None,
    limit: int | None = None,
    state: str | None = None,
    hotkey: str | None = None,
) -> Select[tuple[models.Submission]]:
    """Every submission, newest first, optionally narrowed to one state or one hotkey.

    Unfiltered by default, like the platform's own `/v1/results/submissions`: a feed that
    dropped the rejections would show a reader only the successes and read as the complete
    history. What the competition publishes is which attempts were made and how they went,
    which is the record miners optimise against.

    `hotkey` is the filter that matters operationally: a submission id is the only handle a
    miner gets back, and before this there was no way to find one they had lost.
    """
    statement = select(models.Submission)
    if state is not None:
        statement = statement.where(models.Submission.state == state)
    if hotkey is not None:
        statement = statement.where(models.Submission.hotkey == hotkey)
    return _by_arrival(statement, after=after, limit=limit)


def stuck_submissions(
    *,
    claimed_before: dt.datetime,
    after: tuple[dt.datetime, int] | None = None,
    limit: int | None = None,
) -> Select[tuple[models.Submission, bool]]:
    """What an operator is being asked to look at: errors, and claims that never came back.

    Two populations in one feed because they are one question. `error` is the state the
    worker leaves behind when it gives up on a submission after `max_attempts` -- and the
    report it writes says in as many words that "an operator has been asked to look", which
    until now there was no way to do short of `psql`. A row still `verifying` with a claim
    older than the gate's own timeout is the other half: a worker that died mid-gate, whose
    submission the sweep should have requeued and did not.

    `claimed_before` is passed in rather than computed here, because how long is too long is
    the worker's `VERIFY_TOTAL_TIMEOUT` and this module does not read the worker's settings.
    """
    return _by_arrival(
        select(models.Submission, _has_sources()).where(
            (models.Submission.state == SubmissionState.ERROR.value)
            | (
                (models.Submission.state == SubmissionState.VERIFYING.value)
                & (models.Submission.claimed_at < claimed_before)
            )
        ),
        after=after,
        limit=limit,
    )


def _has_sources():
    """Whether the row still carries its two files, as a boolean the query computes.

    Not `row.parse_source is not None` in the handler: the columns are deferred on the
    model, so reading them there would either fetch a megabyte per row or -- on the async
    engine, which is what serves this -- raise `MissingGreenlet`. Postgres answers the
    question without sending the answer's subject.

    One column is enough for both: `ck_submissions_sources_paired` makes them agree.
    """
    return models.Submission.parse_source.is_not(None).label("has_sources")


def submission_for_operator(sub_id: int) -> Select[tuple[models.Submission, bool]]:
    """One submission and whether its files are still on the row."""
    return select(models.Submission, _has_sources()).where(
        models.Submission.id == sub_id
    )


def requeue_submission(sub_id: int) -> Update:
    """Put one submission back at the front of the queue, as if never claimed.

    Only from a terminal `error` or a stale `verifying` -- never from `accepted` or
    `rejected`. An accept has spent a registration and a reject is a verdict on the miner's
    files; re-running either would either double-spend a slot or overwrite a verdict the
    miner has already been shown. The `state` predicate is the guard, so a concurrent
    worker finishing a claim between the read and this write loses the race cleanly and
    updates nothing.

    `attempts` resets to zero: the operator has looked, which is the event the cap exists to
    force. Leaving it would put the submission straight back over the cap on its next claim.
    """
    return (
        update(models.Submission)
        .where(
            models.Submission.id == sub_id,
            models.Submission.state.in_(
                [SubmissionState.ERROR.value, SubmissionState.VERIFYING.value]
            ),
        )
        .values(
            state=SubmissionState.QUEUED.value,
            worker_id=None,
            claimed_at=None,
            finished_at=None,
            exit_code=None,
            attempts=0,
        )
        .returning(models.Submission.id)
    )


def submission_sources(sub_id: int) -> Select[tuple[bytes | None, bytes | None]]:
    """The two files as uploaded, for one submission.

    Its own statement rather than a read of the whole row, because these are up to a
    megabyte together and every other read of a submission would otherwise carry them.
    """
    return select(
        models.Submission.parse_source, models.Submission.proof_source
    ).where(models.Submission.id == sub_id)


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
