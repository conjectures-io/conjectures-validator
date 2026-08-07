"""Read-only queries behind the public, unauthenticated endpoints.

Separate from ``submissions`` because the audience is different and the rules that
follow from that are worth enforcing in the query layer rather than trusting a
router to remember. Two invariants hold for everything in this module:

* **The submitting hotkey is published; the money is not.** ``ResultRow.hotkey`` names the
  solver, by product decision — a result is credited to the hotkey that produced it. Nothing
  here carries the paying coldkey, the payment reference or the extrinsic, and that boundary is
  the one still worth enforcing structurally: the hotkey is a public chain identity a miner
  signs with, whereas the coldkey and the payment reference lead to the funds behind it.
  ``activity`` still pseudonymises, but see the caveat on that function — publishing the hotkey
  on a result makes those pseudonyms correlatable by timing, so the two are no longer
  independent.
* **Nothing here can be made expensive.** Every feed is keyset-paginated over an
  index built for it — the two state-filtered feeds over partial indexes
  (``deploy/migrate/sql/V002__public_feeds.sql``), the unfiltered dashboard feed over a
  full one (``deploy/migrate/sql/V010__dashboard_feed_index.sql``) — the page size is
  bounded by the caller, and there is no total-count query: ``COUNT(*)`` over a
  growing table on every page read is a scan an anonymous caller should not be
  able to ask for.

The one thing this module does *not* keep behind a state filter is the dashboard feed:
``all_results_page`` lists every submission, whatever state it is in, and each row carries its
three state columns. ``certified_page`` and ``in_review_page`` are still one predicate each. The
disclosure rules the state filter used to carry incidentally are enforced where they belong
instead — ``accepted_solution`` gates proof bytes on review approval, ``public_report`` gates the
report on Lean verification, and ``ReviewRow`` carries only the explicitly public half of a review
decision.

A page is read in four statements rather than one join with three lateral
subqueries: the submissions for the page, then their latest verification runs,
confirmed payouts, and latest binding reviews, each by an indexed key over at most
``limit`` ids. It is not an N+1 — the count is fixed at four — and the intent
survives being read six months from now.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from conjectures_subnet.db.models import (
    ManualReviewState,
    PayoutState,
    Proof,
    ReviewDecision,
    ReviewerKind,
    ReviewOutcome,
    RewardEvent,
    RewardState,
    Submission,
    VerificationRun,
    VerificationState,
)

# A submission is on the public certified feed once it has been paid out. The review status is
# APPROVED in both paths that get there — a human approval, or the recorded AUTOMATIC decision
# when manual review is disabled — so requiring it costs nothing and states the intent.
CERTIFIED = (
    Submission.reward_status == RewardState.REWARDED,
    Submission.manual_review_status == ManualReviewState.APPROVED,
    Submission.verification_status == VerificationState.VERIFIED,
)

# Lean-verified and waiting on the reward decision.
IN_REVIEW = (
    Submission.verification_status == VerificationState.VERIFIED,
    Submission.manual_review_status == ManualReviewState.UNREVIEWED,
)

# Lean-verified and approved by review — the gate on publishing the proof itself.
#
# Deliberately not `CERTIFIED`: this omits `REWARDED`, so an approved submission whose payout has
# not yet confirmed still publishes its proof. The reason the artifact was withheld was that
# handing it out *before the reward decision* would let anyone take a pending result elsewhere;
# approval is that decision. Waiting for the payout as well would withhold the proof over a
# chain confirmation, which is a transfer's problem and not a disclosure question.
#
# APPROVED covers both paths that reach it — a human approval, and the recorded AUTOMATIC
# decision when manual review is disabled.
ACCEPTED = (
    Submission.verification_status == VerificationState.VERIFIED,
    Submission.manual_review_status == ManualReviewState.APPROVED,
)

# A result record and its allowlisted verifier report become public once Lean accepts the
# submission, whatever manual review later decides. Review approval remains the separate gate on
# proof bytes, and payout remains the stricter boundary for the certified feed.
PUBLISHED = (
    Submission.verification_status == VerificationState.VERIFIED,
)

MAX_ACTIVITY_ROWS = 500


@dataclass(frozen=True)
class ResultRow:
    """One publishable result.

    Every field here is either a property of the task, a property of the verdict, the money owed,
    or the solver who submitted it. The paying coldkey, the payment reference and the extrinsic
    are still absent by construction: a router cannot leak what it was never handed, and those
    three lead to a miner's funds rather than to their public signing identity.
    """

    id: uuid.UUID
    # The hotkey that submitted this proof. Published: a result is credited to its solver.
    hotkey: str
    # Where this submission has got to on each of the three axes the schema tracks. Carried on the
    # row because the dashboard feed is unfiltered: a client that reads a queued, rejected and
    # certified row from one feed needs the row itself to say which it is, and re-deriving that
    # from the nullable timestamps below cannot distinguish "rejected" from "not verified yet".
    verification_status: VerificationState
    manual_review_status: ManualReviewState
    reward_status: RewardState
    task_id: str
    # The conjecture this result is against, as an identity that outlives the pin it was produced
    # under. `task_id` names one build of one attack direction and changes on every rotation, so a
    # public link derived from it would break; `reward_target_id` is what the public slug is
    # derived from, and it is recorded on the row, so a result from any past rotation still links
    # to the current conjecture page.
    reward_target_id: str
    task_bundle_sha256: bytes
    created_at: datetime
    bounty_amount_rao: int
    bounty_policy_version: str
    review_policy_version: str
    # When the verifier finished with this submission, whatever it decided. Set on a rejected row
    # too: Lean did run and did reach a verdict, so the timestamp is the real one, and
    # `verification_status` says which verdict it was. Null only while no run has finished.
    verified_at: datetime | None
    certified_at: datetime | None
    verifier_version: str | None
    sandbox_mode: str | None
    # Whether a verifier report exists *and* may be served. The second half is why this is not
    # simply "a run recorded a report": the dashboard feed lists rows `public_report` refuses, and
    # advertising a report the fetch answers 404 for would send a client hunting for it.
    report_available: bool
    # The latest binding decision, reduced to its explicitly public fields. Internal reviewer
    # notes, reviewer identity, and advisory evidence never land on this public row.
    review: ReviewRow | None
    # Whether review has approved this submission, and therefore whether its proof is published.
    # Carried on the row so a feed can advertise the solution link without a second query per
    # item, and so the router never re-derives the disclosure gate from `certified_at` — the two
    # are not the same test, and an approved-but-unpaid row would be got wrong by that shortcut.
    solution_available: bool


@dataclass(frozen=True)
class ReviewRow:
    """The allowlisted, publishable portion of one binding review decision."""

    decision: ReviewOutcome
    reason_code: str
    notes_public: str | None
    policy_version: str
    decided_at: datetime


@dataclass(frozen=True)
class _RunFacts:
    """The columns of the latest verification run a public response may use.

    A plain record rather than a partially populated ``VerificationRun``: constructing a mapped
    instance from a subset of its columns produces a transient object that looks like a pending
    insert, and the report bytes are deliberately not loaded here.
    """

    verifier_version: str
    sandbox_mode: str
    finished_at: datetime
    has_report: bool


@dataclass(frozen=True)
class _PayoutFacts:
    amount_rao: int
    policy_version: str
    confirmed_at: datetime


@dataclass(frozen=True)
class ActivityRow:
    """One anonymised event on a conjecture. `solver` is already a pseudonym."""

    event: str
    occurred_at: datetime
    solver: str


@dataclass(frozen=True)
class TaskActivity:
    attempts: int
    solvers: int
    verified: int
    certified: int
    items: tuple[ActivityRow, ...]


@dataclass(frozen=True)
class QueueDepths:
    awaiting_verification: int
    awaiting_review: int
    awaiting_reward: int

    @property
    def drained(self) -> bool:
        """The precondition README.md sets on starting a pin rotation.

        "No pin update may begin while any submission is queued, leased, running, retryable, or
        awaiting review or reward processing."
        """
        return (
            self.awaiting_verification == 0
            and self.awaiting_review == 0
            and self.awaiting_reward == 0
        )


# --- Feeds ---------------------------------------------------------------------------------


async def certified_page(
    session: AsyncSession,
    *,
    limit: int,
    after: tuple[datetime, uuid.UUID] | None = None,
) -> tuple[ResultRow, ...]:
    """Certified results, newest first. `after` is the keyset position, exclusive."""
    return await _page(session, CERTIFIED, limit=limit, after=after)


async def in_review_page(
    session: AsyncSession,
    *,
    limit: int,
    after: tuple[datetime, uuid.UUID] | None = None,
) -> tuple[ResultRow, ...]:
    """Lean-verified results awaiting manual review, newest first."""
    return await _page(session, IN_REVIEW, limit=limit, after=after)


async def all_results_page(
    session: AsyncSession,
    *,
    limit: int,
    after: tuple[datetime, uuid.UUID] | None = None,
) -> tuple[ResultRow, ...]:
    """Every submission in one feed, newest first, for the public dashboard.

    Unfiltered by state, unlike the two feeds above. A queued, running or rejected attempt is
    listed alongside a certified one, and the row's ``verification_status``,
    ``manual_review_status`` and ``reward_status`` say which it is. The dashboard reports the whole
    pipeline, and an attempt that vanished from it on rejection would misreport the work done —
    a reader would see only successes and read the feed as the complete history.

    Listing a row publishes its state, not its contents. The three gates that matter are unchanged
    and none of them lives in this query:

    * the proof bytes — ``accepted_solution`` still requires review approval, in the query;
    * the verifier report — ``report_available`` is false until Lean verifies the submission, and
      ``public_report`` enforces the same gate;
    * the money — ``ResultRow`` has no column for the coldkey, the payment reference or the
      extrinsic, so widening the row set cannot expose them.

    One side effect worth knowing rather than guarding against: a failed attempt's existence and
    timing are now public, and the ``activity`` pseudonyms are correspondingly weaker for solvers
    who appear here. Those pseudonyms were already correlatable through verified results — see the
    caveat on ``activity`` — and reporting the real pipeline is worth more than tightening them.

    ``public_result`` is deliberately *not* widened with this: reading one submission by id stays
    restricted to Lean-verified work, so an id alone still cannot be turned into a probe for queued
    or Lean-failed submissions. A dashboard that already holds those rows does not need to re-fetch
    them, so the asymmetry costs nothing.
    """
    return await _page(session, [], limit=limit, after=after)


async def public_result(session: AsyncSession, result_id: uuid.UUID) -> ResultRow | None:
    """One result, if it is on a public feed.

    A submission that Lean has not verified is reported as absent rather than forbidden — the same
    rule ``get_for_miner`` follows, so a submission id cannot be probed for the state of something
    not published.
    """
    statement = select(Submission).where(
        Submission.id == result_id,
        *PUBLISHED,
    )
    submission = (await session.execute(statement)).scalar_one_or_none()
    if submission is None:
        return None
    rows = await _decorate(session, [submission])
    return rows[0]


def _is_public(submission: Submission) -> bool:
    """``PUBLISHED`` evaluated against a row already loaded.

    Needed because ``all_results_page`` no longer filters on it: the predicate still decides
    whether a row's *report* may be served, and that has to be answered per row rather than by the
    query.
    """
    return submission.verification_status == VerificationState.VERIFIED


async def _page(
    session: AsyncSession,
    conditions: Sequence,
    *,
    limit: int,
    after: tuple[datetime, uuid.UUID] | None,
) -> tuple[ResultRow, ...]:
    statement: Select = select(Submission).where(*conditions)
    if after is not None:
        # Row-value comparison, so the pair is compared as one value and the partial index on
        # (created_at DESC, id DESC) is scanned from exactly the right offset. Comparing the
        # columns separately would need an OR and would not use the index the same way.
        statement = statement.where(
            tuple_(Submission.created_at, Submission.id) < tuple_(after[0], after[1])
        )
    statement = statement.order_by(
        Submission.created_at.desc(), Submission.id.desc()
    ).limit(limit)
    submissions = list((await session.execute(statement)).scalars())
    return await _decorate(session, submissions)


async def _decorate(
    session: AsyncSession, submissions: Sequence[Submission]
) -> tuple[ResultRow, ...]:
    """Attach the verdict and the payout to a page of submissions."""
    if not submissions:
        return ()
    ids = [submission.id for submission in submissions]
    runs = await _latest_runs(session, ids)
    confirmed = await _confirmed_payouts(session, ids)
    reviews = await _latest_reviews(session, ids)
    rows = []
    for submission in submissions:
        run = runs.get(submission.id)
        payout = confirmed.get(submission.id)
        review = reviews.get(submission.id)
        rows.append(
            ResultRow(
                id=submission.id,
                hotkey=submission.hotkey,
                verification_status=submission.verification_status,
                manual_review_status=submission.manual_review_status,
                reward_status=submission.reward_status,
                task_id=submission.task_id,
                reward_target_id=submission.reward_target_id,
                task_bundle_sha256=bytes(submission.task_bundle_sha256),
                created_at=submission.created_at,
                bounty_amount_rao=(
                    submission.bounty_amount_rao
                    if payout is None
                    else payout.amount_rao
                ),
                bounty_policy_version=(
                    submission.bounty_policy_version
                    if payout is None
                    else payout.policy_version
                ),
                review_policy_version=submission.review_policy_version,
                verified_at=None if run is None else run.finished_at,
                certified_at=None if payout is None else payout.confirmed_at,
                verifier_version=None if run is None else run.verifier_version,
                sandbox_mode=None if run is None else run.sandbox_mode,
                report_available=(
                    run is not None and run.has_report and _is_public(submission)
                ),
                # Not gated on `_is_public`, unlike the report: `ReviewRow` is an allowlist of a
                # decision's public half, so a review-rejected row on the dashboard feed publishes
                # its outcome and `notes_public` and still cannot leak the reviewer, the internal
                # notes, or advisory evidence — `_latest_reviews` never selects them.
                review=review,
                solution_available=(
                    submission.manual_review_status == ManualReviewState.APPROVED
                ),
            )
        )
    return tuple(rows)


async def _latest_reviews(
    session: AsyncSession, submission_ids: Sequence[uuid.UUID]
) -> Mapping[uuid.UUID, ReviewRow]:
    """The latest binding review per submission, with only fields safe to publish.

    Advisory model assessments are evidence, not decisions. They are excluded before selecting
    the greatest id so a later advisory row cannot hide the human or automatic decision. The
    query does not select internal ``notes``, reviewer identity, or raw advisory ``evidence``.
    """
    latest = (
        select(func.max(ReviewDecision.id))
        .where(
            ReviewDecision.submission_id.in_(submission_ids),
            ReviewDecision.kind != ReviewerKind.ADVISORY,
        )
        .group_by(ReviewDecision.submission_id)
        .scalar_subquery()
    )
    statement = select(
        ReviewDecision.submission_id,
        ReviewDecision.decision,
        ReviewDecision.reason_code,
        ReviewDecision.notes_public,
        ReviewDecision.policy_version,
        ReviewDecision.created_at,
    ).where(ReviewDecision.id.in_(latest))
    return {
        row.submission_id: ReviewRow(
            decision=row.decision,
            reason_code=row.reason_code,
            notes_public=row.notes_public,
            policy_version=row.policy_version,
            decided_at=row.created_at,
        )
        for row in (await session.execute(statement)).all()
    }


async def _latest_runs(
    session: AsyncSession, submission_ids: Sequence[uuid.UUID]
) -> Mapping[uuid.UUID, _RunFacts]:
    """The most recent run per submission.

    ``verification_runs.id`` is monotonic, so the latest attempt is the greatest id — no
    timestamp comparison, which could tie. The report bytes are excluded from the load: a page
    of results only needs to know whether a report exists, and the reports are large.
    """
    latest = (
        select(func.max(VerificationRun.id))
        .where(VerificationRun.submission_id.in_(submission_ids))
        .group_by(VerificationRun.submission_id)
        .scalar_subquery()
    )
    statement = select(
        VerificationRun.submission_id,
        VerificationRun.verifier_version,
        VerificationRun.sandbox_mode,
        VerificationRun.report_digest,
        VerificationRun.finished_at,
    ).where(VerificationRun.id.in_(latest))
    return {
        row.submission_id: _RunFacts(
            verifier_version=row.verifier_version,
            sandbox_mode=row.sandbox_mode,
            finished_at=row.finished_at,
            has_report=row.report_digest is not None,
        )
        for row in (await session.execute(statement)).all()
    }


async def _confirmed_payouts(
    session: AsyncSession, submission_ids: Sequence[uuid.UUID]
) -> Mapping[uuid.UUID, _PayoutFacts]:
    """When each submission's payout was confirmed on chain.

    A submission normally has one payout, but the schema permits several on purpose — a second
    real transfer must be recordable. PostgreSQL ``DISTINCT ON`` selects the latest confirmed
    attempt and keeps its timestamp, actual amount, and pricing policy together as one fact.
    """
    statement = (
        select(
            RewardEvent.submission_id,
            RewardEvent.amount_rao,
            RewardEvent.pricing_policy_version,
            RewardEvent.confirmed_at,
        )
        .where(
            RewardEvent.submission_id.in_(submission_ids),
            RewardEvent.status == PayoutState.CONFIRMED,
        )
        .distinct(RewardEvent.submission_id)
        .order_by(
            RewardEvent.submission_id,
            RewardEvent.confirmed_at.desc(),
            RewardEvent.id.desc(),
        )
    )
    return {
        row.submission_id: _PayoutFacts(
            amount_rao=row.amount_rao,
            policy_version=row.pricing_policy_version,
            confirmed_at=row.confirmed_at,
        )
        for row in (await session.execute(statement)).all()
        if row.confirmed_at is not None
    }


async def public_report(
    session: AsyncSession, result_id: uuid.UUID
) -> tuple[bytes, bytes] | None:
    """The latest report bytes and digest for a publicly visible result, or None.

    Returns the raw bytes; reducing them to the publishable subset is the API's job, because the
    allowlist of fields is a transport concern and this package stays free of them.
    """
    statement = (
        select(VerificationRun.report, VerificationRun.report_digest)
        .join(Submission, Submission.id == VerificationRun.submission_id)
        .where(VerificationRun.submission_id == result_id, *PUBLISHED)
        .order_by(VerificationRun.id.desc())
        .limit(1)
    )
    row = (await session.execute(statement)).first()
    if row is None or row.report is None or row.report_digest is None:
        return None
    return bytes(row.report), bytes(row.report_digest)


async def accepted_solution(
    session: AsyncSession, result_id: uuid.UUID
) -> tuple[bytes, bytes, int] | None:
    """The proof bytes, digest and length for an approved submission, or None.

    The one place in this module that reads `proofs.content`, and it is gated on ``ACCEPTED``
    rather than on ``PUBLISHED``. That difference is the whole disclosure rule: a submission is
    *listed* once it is Lean-verified, but its proof is published only once review has approved
    it. An in-review row is on the feed with no solution to fetch.

    The filter is in the query, not in the caller. A router that forgot the check would otherwise
    publish the proof of a submission still awaiting review, and the bytes are the one thing here
    that cannot be un-published.
    """
    statement = (
        select(Proof.content, Proof.digest, Proof.byte_length)
        .join(Submission, Submission.proof_digest == Proof.digest)
        .where(Submission.id == result_id, *ACCEPTED)
    )
    row = (await session.execute(statement)).first()
    if row is None:
        return None
    return bytes(row.content), bytes(row.digest), row.byte_length


# --- Per-conjecture counters ---------------------------------------------------------------
#
# All three key on ``reward_target_id``, not ``task_id``. Three reasons, and they compound:
#
#   * A conjecture is issued as one task per mode, so a task-keyed count answers "attempts at
#     proving it" rather than "attempts at it", and the public page shows both directions.
#   * ``task_id`` is seeded with the pinned source revision, so under the weekly rotation a
#     task-keyed counter silently resets to zero every week.
#   * The public slug is derived from ``reward_target_id``, so this is the same identity the URL
#     uses. A counter keyed on anything else could disagree with the page it appears on.
#
# ``submissions_reward_target_idx`` (V006) covers all three.


async def attempts_by_conjecture(session: AsyncSession) -> Mapping[str, int]:
    """Paid attempts per conjecture, keyed by reward target, for the conjecture list.

    One grouped scan for the whole pool rather than a count per row on the page. The pool is a
    few hundred conjectures, so the result is small enough to attach to every summary in the
    response.
    """
    statement = (
        select(Submission.reward_target_id, func.count())
        .select_from(Submission)
        .group_by(Submission.reward_target_id)
    )
    return {
        reward_target_id: count
        for reward_target_id, count in (await session.execute(statement)).all()
    }


async def attempts_by_task(session: AsyncSession) -> Mapping[str, int]:
    """Paid attempts per task id, for the miner-facing per-task view.

    Kept alongside the conjecture-keyed count because a solver choosing a bundle to build cares
    which *direction* has been attempted, which the grouped count deliberately hides.
    """
    statement = (
        select(Submission.task_id, func.count())
        .select_from(Submission)
        .group_by(Submission.task_id)
    )
    return {task_id: count for task_id, count in (await session.execute(statement)).all()}


async def attempts_for_conjecture(session: AsyncSession, reward_target_id: str) -> int:
    """Paid attempts against one conjecture, in either direction.

    A single count over ``submissions_reward_target_idx``, for the detail page, which needs the
    number but not the stream. Running ``activity`` for it would add an ordered read and a
    four-way aggregate for one integer.
    """
    statement = (
        select(func.count())
        .select_from(Submission)
        .where(Submission.reward_target_id == reward_target_id)
    )
    return (await session.execute(statement)).scalar_one()


async def activity(
    session: AsyncSession,
    reward_target_id: str,
    *,
    limit: int,
    pseudonymise: Callable[[str], str],
) -> TaskActivity:
    """The anonymised activity stream for one conjecture.

    ``pseudonymise`` is required, not optional: the hotkey is read here, mapped, and dropped — it
    never lands on ``ActivityRow``.

    The pseudonyms are no longer unlinkable in practice, and this docstring should not pretend
    otherwise. ``ResultRow.hotkey`` publishes the solver of every verified result, and a result
    carries ``verified_at``; an activity event carries the same transition at hour resolution on
    the same conjecture. Correlating the two names the solver behind a pseudonym, and once named,
    that solver's *other* events on this conjecture — including the failed attempts the pseudonym
    was there to protect — are attributed too. The mapping is still applied because it is what
    the stream is shaped around, but it protects only solvers who have no verified result here.

    The counters are computed from the same rows as the stream when the stream covers the whole
    history, and from a separate aggregate when it does not, so a truncated stream never implies
    a truncated count.
    """
    bounded = min(max(limit, 1), MAX_ACTIVITY_ROWS)
    statement = (
        select(
            Submission.hotkey,
            Submission.created_at,
            Submission.verification_status,
            Submission.manual_review_status,
            Submission.reward_status,
        )
        .where(Submission.reward_target_id == reward_target_id)
        .order_by(Submission.created_at.desc(), Submission.id.desc())
        .limit(bounded)
    )
    items = tuple(
        ActivityRow(
            event=_event(row.verification_status, row.manual_review_status, row.reward_status),
            occurred_at=row.created_at,
            solver=pseudonymise(row.hotkey),
        )
        for row in (await session.execute(statement)).all()
    )

    totals = (
        await session.execute(
            select(
                func.count(),
                func.count(func.distinct(Submission.hotkey)),
                func.count().filter(
                    Submission.verification_status == VerificationState.VERIFIED
                ),
                func.count().filter(Submission.reward_status == RewardState.REWARDED),
            )
            .select_from(Submission)
            .where(Submission.reward_target_id == reward_target_id)
        )
    ).one()
    return TaskActivity(
        attempts=totals[0],
        solvers=totals[1],
        verified=totals[2],
        certified=totals[3],
        items=items,
    )


def _event(
    verification: VerificationState,
    review: ManualReviewState,
    reward: RewardState,
) -> str:
    """The furthest state this submission has reached, as one label.

    Reported as a single event rather than as a per-transition stream because the schema keeps
    current state, not transitions: ``submission_events`` is Stage 2 work. Saying "attempt" for
    a submission that has since been certified would be wrong, so the label is the furthest
    point reached.
    """
    if reward == RewardState.REWARDED:
        return "certified"
    if verification == VerificationState.REJECTED:
        return "rejected"
    if verification == VerificationState.VERIFIED:
        return "verified"
    del review  # the review axis is not published per-event; only its outcome, via `certified`
    return "attempt"


# --- System status -------------------------------------------------------------------------


async def queue_depths(session: AsyncSession) -> QueueDepths:
    """The three worker queues, counted over their own partial indexes.

    Each `count` matches a partial index predicate from the initial migration exactly, so these
    are index-only counts rather than table scans, and they stay cheap as history grows.
    """
    statement = select(
        func.count().filter(
            Submission.verification_status == VerificationState.UNVERIFIED
        ),
        func.count().filter(
            (Submission.verification_status == VerificationState.VERIFIED)
            & (Submission.manual_review_status == ManualReviewState.UNREVIEWED)
        ),
        func.count().filter(Submission.reward_status == RewardState.ELIGIBLE),
    ).select_from(Submission)
    row = (await session.execute(statement)).one()
    return QueueDepths(
        awaiting_verification=row[0],
        awaiting_review=row[1],
        awaiting_reward=row[2],
    )


__all__ = [
    "ACCEPTED",
    "CERTIFIED",
    "IN_REVIEW",
    "PUBLISHED",
    "MAX_ACTIVITY_ROWS",
    "ActivityRow",
    "QueueDepths",
    "ReviewRow",
    "ResultRow",
    "TaskActivity",
    "accepted_solution",
    "activity",
    "all_results_page",
    "attempts_by_conjecture",
    "attempts_by_task",
    "attempts_for_conjecture",
    "certified_page",
    "in_review_page",
    "public_report",
    "public_result",
    "queue_depths",
]
