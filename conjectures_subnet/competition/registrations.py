"""Registrations, and the submission slots they buy.

Two jobs: record what the chain watcher sees (`publish_snapshot`), and answer
"may this hotkey submit, and what does an acceptance cost them" (`available_slots`,
`claim_slot`). The entitlement rule -- one registration, one accepted submission --
lives in `entitlement_claims`' keys; this module only drives it.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable, Iterable
from typing import Protocol, cast

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from . import models
from conjectures_subnet.db.engine import session_scope

logger = logging.getLogger(__name__)

# Log backfill progress every this many resolved timestamps: an empty database resolves
# one archive lookup per uid, sequentially, and without progress lines the watcher looks
# hung while it grinds through the whole subnet.
PROGRESS_EVERY = 25


class Neuron(Protocol):
    """The four fields this module reads off a metagraph neuron.

    Structural rather than the SDK's own class, so the diffing stays testable with a
    plain stub and the store keeps no import of bittensor.
    """

    uid: int
    hotkey: str
    coldkey: str
    block_at_registration: int


def changed_rows(
    latest: dict[int, tuple[str, str]], neurons: Iterable[Neuron]
) -> list[dict[str, object]]:
    """The rows to insert for a snapshot: only uids whose (hot, cold) pair moved.

    `latest` maps uid -> the last recorded pair. A neuron yields a row only when it has
    no prior record or its pair differs. Pure, so the change detection is testable
    without a database.

    Each row's `block` is the neuron's own registration height, not the snapshot's, so a
    row records when the uid actually registered even if the watcher saw it much later.
    `block_date` is filled by the caller, which resolves that height's on-chain time --
    an archive lookup -- only for these already-filtered rows.
    """
    return [
        {
            "uid": n.uid,
            "ss58_hot": n.hotkey,
            "ss58_cold": n.coldkey,
            "block": n.block_at_registration,
        }
        for n in neurons
        if latest.get(n.uid) != (n.hotkey, n.coldkey)
    ]


def resolve_block_dates(
    rows: list[dict[str, object]], block_time: Callable[[int], dt.datetime], *, scope: str
) -> None:
    # Fill each changed row's block_date from its registration height, in place. Runs
    # only over the diffed rows, so a quiet tick does no lookups at all.
    total = len(rows)
    logger.info("resolving %d registration timestamp(s) [%s]", total, scope)
    for i, row in enumerate(rows, start=1):
        row["block_date"] = block_time(cast(int, row["block"]))
        if total >= PROGRESS_EVERY and (i % PROGRESS_EVERY == 0 or i == total):
            logger.info("  resolved %d/%d registration timestamps", i, total)


class NoSlot(Exception):
    """Raised when a hotkey has no unclaimed registration left to spend."""


class RegistrationsDb:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    # --- the chain watcher's seam --------------------------------------------
    def publish_snapshot(self, head, metagraph, block_time: Callable[[int], dt.datetime]) -> int:
        """Append a registration row for every uid whose hot/cold pair changed.

        Returns how many rows were written. An unchanged metagraph -- the common case,
        blocks being ~12s apart and registrations rare -- writes nothing. `head` is the
        polled tip, kept for the sink contract; the rows are stamped from the neurons'
        own registration blocks.
        """
        del head  # the sink contract passes it; the rows are stamped from the neurons
        if not metagraph.neurons:
            logger.warning("block %d: empty metagraph, nothing to publish", metagraph.block)
            return 0

        with session_scope(self._sessions) as session:
            latest = self._latest_keys(session)
            rows = changed_rows(latest, metagraph.neurons)
            if not rows:
                logger.info(
                    "block %d: no registration changes (%d neurons)",
                    metagraph.block,
                    len(metagraph.neurons),
                )
                return 0
            scope = "initial load" if not latest else "update"
            resolve_block_dates(rows, block_time, scope=scope)
            # do-nothing, not do-update: a conflict means this uid's block is already
            # recorded, and the history stands.
            session.execute(
                insert(models.Registration)
                .values(rows)
                .on_conflict_do_nothing(index_elements=["uid", "block"])
            )
            logger.info(
                "block %d: recorded %d registration change(s) [%s] for uid(s) %s",
                metagraph.block,
                len(rows),
                scope,
                sorted(cast(int, r["uid"]) for r in rows),
            )
            return len(rows)

    @staticmethod
    def _latest_keys(session: Session) -> dict[int, tuple[str, str]]:
        # Each uid's most recently recorded (hot, cold) pair: DISTINCT ON by descending
        # block keeps the newest row per uid, which is what a snapshot is diffed against.
        stmt = (
            select(
                models.Registration.uid,
                models.Registration.ss58_hot,
                models.Registration.ss58_cold,
            )
            .distinct(models.Registration.uid)
            .order_by(models.Registration.uid, models.Registration.block.desc())
        )
        return {row.uid: (row.ss58_hot, row.ss58_cold) for row in session.execute(stmt)}

    # --- entitlements ---------------------------------------------------------
    def is_registered(self, hotkey: str) -> bool:
        # Whether the subnet has ever carried this hotkey. A miner who has not registered
        # is refused before anything else is checked.
        with session_scope(self._sessions) as session:
            return (
                session.execute(
                    select(models.Registration.id)
                    .where(models.Registration.ss58_hot == hotkey)
                    .limit(1)
                ).first()
                is not None
            )

    def available_slots(self, hotkey: str) -> int:
        # Registrations this hotkey holds that no accepted submission has spent yet.
        with session_scope(self._sessions) as session:
            return _available_slots(session, hotkey)

    def claim_slot(self, session: Session, hotkey: str, submission_id: int) -> int:
        """Spend this hotkey's oldest unclaimed registration on `submission_id`.

        Takes a caller's `session` on purpose: the claim must commit in the same
        transaction as the acceptance it pays for, or a crash between the two would
        either give away a free submission or charge for one that was never recorded.

        FOR UPDATE ... SKIP LOCKED picks a row no concurrent claim is already holding, so
        two simultaneous acceptances take two different registrations instead of racing
        for one. The primary key on registration_id is the backstop if they ever do.
        """
        claimed = select(models.EntitlementClaim.registration_id)
        row = session.execute(
            select(models.Registration.id)
            .where(
                models.Registration.ss58_hot == hotkey,
                models.Registration.id.not_in(claimed),
            )
            .order_by(models.Registration.block, models.Registration.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).first()
        if row is None:
            raise NoSlot(f"{hotkey[:8]}… has no unclaimed registration")
        session.add(models.EntitlementClaim(registration_id=row[0], submission_id=submission_id))
        session.flush()
        return int(row[0])


def _available_slots(session: Session, hotkey: str) -> int:
    # Shared by the API's reservation check and by available_slots(); takes a session so
    # the worker can ask the question inside the transaction that is about to claim.
    claimed = select(models.EntitlementClaim.registration_id)
    return int(
        session.execute(
            select(func.count())
            .select_from(models.Registration)
            .where(
                models.Registration.ss58_hot == hotkey,
                models.Registration.id.not_in(claimed),
            )
        ).scalar_one()
    )
