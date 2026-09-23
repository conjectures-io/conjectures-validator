"""The miniz_oxide DEFLATE competition: a proven LZ77 parser, scored on bytes and speed.

A submission is `parse.rs` and a Lean proof, `Parse.lean`, that it meets the specification.
The competition's gate worker runs `verify.py` over it; its chain watcher records Subnet 66
registrations; its weight setter scores accepted submissions and sets weights. All of them
write the competition's own database, which this adapter reads -- and writes to only to queue
a submission, exactly as the competition's own service does.

The rules mirrored here are the competition's, restated from its store
(`validator/db/submissions.py`, `validator/db/registrations.py`):

* a hotkey must hold a registration row to submit at all;
* one registration buys one *accepted* submission -- the slot is spent by the gate on
  acceptance, not here -- so a hotkey may not have more queued than it has unspent;
* the same two files from the same hotkey are the same submission.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, exists, func, or_, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from submission_api.competitions.base import (
    CompetitionAdapter,
    CompetitionInfo,
    Eligibility,
    FileSpec,
    Metric,
    MetricValue,
    NoEntitlement,
    NotRegistered,
    OperatorView,
    Position,
    Queued,
    Report,
    ScoreEntry,
    Scores,
    Standing,
    Stats,
    Submission,
    SubmissionState,
)
from submission_api.competitions.miniz_oxide import tables as t

SLUG = "miniz-oxide"
# Each file. The largest reference submission is under 20 KB; the competition's own service
# enforces the same cap (`MAX_FILE_BYTES`).
MAX_FILE_BYTES = 512 * 1024
# The multiple of the incumbent's parse time a submission may not exceed. The gate enforces
# it; it is published so a miner sees the bar before spending an hour of gate time.
SPEED_FLOOR = 8.0

PENDING = (SubmissionState.QUEUED.value, SubmissionState.VERIFYING.value)

INFO = CompetitionInfo(
    slug=SLUG,
    name="miniz_oxide DEFLATE",
    description=(
        "Write a faster or smaller LZ77 parser for miniz_oxide's DEFLATE encoder and prove it "
        "correct in Lean. Accepted parsers are measured on a benchmark corpus."
    ),
    files=(
        FileSpec("parse.rs", MAX_FILE_BYTES, "the parser, in the competition's Rust subset"),
        FileSpec("Parse.lean", MAX_FILE_BYTES, "the Lean proof that parse.rs meets the spec"),
    ),
    metrics=(
        Metric("bytes", "Compressed size", "bytes", "lower"),
        Metric("vs_incumbent", "Size vs incumbent", "ratio", "lower"),
        Metric("ratio_pct", "Compressed / raw", "%", "lower"),
        Metric("time_ratio", "Parse time vs incumbent", "ratio", "lower"),
        Metric("compression_seconds", "Compression time", "s", "lower"),
    ),
    ranked_by="bytes",
)

_s = t.submissions
# Competitors only. Baseline rows are the operator's reference points: they have no hotkey,
# spend no registration and are not anyone's standing.
_COMPETITOR = _s.c.hotkey.is_not(None)
_RANKABLE = and_(_COMPETITOR, _s.c.state == SubmissionState.ACCEPTED.value, _s.c.bytes.is_not(None))


def _micros(moment: datetime) -> str:
    return str(int(moment.astimezone(UTC).timestamp() * 1_000_000))


def _from_micros(value: str) -> datetime:
    return datetime.fromtimestamp(int(value) / 1_000_000, tz=UTC)


def _metrics(row: Any) -> dict[str, MetricValue]:
    size = row.bytes
    return {
        "bytes": size,
        "vs_incumbent": round(size / row.incumbent_bytes, 5)
        if size is not None and row.incumbent_bytes
        else None,
        "ratio_pct": round(100.0 * size / row.raw_bytes, 4)
        if size is not None and row.raw_bytes
        else None,
        "time_ratio": row.time_ratio,
        "compression_seconds": row.compression_seconds,
    }


def _state(value: str) -> SubmissionState:
    try:
        return SubmissionState(value)
    except ValueError:
        # The competition's CHECK constraint allows exactly the five generic states, so this
        # is unreachable against a schema the contract test has passed. Reported as the
        # validator's problem rather than guessed at.
        return SubmissionState.ERROR


def _submission(row: Any) -> Submission:
    return Submission(
        id=str(row.id),
        hotkey=row.hotkey,
        digest=row.digest,
        state=_state(row.state),
        submitted_at=row.submitted_at,
        finished_at=row.finished_at,
        metrics=_metrics(row),
        position=(_micros(row.submitted_at), str(row.id)),
    )


def _standing(row: Any) -> Standing:
    return Standing(
        hotkey=row.hotkey,
        submission_id=str(row.id),
        submitted_at=row.submitted_at,
        metrics=_metrics(row),
        position=(str(row.bytes), _micros(row.submitted_at), str(row.id)),
    )


_COLUMNS = (
    _s.c.id,
    _s.c.hotkey,
    _s.c.digest,
    _s.c.state,
    _s.c.submitted_at,
    _s.c.finished_at,
    _s.c.bytes,
    _s.c.incumbent_bytes,
    _s.c.raw_bytes,
    _s.c.time_ratio,
    _s.c.compression_seconds,
)


def _best_per_hotkey():
    """Each hotkey's best accepted row: fewest bytes, then earliest, then lowest id.

    DISTINCT ON needs its key to lead the ORDER BY, so the board's own order is applied by the
    caller over this subquery.
    """
    return (
        select(*_COLUMNS)
        .where(_RANKABLE)
        .distinct(_s.c.hotkey)
        .order_by(_s.c.hotkey, _s.c.bytes, _s.c.submitted_at, _s.c.id)
        .subquery("best")
    )


def _rank_key(best) -> Any:
    return tuple_(best.c.bytes, best.c.submitted_at, best.c.id)


def _unclaimed(hotkey: str):
    claimed = select(t.entitlement_claims.c.registration_id)
    return and_(
        t.registrations.c.ss58_hot == hotkey,
        t.registrations.c.id.not_in(claimed),
    )


class MinizOxide(CompetitionAdapter):
    info = INFO

    def parse_id(self, raw: str) -> str | None:
        # BIGINT ids. Anything else cannot name a row, and is a 404 rather than a query.
        if not raw.isdigit() or len(raw) > 18 or int(raw) < 1:
            return None
        return str(int(raw))

    # -- public reads --------------------------------------------------------------------

    async def headline(self, session: AsyncSession) -> Mapping[str, MetricValue]:
        # The incumbent moves when the operator promotes a new one, so the newest measurement
        # of it is the bar -- the same number the competition's own /health reports.
        incumbent = (
            await session.execute(
                select(_s.c.incumbent_bytes)
                .where(_s.c.incumbent_bytes.is_not(None))
                .order_by(_s.c.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return {"incumbent_bytes": incumbent, "speed_floor": SPEED_FLOOR}

    async def queue_depth(self, session: AsyncSession) -> int:
        return int(
            (
                await session.execute(
                    select(func.count()).select_from(_s).where(_s.c.state.in_(PENDING))
                )
            ).scalar_one()
        )

    async def leaderboard(
        self, session: AsyncSession, *, after: Position | None, limit: int
    ) -> Sequence[Standing]:
        best = _best_per_hotkey()
        stmt = select(best).order_by(best.c.bytes, best.c.submitted_at, best.c.id).limit(limit)
        if after is not None:
            stmt = stmt.where(_rank_key(best) > self._rank_position(after))
        return [_standing(row) for row in (await session.execute(stmt)).all()]

    async def rank_before(self, session: AsyncSession, after: Position) -> int:
        best = _best_per_hotkey()
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(best)
                    .where(_rank_key(best) <= self._rank_position(after))
                )
            ).scalar_one()
        )

    @staticmethod
    def _rank_position(after: Position) -> Any:
        size, moment, sub_id = after
        return tuple_(int(size), _from_micros(moment), int(sub_id))

    async def submissions(
        self,
        session: AsyncSession,
        *,
        after: Position | None,
        limit: int,
        state: SubmissionState | None = None,
        hotkeys: Sequence[str] | None = None,
    ) -> Sequence[Submission]:
        stmt = (
            select(*_COLUMNS)
            .where(_COMPETITOR)
            .order_by(_s.c.submitted_at.desc(), _s.c.id.desc())
            .limit(limit)
        )
        if after is not None:
            moment, sub_id = after
            stmt = stmt.where(
                tuple_(_s.c.submitted_at, _s.c.id) < tuple_(_from_micros(moment), int(sub_id))
            )
        if state is not None:
            stmt = stmt.where(_s.c.state == state.value)
        if hotkeys is not None:
            stmt = stmt.where(_s.c.hotkey.in_(list(hotkeys)))
        return [_submission(row) for row in (await session.execute(stmt)).all()]

    async def submission(self, session: AsyncSession, submission_id: str) -> Submission | None:
        row = (
            await session.execute(
                select(*_COLUMNS).where(_s.c.id == int(submission_id), _COMPETITOR)
            )
        ).first()
        return _submission(row) if row is not None else None

    async def report(self, session: AsyncSession, submission_id: str) -> Report | None:
        row = (
            await session.execute(
                select(_s.c.id, _s.c.state, _s.c.exit_code, _s.c.report).where(
                    _s.c.id == int(submission_id), _COMPETITOR
                )
            )
        ).first()
        if row is None:
            return None
        return Report(id=str(row.id), state=_state(row.state), exit_code=row.exit_code, text=row.report)

    async def stats(self, session: AsyncSession) -> Stats:
        counted = (
            await session.execute(
                select(_s.c.state, func.count()).where(_COMPETITOR).group_by(_s.c.state)
            )
        ).all()
        row = (
            await session.execute(
                select(
                    func.count(func.distinct(_s.c.hotkey)),
                    func.min(_s.c.bytes),
                    func.max(_s.c.finished_at),
                ).where(_RANKABLE)
            )
        ).one()
        return Stats(
            by_state={state: int(count) for state, count in counted},
            competitors=int(row[0]),
            last_accepted_at=row[2],
            best={"bytes": row[1]},
        )

    async def best_for(self, session: AsyncSession, hotkey: str) -> Standing | None:
        row = (
            await session.execute(
                select(*_COLUMNS)
                .where(_RANKABLE, _s.c.hotkey == hotkey)
                .order_by(_s.c.bytes, _s.c.submitted_at, _s.c.id)
                .limit(1)
            )
        ).first()
        return _standing(row) if row is not None else None

    # -- who may submit ------------------------------------------------------------------

    async def eligibility(self, session: AsyncSession, hotkey: str) -> Eligibility:
        registered = (
            await session.execute(
                select(exists().where(t.registrations.c.ss58_hot == hotkey))
            )
        ).scalar_one()
        slots = int(
            (
                await session.execute(
                    select(func.count()).select_from(t.registrations).where(_unclaimed(hotkey))
                )
            ).scalar_one()
        )
        pending = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(_s)
                    .where(_s.c.hotkey == hotkey, _s.c.state.in_(PENDING))
                )
            ).scalar_one()
        )
        # How many MORE could be queued now. A queued submission has not spent a registration
        # -- only acceptance does -- so the raw count of unspent ones would stay flat while
        # the ability to queue another had already gone.
        return Eligibility(
            registered=bool(registered), slots_remaining=max(slots - pending, 0), pending=pending
        )

    async def registered_by(self, session: AsyncSession, *, hotkey: str, coldkey: str) -> bool:
        return bool(
            (
                await session.execute(
                    select(
                        exists().where(
                            t.registrations.c.ss58_hot == hotkey,
                            t.registrations.c.ss58_cold == coldkey,
                        )
                    )
                )
            ).scalar_one()
        )

    async def hotkeys_of(self, session: AsyncSession, coldkey: str) -> Sequence[str]:
        rows = await session.execute(
            select(t.registrations.c.ss58_hot)
            .where(t.registrations.c.ss58_cold == coldkey)
            .distinct()
            .order_by(t.registrations.c.ss58_hot)
        )
        return [hot for (hot,) in rows]

    # -- writing -------------------------------------------------------------------------

    async def queue(
        self,
        session: AsyncSession,
        *,
        hotkey: str,
        digest: str,
        files: Mapping[str, bytes],
    ) -> Queued:
        # One hotkey's submits run one at a time. Without this, two different submissions
        # racing past the entitlement check below could both be queued against one slot; the
        # gate would still refuse to pay the second at acceptance, but only after an hour of
        # verifying it. Transaction-scoped, so it is released by the commit below.
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"competition-submit:{SLUG}:{hotkey}"},
        )
        existing = await self._find(session, hotkey, digest)
        if existing is not None:
            eligibility = await self.eligibility(session, hotkey)
            await session.commit()
            return Queued(submission=existing, created=False, eligibility=eligibility)

        eligibility = await self.eligibility(session, hotkey)
        if not eligibility.registered:
            await session.rollback()
            raise NotRegistered(hotkey)
        if not eligibility.slots_remaining:
            await session.rollback()
            raise NoEntitlement(
                f"{eligibility.pending} submission(s) already queued against this hotkey's "
                "unspent registrations: one registration buys one accepted submission, so "
                "register again to submit again"
            )

        inserted = (
            await session.execute(
                insert(_s)
                .values(hotkey=hotkey, digest=digest, state=SubmissionState.QUEUED.value)
                .on_conflict_do_nothing(index_elements=["hotkey", "digest"])
                .returning(_s.c.id)
            )
        ).scalar_one_or_none()
        created = inserted is not None
        if created:
            await session.execute(
                insert(t.submission_files),
                [
                    {"submission_id": inserted, "name": spec.name, "content": files[spec.name]}
                    for spec in self.info.files
                ],
            )
        row = await self._find(session, hotkey, digest)
        assert row is not None, "the row was either inserted above or already there"
        after = await self.eligibility(session, hotkey)
        await session.commit()
        return Queued(submission=row, created=created, eligibility=after)

    async def _find(self, session: AsyncSession, hotkey: str, digest: str) -> Submission | None:
        row = (
            await session.execute(
                select(*_COLUMNS).where(_s.c.hotkey == hotkey, _s.c.digest == digest)
            )
        ).first()
        return _submission(row) if row is not None else None

    # -- optional capabilities -----------------------------------------------------------

    async def files(self, session: AsyncSession, submission_id: str) -> Mapping[str, bytes]:
        rows = (
            await session.execute(
                select(t.submission_files.c.name, t.submission_files.c.content)
                .join(_s, _s.c.id == t.submission_files.c.submission_id)
                .where(
                    _s.c.id == int(submission_id),
                    _COMPETITOR,
                    _s.c.state == SubmissionState.ACCEPTED.value,
                )
            )
        ).all()
        found = {name: bytes(content) for name, content in rows}
        if set(found) != {spec.name for spec in self.info.files}:
            # Not accepted, or queued through the competition's own service, which keeps the
            # files on the gate host's disk and never wrote them here.
            raise LookupError(submission_id)
        return found

    async def scores(self, session: AsyncSession) -> Scores | None:
        ws = t.weight_sets
        latest = (
            await session.execute(
                select(ws).order_by(ws.c.created_at.desc(), ws.c.id.desc()).limit(1)
            )
        ).first()
        if latest is None:
            return None
        snap = t.score_snapshots
        rows = (
            await session.execute(
                select(snap)
                .where(snap.c.weight_set_id == latest.id)
                .order_by(snap.c.payable_weight.desc(), snap.c.id)
            )
        ).all()
        return Scores(
            computed_at=latest.created_at,
            block=latest.block,
            dry_run=latest.dry_run,
            accepted=latest.accepted,
            summary=latest.summary,
            entries=tuple(
                ScoreEntry(
                    hotkey=row.hotkey,
                    submission_id=str(row.submission_id) if row.submission_id else None,
                    weight=row.payable_weight,
                    metrics={
                        "time_s": row.time_s,
                        "ratio_pct": row.ratio_pct,
                        "on_frontier": int(row.on_frontier),
                        "pareto_weight": row.pareto_weight,
                        "improvement_weight": row.improvement_weight,
                        "combined_weight": row.combined_weight,
                    },
                    note=row.burn_reason or ("baseline" if row.baseline_key else None),
                )
                for row in rows
            ),
        )

    async def stuck(
        self,
        session: AsyncSession,
        *,
        claimed_before: datetime,
        after: Position | None,
        limit: int,
    ) -> Sequence[OperatorView]:
        stmt = (
            self._operator_select()
            .where(
                _COMPETITOR,
                or_(
                    _s.c.state == SubmissionState.ERROR.value,
                    and_(
                        _s.c.state == SubmissionState.VERIFYING.value,
                        _s.c.claimed_at < claimed_before,
                    ),
                ),
            )
            .order_by(_s.c.submitted_at.desc(), _s.c.id.desc())
            .limit(limit)
        )
        if after is not None:
            moment, sub_id = after
            stmt = stmt.where(
                tuple_(_s.c.submitted_at, _s.c.id) < tuple_(_from_micros(moment), int(sub_id))
            )
        return [self._operator(row) for row in (await session.execute(stmt)).all()]

    async def operator_view(
        self, session: AsyncSession, submission_id: str
    ) -> OperatorView | None:
        row = (
            await session.execute(
                self._operator_select().where(_s.c.id == int(submission_id), _COMPETITOR)
            )
        ).first()
        return self._operator(row) if row is not None else None

    async def requeue(self, session: AsyncSession, submission_id: str) -> bool:
        # Only from error or verifying, and the predicate is the guard: a worker finishing its
        # claim between an operator's read and this write loses nothing -- this updates no
        # row, and the gate's own finish() refuses a claim whose attempt token was cleared.
        # Never from accepted or rejected: one has spent a registration, the other is a
        # verdict the miner has already been shown.
        result = await session.execute(
            update(_s)
            .where(
                _s.c.id == int(submission_id),
                _COMPETITOR,
                _s.c.state.in_([SubmissionState.ERROR.value, SubmissionState.VERIFYING.value]),
            )
            .values(
                state=SubmissionState.QUEUED.value,
                worker_id=None,
                claimed_at=None,
                verification_attempt=None,
            )
        )
        await session.commit()
        return bool(result.rowcount)  # pyright: ignore[reportAttributeAccessIssue]

    @staticmethod
    def _operator_select():
        has_files = exists().where(t.submission_files.c.submission_id == _s.c.id)
        return select(
            *_COLUMNS,
            _s.c.worker_id,
            _s.c.claimed_at,
            _s.c.exit_code,
            _s.c.report,
            has_files.label("has_files"),
        )

    @staticmethod
    def _operator(row: Any) -> OperatorView:
        return OperatorView(
            submission=_submission(row),
            worker_id=row.worker_id,
            claimed_at=row.claimed_at,
            exit_code=row.exit_code,
            has_files=bool(row.has_files),
            report=row.report,
        )


__all__ = ["INFO", "MAX_FILE_BYTES", "SLUG", "SPEED_FLOOR", "MinizOxide"]
