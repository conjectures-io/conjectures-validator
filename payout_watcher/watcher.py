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
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from conjectures_subnet import transfers as chain
from conjectures_subnet.axiom import Severity, get_axiom
from conjectures_subnet.db import async_session_scope
from conjectures_subnet.db import payouts as store
from conjectures_subnet.db.models import PayoutWatchCursor
from payout_watcher.settings import PayoutWatcherSettings, SettingsError

logger = logging.getLogger("payout_watcher")


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
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.source = source

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
        async with async_session_scope(self.sessions) as session:
            unresolved = await store.oldest_unresolved_at(session)
            cursor = await store.cursor(session)
        if unresolved is None:
            return None
        if cursor is None:
            cursor = await self.resolve_cursor()
            if cursor is None:  # pragma: no cover - unresolved was just observed
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
            await self._scan_best_tail(scanned)
        return scanned

    async def _scan_finalized_block(self, number: int, scanned: Scanned) -> None:
        observed_events = await self.source.payouts_in(block=number)
        for observed in observed_events:
            if not self._ours(observed):
                continue
            scanned.observed += 1
            async with async_session_scope(self.sessions) as session:
                update = await store.confirm(session, observed)
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
                session, through_block=number, now=dt.datetime.now(dt.UTC)
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
                    session, item, now=dt.datetime.now(dt.UTC)
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
        while not halt.is_set():
            try:
                scanned = await self.scan_once()
            except asyncio.CancelledError:
                raise
            except SettingsError:
                raise
            except Exception:
                logger.exception("payout scan failed; finalized cursor did not skip failed work")
                get_axiom().exception(
                    source="payout-watcher",
                    event_type="unexpected_error",
                    severity=Severity.WARNING,
                    watcher_id=self.settings.watcher_id,
                )
                scanned = None
            if scanned is not None:
                self._log_pass(scanned)
                if scanned.finalized_blocks >= self.settings.batch_blocks:
                    continue
            try:
                await asyncio.wait_for(halt.wait(), self.settings.poll_seconds)
            except TimeoutError:
                pass

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

