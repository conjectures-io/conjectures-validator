"""The loop: read finalized blocks forwards, record every arrival, credit the ones it can attribute.

One transaction per arrival, then one for the cursor. That is what makes the whole thing
restartable: a crash leaves the cursor at the last block whose transfers are durably recorded, and
the next pass re-reads from there. Re-reading is free — `transfers.record` is idempotent on the
extrinsic reference, and `credits.credit_deposit` refuses a second entry for one transfer through
two separate unique indexes.

The order is deliberate and is the safety property of this module:

    record the transfer  ->  attribute it  ->  advance the cursor

Recording first means an arrival exists in the database before anything decides what it is worth.
Advancing last means a block is only ever marked read once its arrivals are stored. Reversing
either would create a window in which money has arrived, the watcher has moved past it, and no row
anywhere says so.

Per *arrival* and not per *block*, for a reason worth stating plainly: `credits.credit_deposit`
rolls its session back when an insert conflicts, and a rollback discards everything the
transaction holds. One transaction per block therefore meant a conflict on the second arrival
threw away the first — and the cursor then advanced past both. `_scan_block` has the detail.

**Attribution never guesses.** A transfer whose sending coldkey belongs to no account is recorded
`UNATTRIBUTED` with the reason written on it, and it stays there for a human. Crediting a
best-effort match would mean handing one account's money to another on a coincidence.

The Axiom events here follow the same order, and are per *arrival* rather than per pass for the
same reason the transactions are: `transfer_credited`, `transfer_unattributed`, `transfer_ignored`
and `transfer_conflict` are emitted only after the transaction that decided them committed, so the
dataset never claims a credit that was rolled back. `blocks_scanned` reports the pass itself, and
is `debug` on a quiet range for the reason `_log_pass` gives — at one line per twelve seconds the
empty ones would bury the rest.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conjectures_subnet import transfers as chain
from conjectures_subnet.axiom import Severity, get_axiom
from conjectures_subnet.db import transfers as store
from conjectures_subnet.db.engine import async_session_scope
from conjectures_subnet.db.errors import RecordConflict
from conjectures_subnet.db.models import (
    ChainTransfer,
    ChainTransferState,
    ChainWatchCursor,
)
from deposit_watcher.settings import SettingsError, WatcherSettings

logger = logging.getLogger("deposit_watcher")

# Written onto every ledger entry this worker produces, so a balance can be traced to the process
# that moved it rather than to an anonymous 'system'.
LEDGER_ACTOR = "deposit-watcher"

# Recorded on a transfer nobody owns. Read by an operator working the unattributed queue, so it
# says which lookup failed rather than just that one did.
UNKNOWN_SENDER = (
    "no account owns this sending coldkey: it is not a linked wallet and not a unique payout "
    "coldkey. Attribute it by hand, or have the sender claim it."
)
# The validator moving its own funds is not a purchase.
SELF_TRANSFER = "the sender is the watched address itself; this is not an incoming payment"
# Prefixed onto the note of an arrival whose settlement conflicted with durable state, so the
# operator queue distinguishes "nobody owns this" from "something already claims it".
CONFLICT_NOTE = "settlement conflicted with existing state:"


@dataclass(frozen=True)
class Admitted:
    """What became of one arrival, once its transaction committed.

    Returned rather than counted in place, because a transaction that rolls back must not leave a
    credit reported in the log that no ledger entry backs.
    """

    recorded: bool = False  # this pass created the row, rather than re-reading it
    credited: bool = False
    credits_granted: int = 0
    unattributed: bool = False
    ignored: bool = False
    # Set when settlement conflicted with durable state. The row still exists and still holds the
    # money; what failed is the decision about it.
    conflict: str | None = None

    def apply(self, scanned: Scanned) -> None:
        scanned.recorded += int(self.recorded)
        scanned.credited += int(self.credited)
        scanned.credits_granted += self.credits_granted
        scanned.unattributed += int(self.unattributed)
        scanned.ignored += int(self.ignored)
        if self.conflict is not None:
            scanned.errors.append(self.conflict)


@dataclass
class Scanned:
    """What one pass did, for the log and for tests."""

    from_block: int
    through_block: int
    observed: int = 0  # transfer events seen, including ones already recorded
    recorded: int = 0  # rows this pass created
    credited: int = 0  # transfers attributed and credited
    credits_granted: int = 0  # whole credits those transfers bought
    unattributed: int = 0
    ignored: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def blocks(self) -> int:
        return max(0, self.through_block - self.from_block + 1)


class DepositWatcher:
    def __init__(
        self,
        *,
        settings: WatcherSettings,
        sessions: async_sessionmaker[AsyncSession],
        source: chain.TransferSource,
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.source = source

    # --- startup ------------------------------------------------------------------------

    async def verify_identity(self) -> str:
        """Confirm the configured address is the hotkey registered at the configured uid.

        The one check that the money being watched is this validator's own. A mistyped address
        does not fail — it produces a watcher that runs forever, reads real blocks, and credits
        nothing, which looks exactly like "no customers today".

        Skippable outside production only, and only because a local Subtensor has no subnet to
        answer about. `WatcherSettings` refuses to skip it in production.
        """
        registered = await self.source.hotkey_at(
            netuid=self.settings.netuid, uid=self.settings.uid
        )
        if registered is None:
            raise SettingsError(
                f"netuid {self.settings.netuid} uid {self.settings.uid} has no registered "
                "hotkey; DEPOSIT_WATCH_NETUID/DEPOSIT_WATCH_UID do not name this validator"
            )
        if registered != self.settings.recipient:
            raise SettingsError(
                f"netuid {self.settings.netuid} uid {self.settings.uid} is {registered}, not the "
                f"configured DEPOSIT_WATCH_RECIPIENT_SS58 {self.settings.recipient}; refusing to "
                "watch an address that is not this validator's own"
            )
        return registered

    async def resolve_cursor(self) -> ChainWatchCursor:
        """Load the cursor, or create it by resolving the genesis timestamp to a block.

        The bisection runs at most once in the lifetime of a deployment. Once `start_block` is
        recorded it is never recomputed: a second search against a re-synced node could land
        either side of the same boundary, which would change which transfers the watcher believes
        exist at all.

        An existing cursor that disagrees with the environment is a refusal, not a migration. All
        four of the compared values decide whose money this is, and quietly adopting a new one
        would either abandon the old address's arrivals or re-scan a range that was never meant to
        buy credits.
        """
        async with async_session_scope(self.sessions) as session:
            existing = await store.cursor(session)
            if existing is not None:
                self._require_same_watch(existing)
                return existing

        # Outside the transaction: the bisection is a few dozen chain reads and holding a
        # transaction open across them would keep one for minutes.
        genesis = await chain.first_block_at_or_after(
            self.source, self.settings.watch_from
        )
        async with async_session_scope(self.sessions) as session:
            # Re-read under the write: two watchers starting together must not both bisect and
            # both insert. The second one finds the first one's row and validates against it.
            existing = await store.cursor(session)
            if existing is not None:
                self._require_same_watch(existing)
                return existing
            cursor = await store.open_cursor(
                session,
                recipient=self.settings.recipient,
                netuid=self.settings.netuid,
                uid=self.settings.uid,
                watch_from=self.settings.watch_from,
                start_block=genesis.number,
                start_block_timestamp=genesis.timestamp,
            )
            logger.info(
                "opened cursor recipient=%s netuid=%d uid=%d watch_from=%s "
                "start_block=%d (%s)",
                cursor.recipient,
                cursor.netuid,
                cursor.uid,
                cursor.watch_from.isoformat(),
                cursor.start_block,
                cursor.start_block_timestamp.isoformat(),
            )
            # Once in the lifetime of a deployment, and it fixes which transfers can ever buy
            # credits — so it is worth a row that outlives the container that wrote it.
            get_axiom().info(
                source="deposit-watcher",
                event_type="cursor_opened",
                recipient=cursor.recipient,
                netuid=cursor.netuid,
                uid=cursor.uid,
                watch_from=cursor.watch_from.isoformat(),
                start_block=cursor.start_block,
                start_block_timestamp=cursor.start_block_timestamp.isoformat(),
            )
            return cursor

    def _require_same_watch(self, cursor: ChainWatchCursor) -> None:
        differences = []
        if cursor.recipient != self.settings.recipient:
            differences.append(
                f"recipient {cursor.recipient} vs {self.settings.recipient}"
            )
        if cursor.netuid != self.settings.netuid:
            differences.append(f"netuid {cursor.netuid} vs {self.settings.netuid}")
        if cursor.uid != self.settings.uid:
            differences.append(f"uid {cursor.uid} vs {self.settings.uid}")
        if _to_utc(cursor.watch_from) != self.settings.watch_from:
            differences.append(
                f"watch_from {_to_utc(cursor.watch_from).isoformat()} vs "
                f"{self.settings.watch_from.isoformat()}"
            )
        if not differences:
            return
        raise SettingsError(
            "the stored cursor does not match this configuration: "
            + "; ".join(differences)
            + ". Change one or the other deliberately: a new address or an earlier genesis "
            "timestamp needs a new cursor row, and creating one is an operator decision "
            "because it decides which transfers buy credits."
        )

    # --- the loop -----------------------------------------------------------------------

    async def scan_once(self) -> Scanned | None:
        """Read the next batch of finalized blocks. None means there is nothing new yet."""
        async with async_session_scope(self.sessions) as session:
            cursor = await store.cursor(session)
        if cursor is None:  # pragma: no cover - resolve_cursor runs before the loop
            raise SettingsError("the watcher has no cursor; call resolve_cursor first")

        head = await self.source.finalized_head()
        first = cursor.last_scanned_block + 1
        if first > head:
            return None
        last = min(head, first + self.settings.batch_blocks - 1)

        scanned = Scanned(from_block=first, through_block=last)
        for number in range(first, last + 1):
            await self._scan_block(number, scanned)
        return scanned

    async def _scan_block(self, number: int, scanned: Scanned) -> None:
        """One block: a transaction per arrival, then the cursor.

        ONE TRANSACTION PER ARRIVAL, not one per block. `credits.credit_deposit` rolls its session
        back when an insert conflicts — a deposit the API's claim endpoint already attached this
        extrinsic reference to would do it — and a rollback discards everything the transaction
        holds. Sharing one transaction across a block therefore meant a conflict on the second
        arrival threw away the first, after which the cursor advanced past both. That is money
        arriving, being discarded, and never being read again. Per-arrival transactions bound a
        rollback to the arrival that caused it.

        The cursor still advances last, in its own transaction, so a block is only ever marked
        read once its arrivals are durably recorded. A crash in between costs a re-read, which
        `store.record` absorbs.

        A chain read failure leaves the cursor where it was and the block is retried on the next
        pass, so a node that answers badly costs a delay and never a missed transfer.
        """
        arrivals = await self.source.transfers_to(
            recipient=self.settings.recipient, block=number
        )
        for arrival in arrivals:
            scanned.observed += 1
            # Counted only from committed work, so a rolled-back settlement is never reported as
            # a credit.
            (await self._admit(arrival)).apply(scanned)

        async with async_session_scope(self.sessions) as session:
            await store.advance_cursor(
                session, through_block=number, now=dt.datetime.now(dt.UTC)
            )

    async def _admit(self, arrival: chain.IncomingTransfer) -> Admitted:
        """Record one arrival, then decide what it is worth. Two transactions, in that order.

        THE RECORD IS ITS OWN TRANSACTION. `credits.credit_deposit` rolls its session back on an
        insert conflict, and a rollback discards everything the transaction holds — including this
        arrival's own row, if the two shared one. The arrival would then be money that arrived,
        was discarded, and was never read again, because the cursor advances regardless.

        Committing the record first makes the module's stated order literally true: the arrival
        exists in the database before anything decides what it is worth, so the worst a failed
        settlement can do is leave the row `UNATTRIBUTED` with the reason on it — which is the
        operator's queue, exactly where an arrival nobody could settle belongs.
        """
        async with async_session_scope(self.sessions) as session:
            recorded = await store.record(
                session,
                extrinsic_reference=arrival.reference,
                block=arrival.block,
                block_timestamp=arrival.block_timestamp,
                extrinsic_index=arrival.extrinsic_index,
                event_index=arrival.event_index,
                sender_coldkey=arrival.sender,
                recipient=arrival.recipient,
                amount_rao=arrival.amount_rao,
            )
            created = recorded.created
            transfer_id = recorded.transfer.id
            settled = recorded.transfer.status is not ChainTransferState.UNATTRIBUTED

        if settled:
            # Already decided on an earlier pass — the normal case for a re-read.
            return Admitted(recorded=created)

        try:
            async with async_session_scope(self.sessions) as session:
                return await self._settle(
                    session, transfer_id, arrival, recorded=created
                )
        except RecordConflict as exc:
            # Another watcher got there first, or a deposit already holds this extrinsic
            # reference — the API's claim endpoint attaches one before any credit is issued. The
            # row survives, because it was committed above; the reason goes onto it so the
            # operator working the queue does not have to guess.
            logger.info(
                "transfer %s could not be settled: %s", arrival.reference, exc.message
            )
            # `warning`: money has arrived that nothing could decide, and it now sits in the
            # operator queue. Not an error — the row survived and the design says this is where
            # such an arrival belongs — but not a routine info either.
            get_axiom().warn(
                source="deposit-watcher",
                event_type="transfer_conflict",
                extrinsic_reference=arrival.reference,
                block=arrival.block,
                amount_rao=arrival.amount_rao,
                sender_coldkey=arrival.sender,
                conflict=exc.message,
            )
            async with async_session_scope(self.sessions) as session:
                transfer = await session.get(ChainTransfer, transfer_id)
                if transfer is not None:
                    await store.leave_unattributed(
                        session, transfer, note=f"{CONFLICT_NOTE} {exc.message}"
                    )
            return Admitted(
                recorded=created,
                unattributed=True,
                conflict=f"{arrival.reference}: {exc.message}",
            )

    async def _settle(
        self,
        session: AsyncSession,
        transfer_id: int,
        arrival: chain.IncomingTransfer,
        *,
        recorded: bool,
    ) -> Admitted:
        """Decide what one already-recorded arrival is worth.

        The row is taken `FOR UPDATE`. That is not about two watchers — there is meant to be one —
        but about the API: `db.transfers.claim_for_submission` locks the same row when a miner cites
        this transfer to fund a submission directly. Without the lock both could read
        `UNATTRIBUTED` and one payment would buy both an attempt and a credit.
        """
        transfer = await session.get(ChainTransfer, transfer_id, with_for_update=True)
        if transfer is None:  # pragma: no cover - it was committed a moment ago
            raise RecordConflict("the recorded transfer disappeared")
        if transfer.status is not ChainTransferState.UNATTRIBUTED:
            # Settled while we waited for the lock — by the API funding a submission with it, or by
            # another watcher. Either way it is spent, and not by us.
            logger.info(
                "transfer %s was settled as %s before this pass could credit it",
                arrival.reference,
                transfer.status,
            )
            return Admitted(recorded=recorded)

        if arrival.sender == arrival.recipient:
            await store.ignore(session, transfer, note=SELF_TRANSFER)
            get_axiom().info(
                source="deposit-watcher",
                event_type="transfer_ignored",
                extrinsic_reference=arrival.reference,
                block=arrival.block,
                amount_rao=arrival.amount_rao,
                reason="self_transfer",
            )
            return Admitted(recorded=recorded, ignored=True)

        account_id = await store.account_for_coldkey(session, arrival.sender)
        if account_id is None:
            await store.leave_unattributed(session, transfer, note=UNKNOWN_SENDER)
            logger.info(
                "transfer %s of %d rao from %s belongs to no account; left unattributed",
                arrival.reference,
                arrival.amount_rao,
                arrival.sender,
            )
            # `warning` rather than `info`, unlike the log line above. Someone has sent this
            # validator money it cannot attribute, and every one of these is a person waiting for
            # credits that will not arrive until an operator works the queue.
            get_axiom().warn(
                source="deposit-watcher",
                event_type="transfer_unattributed",
                extrinsic_reference=arrival.reference,
                block=arrival.block,
                amount_rao=arrival.amount_rao,
                sender_coldkey=arrival.sender,
                reason="unknown_sender",
            )
            return Admitted(recorded=recorded, unattributed=True)

        await store.credit(
            session,
            transfer,
            account_id=account_id,
            credit_price_rao=self.settings.credit_price_rao,
            deposit_expires_at=dt.datetime.now(dt.UTC)
            + self.settings.adopted_deposit_window,
            created_by=LEDGER_ACTOR,
            bonus_schedule=self.settings.bonus_schedule,
        )
        logger.info(
            "credited transfer %s: %d rao from %s to account %s = %d credit(s) "
            "(%d rao towards the next)",
            arrival.reference,
            arrival.amount_rao,
            arrival.sender,
            account_id,
            transfer.credits_granted,
            arrival.amount_rao % self.settings.credit_price_rao,
        )
        get_axiom().info(
            source="deposit-watcher",
            event_type="transfer_credited",
            extrinsic_reference=arrival.reference,
            block=arrival.block,
            amount_rao=arrival.amount_rao,
            sender_coldkey=arrival.sender,
            account_id=str(account_id),
            credits_granted=transfer.credits_granted,
            # Integer arithmetic all the way, as docs/SUBNET.md requires of amounts: this is the
            # remainder in rao, never a fractional credit.
            remainder_rao=arrival.amount_rao % self.settings.credit_price_rao,
            credit_price_rao=self.settings.credit_price_rao,
        )
        return Admitted(
            recorded=recorded, credited=True, credits_granted=transfer.credits_granted
        )

    async def catch_up(self, *, max_passes: int | None = None) -> list[Scanned]:
        """Read forwards until the cursor reaches the finalized head. For `--once` and tests."""
        passes: list[Scanned] = []
        while max_passes is None or len(passes) < max_passes:
            scanned = await self.scan_once()
            if scanned is None:
                return passes
            passes.append(scanned)
            self._log_pass(scanned)
        return passes

    async def run_forever(self, *, stop: asyncio.Event | None = None) -> None:
        """Poll until asked to stop.

        A failed pass is logged and retried. It must never end the worker: the cursor did not
        move, so the same blocks are read again, and the alternative — exiting — is a validator
        that stops crediting deposits until somebody notices.
        """
        halt = stop or asyncio.Event()
        while not halt.is_set():
            try:
                scanned = await self.scan_once()
            except asyncio.CancelledError:
                raise
            except SettingsError:
                # Configuration, not the chain. Retrying cannot fix it.
                raise
            except Exception:
                logger.exception("scan pass failed; the cursor did not move, retrying")
                # `warning`, not `error`: a chain read that fails is expected of a node that is
                # syncing or briefly unreachable, the cursor did not move, and the same blocks are
                # read again next pass. What would be an error is this never stopping, which is a
                # rate on this event rather than any single one of them.
                get_axiom().exception(
                    source="deposit-watcher",
                    event_type="unexpected_error",
                    severity=Severity.WARNING,
                    watcher_id=self.settings.watcher_id,
                )
                scanned = None
            if scanned is not None:
                self._log_pass(scanned)
                # Still behind the head, so go straight round rather than sleeping through a
                # backlog one batch at a time.
                if scanned.blocks >= self.settings.batch_blocks:
                    continue
            try:
                await asyncio.wait_for(halt.wait(), self.settings.poll_seconds)
            except TimeoutError:
                pass

    def _log_pass(self, scanned: Scanned) -> None:
        # Quiet on the overwhelmingly common case: a block with no transfers into the treasury is
        # not worth a line, and at one line per twelve seconds it would bury the ones that are.
        level = logging.INFO if scanned.observed else logging.DEBUG
        logger.log(
            level,
            "scanned blocks %d-%d: observed=%d recorded=%d credited=%d credits=%d "
            "unattributed=%d ignored=%d",
            scanned.from_block,
            scanned.through_block,
            scanned.observed,
            scanned.recorded,
            scanned.credited,
            scanned.credits_granted,
            scanned.unattributed,
            scanned.ignored,
        )
        # Same severity rule as the log line, for the same reason — and it is why the bridge is not
        # left to forward this one: a `debug`-severity event on an empty range keeps "is the watcher
        # keeping up with the head" queryable without an event per twelve seconds at `info`.
        get_axiom().emit(
            severity=Severity.INFO if scanned.observed else Severity.DEBUG,
            source="deposit-watcher",
            event_type="blocks_scanned",
            from_block=scanned.from_block,
            through_block=scanned.through_block,
            blocks=scanned.blocks,
            observed=scanned.observed,
            recorded=scanned.recorded,
            credited=scanned.credited,
            credits_granted=scanned.credits_granted,
            unattributed=scanned.unattributed,
            ignored=scanned.ignored,
            errors=len(scanned.errors),
            watcher_id=self.settings.watcher_id,
        )


def _to_utc(value: dt.datetime) -> dt.datetime:
    """A stored timestamp as an aware UTC instant.

    psycopg returns TIMESTAMPTZ aware, but `Base.metadata.create_all()` under SQLite in a future
    test would not — and comparing an aware configured instant to a naive stored one raises rather
    than reporting a mismatch, which would turn a real misconfiguration into a crash that says
    nothing about it.
    """
    if value.tzinfo is None:  # pragma: no cover - psycopg returns aware timestamps
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


__all__ = [
    "CONFLICT_NOTE",
    "LEDGER_ACTOR",
    "SELF_TRANSFER",
    "UNKNOWN_SENDER",
    "Admitted",
    "DepositWatcher",
    "Scanned",
]
