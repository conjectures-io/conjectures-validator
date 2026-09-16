"""Best/finalized event reconciliation for payout status.

The state names have literal chain boundaries:

* ``PENDING`` — no matching successful chain event;
* ``SUBMITTED`` — a matching event exists on the current best chain;
* ``CONFIRMED`` — that event is in a finalized block.

Best-chain state is re-read on every pass and rolled back to PENDING if its event is reorganized
away.  The finalized cursor advances only after every matching event in a block has committed, so
a restart costs a safe replay and cannot skip a payout.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conjectures_subnet import transfers as chain
from conjectures_subnet.axiom import Severity, get_axiom
from conjectures_subnet.db import async_session_scope
from conjectures_subnet.db import payouts as store
from conjectures_subnet.db.models import PayoutWatchCursor
from payout_watcher.settings import PayoutWatcherSettings, SettingsError

logger = logging.getLogger("payout_watcher")

# The longest a failing streak backs off to.  Five minutes is well inside the window an archive
# budget refills over, and short enough that a watcher which recovered unattended is not sitting
# idle long after the fact.
MAX_BACKOFF_SECONDS = 300.0
# Doubling past this many steps cannot reach further than the ceiling above, and stopping the
# exponent from growing keeps the shift bounded however long an outage lasts.
_BACKOFF_CEILING_STEPS = 16


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


@dataclass
class Scanned:
    finalized_from: int
    finalized_through: int
    finalized_head: int
    best_head: int
    observed: int = 0
    submitted: int = 0
    confirmed: int = 0
    reorged: int = 0
    unmatched: int = 0

    @property
    def finalized_blocks(self) -> int:
        return max(0, self.finalized_through - self.finalized_from + 1)


class PayoutWatcher:
    def __init__(
        self,
        *,
        settings: PayoutWatcherSettings,
        sessions: async_sessionmaker[AsyncSession],
        source: chain.PayoutSource,
        clock: Callable[[], dt.datetime] = _now,
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.source = source
        # Injected so the idle fast-forward's boundary is testable.  That boundary is wall-clock
        # by necessity -- it is a claim about rows that do not exist yet -- so a fake chain alone
        # cannot exercise it.
        self.clock = clock

    async def resolve_cursor(self) -> PayoutWatchCursor | None:
        """Open the durable boundary when the first unresolved payout exists.

        There is deliberately no operator-provided genesis timestamp.  A payout command is not
        rendered until its reward row commits, so the oldest unresolved row's creation time is an
        exact lower bound on every chain event this watcher is responsible for.
        """
        async with async_session_scope(self.sessions) as session:
            existing = await store.cursor(session)
            if existing is not None:
                self._require_same_watch(existing)
                return existing
            watch_from = await store.oldest_unresolved_at(session)
        if watch_from is None:
            return None
        watch_from = _utc(watch_from)

        first = await chain.first_block_at_or_after(self.source, watch_from)
        async with async_session_scope(self.sessions) as session:
            existing = await store.cursor(session)
            if existing is not None:
                self._require_same_watch(existing)
                return existing
            row = await store.open_cursor(
                session,
                network=self.settings.network,
                origin_coldkey=self.settings.origin_coldkey,
                origin_hotkey=self.settings.origin_hotkey,
                netuid=self.settings.netuid,
                watch_from=watch_from,
                start_block=first.number,
                start_block_timestamp=first.timestamp,
            )
            logger.info(
                "opened payout cursor network=%s origin=%s/%s netuid=%d start_block=%d",
                row.network,
                row.origin_coldkey,
                row.origin_hotkey,
                row.netuid,
                row.start_block,
            )
            get_axiom().info(
                source="payout-watcher",
                event_type="cursor_opened",
                network=row.network,
                origin_coldkey=row.origin_coldkey,
                origin_hotkey=row.origin_hotkey,
                netuid=row.netuid,
                watch_from=_utc(row.watch_from).isoformat(),
                start_block=row.start_block,
            )
            return row

    def _require_same_watch(self, cursor: PayoutWatchCursor) -> None:
        differences: list[str] = []
        for field in ("network", "origin_coldkey", "origin_hotkey", "netuid"):
            stored = getattr(cursor, field)
            configured = getattr(self.settings, field)
            if stored != configured:
                differences.append(f"{field} {stored!r} vs {configured!r}")
        if differences:
            raise SettingsError(
                "the stored payout cursor does not match the command renderer: "
                + "; ".join(differences)
                + ". Moving the treasury stake position requires a deliberate cursor migration."
            )

    def _ours(self, observed: chain.ObservedPayout) -> bool:
        return (
            observed.origin_coldkey == self.settings.origin_coldkey
            and observed.origin_hotkey == self.settings.origin_hotkey
            and observed.origin_netuid == self.settings.netuid
            and observed.destination_netuid == self.settings.netuid
        )

    async def scan_once(self) -> Scanned | None:
        """One pass: read finalized blocks, record what left the treasury, then match.

        Reading is no longer conditional on an obligation being outstanding.  It used to be, and
        the cursor only advanced inside a scan, so an idle watcher fell a day behind finality for
        every quiet day and a payout made in that gap was never read at all.  Recording first and
        matching second is what makes that gap harmless -- and unnecessary.
        """
        async with async_session_scope(self.sessions) as session:
            cursor = await store.cursor(session)
        if cursor is None:
            cursor = await self.resolve_cursor()
            if cursor is None:
                # No cursor and nothing to open one from: no payout has ever been owed, so there
                # is no boundary this watcher could honestly claim to have read from.
                return None
        self._require_same_watch(cursor)

        finalized_head = await self.source.finalized_head()
        first = cursor.last_scanned_block + 1
        last = min(finalized_head, first + self.settings.batch_blocks - 1)
        scanned = Scanned(
            finalized_from=first,
            finalized_through=last,
            finalized_head=finalized_head,
            best_head=finalized_head,
        )

        for number in range(first, last + 1):
            await self._scan_finalized_block(number, scanned)

        # Only inspect the best-chain tail after the finalized cursor is caught up.  Otherwise a
        # payout already in an older finalized block could be presented as merely SUBMITTED while
        # the cursor works through its backlog.
        if last >= finalized_head:
            await self._reconcile_recent(scanned)
            await self._scan_best_tail(scanned)
        return scanned

    async def _scan_finalized_block(self, number: int, scanned: Scanned) -> None:
        observed_events = await self.source.payouts_in(block=number)
        for observed in observed_events:
            if not self._ours(observed):
                continue
            scanned.observed += 1
            # Record first, and commit that before anything tries to interpret it.  Separate
            # transactions on purpose: a conflict raised while matching must not roll back the
            # record of what the chain actually did.  Re-recording is free -- `record_payout` is
            # idempotent on the reference -- so a crash between the two costs a replay, not a fact.
            async with async_session_scope(self.sessions) as session:
                payout_id = (await store.record_payout(session, observed)).id
            async with async_session_scope(self.sessions) as session:
                update = await store.claim_payout(session, payout_id)
            if update is None:
                scanned.unmatched += 1
                self._log_unmatched(observed, finalized=True)
            elif update.changed:
                scanned.confirmed += 1
                logger.info(
                    "confirmed reward event %d from finalized chain event %s",
                    update.reward_event_id,
                    observed.reference,
                )
                get_axiom().info(
                    source="payout-watcher",
                    event_type="payout_confirmed",
                    reward_event_id=update.reward_event_id,
                    submission_id=str(update.submission_id),
                    extrinsic_reference=observed.reference,
                    block=observed.block,
                    amount_rao=observed.amount_rao,
                    destination_coldkey=observed.destination_coldkey,
                    destination_hotkey=observed.destination_hotkey,
                )

        # Cursor last, and in its own transaction.  A failure above leaves this block unread so
        # every event is retried; a crash after event commit but before this write is an idempotent
        # replay through the event reference.
        async with async_session_scope(self.sessions) as session:
            await store.advance_cursor(
                session, through_block=number, now=self.clock()
            )

    async def _reconcile_recent(self, scanned: Scanned) -> None:
        """Retry payouts recorded before the obligation that pays them existed.

        The narrow race this closes: a payout is observed, matches nothing, and the notifier
        seeds its obligation moments later.  Without a retry that payout would need an operator
        to bind it even though automatic matching was about to become possible.

        Deliberately bounded to `CHAIN_CLOCK_TOLERANCE`, which is not an optimisation but the
        exact reach of automatic matching.  A payout older than that can never be claimed
        automatically -- every obligation written from now on has `created_at >= now`, and
        `_oldest_match` refuses `created_at > block_timestamp + tolerance` -- so retrying one
        would be asking the same question forever and always getting the same answer.
        """
        async with async_session_scope(self.sessions) as session:
            pending = await store.unclaimed_payouts(
                session, claimable_since=self.clock() - store.CHAIN_CLOCK_TOLERANCE
            )
        for item in pending:
            async with async_session_scope(self.sessions) as session:
                update = await store.claim_payout(session, item.id)
            if update is not None and update.changed:
                scanned.confirmed += 1
                logger.info(
                    "confirmed reward event %d from previously unclaimed payout %s",
                    update.reward_event_id,
                    item.extrinsic_reference,
                )
                get_axiom().info(
                    source="payout-watcher",
                    event_type="payout_confirmed",
                    reward_event_id=update.reward_event_id,
                    submission_id=str(update.submission_id),
                    extrinsic_reference=item.extrinsic_reference,
                    block=item.block,
                    amount_rao=item.amount_rao,
                    destination_coldkey=item.destination_coldkey,
                    destination_hotkey=item.destination_hotkey,
                )

    async def _scan_best_tail(self, scanned: Scanned) -> None:
        best_head = max(scanned.finalized_head, await self.source.best_head())
        scanned.best_head = best_head
        seen: set[str] = set()
        for number in range(scanned.finalized_head + 1, best_head + 1):
            for observed in await self.source.payouts_in(block=number):
                if not self._ours(observed):
                    continue
                seen.add(observed.reference)
                scanned.observed += 1
                async with async_session_scope(self.sessions) as session:
                    update = await store.mark_submitted(session, observed)
                if update is None:
                    scanned.unmatched += 1
                    self._log_unmatched(observed, finalized=False)
                elif update.changed:
                    scanned.submitted += 1
                    logger.info(
                        "submitted reward event %d observed at best-chain event %s",
                        update.reward_event_id,
                        observed.reference,
                    )

        # SUBMITTED is explicitly best-chain state.  If its reference is absent from the complete
        # unfinalized tail, the block was reorganized and the site must return to Approved/Pending.
        async with async_session_scope(self.sessions) as session:
            submitted = await store.submitted_after(session, scanned.finalized_head)
        for item in submitted:
            if item.reference in seen:
                continue
            async with async_session_scope(self.sessions) as session:
                changed = await store.revert_submitted(
                    session, item, now=self.clock()
                )
            if changed:
                scanned.reorged += 1
                logger.warning(
                    "best-chain payout %s disappeared; reward event %d is pending again",
                    item.reference,
                    item.reward_event_id,
                )
                get_axiom().warn(
                    source="payout-watcher",
                    event_type="payout_reorged",
                    reward_event_id=item.reward_event_id,
                    extrinsic_reference=item.reference,
                    submitted_block=item.submitted_block,
                    finalized_head=scanned.finalized_head,
                    best_head=best_head,
                )

    def _log_unmatched(
        self, observed: chain.ObservedPayout, *, finalized: bool
    ) -> None:
        logger.warning(
            "%s treasury payout %s to %s/%s amount=%d matches no pending reward",
            "finalized" if finalized else "best-chain",
            observed.reference,
            observed.destination_coldkey,
            observed.destination_hotkey,
            observed.amount_rao,
        )
        get_axiom().warn(
            source="payout-watcher",
            event_type="payout_unmatched",
            finalized=finalized,
            extrinsic_reference=observed.reference,
            block=observed.block,
            amount_rao=observed.amount_rao,
            destination_coldkey=observed.destination_coldkey,
            destination_hotkey=observed.destination_hotkey,
        )

    async def run_forever(self, *, stop: asyncio.Event | None = None) -> None:
        halt = stop or asyncio.Event()
        # Consecutive failed passes, which decides how long to wait before the next one.  A
        # failing scan is usually a chain read that was refused, and the most common refusal is
        # an archive endpoint's historical-work budget.  Retrying that on the ordinary poll
        # interval spends the budget on rejections and can hold it exhausted indefinitely, so
        # each failure in a row waits longer, up to `MAX_BACKOFF_SECONDS`.  One success resets
        # it: the very next pass after recovery runs at full speed.
        failures = 0
        while not halt.is_set():
            try:
                scanned = await self.scan_once()
            except asyncio.CancelledError:
                raise
            except SettingsError:
                raise
            except Exception:
                failures += 1
                logger.exception(
                    "payout scan failed; finalized cursor did not skip failed work "
                    "(consecutive failures=%d)",
                    failures,
                )
                get_axiom().exception(
                    source="payout-watcher",
                    event_type="unexpected_error",
                    severity=Severity.WARNING,
                    watcher_id=self.settings.watcher_id,
                    consecutive_failures=failures,
                )
                scanned = None
            else:
                failures = 0
            if scanned is not None:
                self._log_pass(scanned)
                if scanned.finalized_blocks >= self.settings.batch_blocks:
                    continue
            try:
                await asyncio.wait_for(halt.wait(), self._delay(failures))
            except TimeoutError:
                pass

    def _delay(self, failures: int) -> float:
        """How long to wait before the next pass, given the failing streak behind this one."""
        if failures == 0:
            return self.settings.poll_seconds
        # Doubling from the poll interval, so a healthy watcher's first hiccup costs nothing
        # noticeable and a sustained outage settles at one attempt per `MAX_BACKOFF_SECONDS`.
        backoff = self.settings.poll_seconds * 2 ** min(failures - 1, _BACKOFF_CEILING_STEPS)
        return min(backoff, MAX_BACKOFF_SECONDS)

    def _log_pass(self, scanned: Scanned) -> None:
        active = scanned.observed or scanned.submitted or scanned.confirmed or scanned.reorged
        logger.log(
            logging.INFO if active else logging.DEBUG,
            "payout scan finalized=%d-%d/%d best=%d observed=%d submitted=%d confirmed=%d "
            "reorged=%d unmatched=%d",
            scanned.finalized_from,
            scanned.finalized_through,
            scanned.finalized_head,
            scanned.best_head,
            scanned.observed,
            scanned.submitted,
            scanned.confirmed,
            scanned.reorged,
            scanned.unmatched,
        )
        get_axiom().emit(
            severity=Severity.INFO if active else Severity.DEBUG,
            source="payout-watcher",
            event_type="blocks_scanned",
            finalized_from=scanned.finalized_from,
            finalized_through=scanned.finalized_through,
            finalized_head=scanned.finalized_head,
            best_head=scanned.best_head,
            observed=scanned.observed,
            submitted=scanned.submitted,
            confirmed=scanned.confirmed,
            reorged=scanned.reorged,
            unmatched=scanned.unmatched,
            watcher_id=self.settings.watcher_id,
        )


def _utc(value: dt.datetime) -> dt.datetime:
    return value.replace(tzinfo=dt.UTC) if value.tzinfo is None else value.astimezone(dt.UTC)


__all__ = ["PayoutWatcher", "Scanned"]

