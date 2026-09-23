"""The registration watcher: the process without which nobody can submit to a competition.

The competition API admits a hotkey only if the competition database has a registration row
for it, and this watcher is the only thing that writes one. So the properties worth pinning
are the ones whose failure is a miner who paid and is refused: every uid recorded on the
first pass, a re-registration appended rather than missed, and a pass that fails part way
retried rather than skipped.

The chain is a fake here; the database is the real competition schema. The live source is
tested against a fake client whose answers have the shapes Finney actually returned for
netuid 66 -- integer uids, SS58 strings -- which were checked against the chain when this
was written.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import time
from pathlib import Path
from typing import Any

import pytest
from conftest import COMPETITION_SKIP_REASON, competition_dsn

from conjectures_subnet.competition import connect
from conjectures_subnet.transfers import ChainUnavailable
from registration_watcher import healthcheck
from registration_watcher.settings import NETUID, RegistrationWatcherSettings, SettingsError
from registration_watcher.source import (
    BittensorRegistrationSource,
    Neuron,
    Snapshot,
)
from registration_watcher.watcher import RegistrationWatcher

needs_db = pytest.mark.skipif(competition_dsn() is None, reason=COMPETITION_SKIP_REASON)

T0 = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)


def run(coro):
    return asyncio.run(coro)


def _neuron(uid: int, hot: str, *, cold: str | None = None, at: int = 100) -> Neuron:
    return Neuron(uid=uid, hotkey=hot, coldkey=cold or f"cold-{hot}", block_at_registration=at)


class FakeChain:
    """A subnet you script: set the finalized head and who holds each uid."""

    def __init__(self) -> None:
        self.head = 1_000
        self.neurons: dict[int, Neuron] = {}
        self.snapshots_read = 0
        self.times_asked: list[int] = []
        self.fail_block_time = False
        self.fail_head = False

    def register(self, neuron: Neuron) -> None:
        self.neurons[neuron.uid] = neuron

    async def finalized_head(self) -> int:
        if self.fail_head:
            raise ChainUnavailable("node went away")
        return self.head

    async def snapshot(self, netuid: int, *, block: int) -> Snapshot:
        assert netuid == NETUID
        self.snapshots_read += 1
        return Snapshot(
            netuid=netuid, block=block, neurons=tuple(self.neurons[u] for u in sorted(self.neurons))
        )

    async def block_time(self, block: int) -> dt.datetime:
        if self.fail_block_time:
            raise ChainUnavailable("archive timed out")
        self.times_asked.append(block)
        return T0 + dt.timedelta(seconds=12 * block)

    async def close(self) -> None:
        pass


@pytest.fixture
def store():
    from sqlalchemy import text

    opened = connect(competition_dsn())
    with opened.engine.begin() as conn:
        for table in ("entitlement_claims", "submissions", "registrations"):
            conn.execute(text(f"DELETE FROM {table}"))
    try:
        yield opened
    finally:
        opened.close()


def _rows(store) -> list[tuple[int, str, str, int, dt.datetime]]:
    from sqlalchemy import text

    with store.engine.connect() as conn:
        return [
            tuple(r)
            for r in conn.execute(
                text(
                    "SELECT uid, ss58_hot, ss58_cold, block, block_date "
                    "FROM registrations ORDER BY uid, block"
                )
            )
        ]


def _watcher(chain: FakeChain, store, **kwargs: Any) -> RegistrationWatcher:
    return RegistrationWatcher(
        source=chain, registrations=store.registrations, netuid=NETUID, poll_seconds=0.01, **kwargs
    )


# ── the watcher against a real schema ──────────────────────────────────────


@needs_db
def test_the_first_pass_records_every_uid_with_its_registration_time(store):
    chain = FakeChain()
    chain.register(_neuron(0, "5Alice", at=100))
    chain.register(_neuron(1, "5Bob", at=250))
    watcher = _watcher(chain, store)

    assert run(watcher.step()) == 2
    assert _rows(store) == [
        (0, "5Alice", "cold-5Alice", 100, T0 + dt.timedelta(seconds=1_200)),
        (1, "5Bob", "cold-5Bob", 250, T0 + dt.timedelta(seconds=3_000)),
    ]
    # And that is what admits a miner: the store the API asks now says yes.
    assert store.registrations.is_registered("5Alice")
    assert store.registrations.available_slots("5Alice") == 1


@needs_db
def test_a_head_that_has_not_advanced_reads_nothing(store):
    chain = FakeChain()
    chain.register(_neuron(0, "5Alice"))
    watcher = _watcher(chain, store)
    run(watcher.step())
    run(watcher.step())
    # One snapshot, not two: an unchanged finalized head is not worth three storage reads.
    assert chain.snapshots_read == 1


@needs_db
def test_a_re_registration_is_appended_and_nothing_else_is_written(store):
    chain = FakeChain()
    chain.register(_neuron(0, "5Alice", at=100))
    chain.register(_neuron(1, "5Bob", at=250))
    watcher = _watcher(chain, store)
    run(watcher.step())

    # uid 1 changes hands. uid 0 does not.
    chain.head += 5
    chain.register(_neuron(1, "5Carol", at=1_003))
    assert run(watcher.step()) == 1
    rows = _rows(store)
    assert [(uid, hot) for uid, hot, *_ in rows] == [(0, "5Alice"), (1, "5Bob"), (1, "5Carol")]
    # History, not state: Bob's row stands, so the slot his registration bought is still his.
    assert store.registrations.available_slots("5Bob") == 1
    assert store.registrations.available_slots("5Carol") == 1


@needs_db
def test_the_archive_is_asked_only_for_heights_that_change_and_only_once(store):
    chain = FakeChain()
    chain.register(_neuron(0, "5Alice", at=100))
    chain.register(_neuron(1, "5Bob", at=100))
    watcher = _watcher(chain, store)
    run(watcher.step())
    assert chain.times_asked == [100]

    chain.head += 1
    run(watcher.step())
    # Nothing changed, so nothing was looked up: a quiet subnet costs no archive reads.
    assert chain.times_asked == [100]


@needs_db
def test_a_pass_that_fails_part_way_is_retried_not_skipped(store):
    chain = FakeChain()
    chain.register(_neuron(0, "5Alice", at=100))
    watcher = _watcher(chain, store)

    chain.fail_block_time = True
    with pytest.raises(ChainUnavailable):
        run(watcher.step())
    assert _rows(store) == []
    assert watcher.last_block is None

    # Same finalized head on the next pass. Had `last_block` advanced above, this pass would
    # see an unchanged head, read nothing, and Alice's registration would be lost for good.
    chain.fail_block_time = False
    assert run(watcher.step()) == 1
    assert store.registrations.is_registered("5Alice")


@needs_db
def test_the_loop_survives_a_failing_pass(store):
    chain = FakeChain()
    chain.register(_neuron(0, "5Alice"))
    chain.fail_head = True
    watcher = _watcher(chain, store)

    async def scenario() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(watcher.run_forever(stop=stop))
        await asyncio.sleep(0.05)
        chain.fail_head = False
        for _ in range(100):
            if watcher.last_block is not None:
                break
            await asyncio.sleep(0.01)
        stop.set()
        await task

    run(scenario())
    assert store.registrations.is_registered("5Alice")


@needs_db
def test_a_completed_pass_touches_the_heartbeat_and_a_failed_one_does_not(store, tmp_path):
    beat = tmp_path / "heartbeat"
    chain = FakeChain()
    chain.register(_neuron(0, "5Alice"))
    watcher = _watcher(chain, store, heartbeat=beat)

    chain.fail_head = True
    with pytest.raises(ChainUnavailable):
        run(watcher.step())
    assert not beat.exists()

    chain.fail_head = False
    run(watcher.step())
    assert beat.exists()


# ── the live source, against a client shaped like Finney ───────────────────


class FinneyShapedClient:
    """Answers the way Finney answered for netuid 66: int uids, SS58 strings, per block."""

    def __init__(self, *, drop_owner_for: int | None = None) -> None:
        self.blocks_asked: set[int | None] = set()
        self._hot = {0: "5HotZero", 1: "5HotOne"}
        self._at = {0: 4_958_013, 1: 5_000_000}
        self._cold = {"5HotZero": "5ColdZero", "5HotOne": "5ColdOne"}
        self._drop = drop_owner_for

    async def query_map(self, item, params, *, block=None):
        self.blocks_asked.add(block)
        assert params == [NETUID]
        if item == ("SubtensorModule", "Keys"):
            return list(self._hot.items())
        if item == ("SubtensorModule", "BlockAtRegistration"):
            return list(self._at.items())
        raise AssertionError(item)

    async def query_batch(self, item, param_sets, *, block=None):
        self.blocks_asked.add(block)
        assert item == ("SubtensorModule", "Owner")
        return [
            None if self._drop is not None and hot == self._hot[self._drop] else self._cold[hot]
            for (hot,) in param_sets
        ]


def _source_with(client: FinneyShapedClient, monkeypatch) -> BittensorRegistrationSource:
    source = BittensorRegistrationSource("finney")

    async def connect(network: str) -> FinneyShapedClient:
        return client

    monkeypatch.setattr(source, "_connect", connect)
    return source


def test_the_live_source_reads_the_subnet_pinned_to_one_block(monkeypatch):
    client = FinneyShapedClient()
    snapshot = run(_source_with(client, monkeypatch).snapshot(NETUID, block=9_129_975))
    assert snapshot == Snapshot(
        netuid=NETUID,
        block=9_129_975,
        neurons=(
            Neuron(uid=0, hotkey="5HotZero", coldkey="5ColdZero", block_at_registration=4_958_013),
            Neuron(uid=1, hotkey="5HotOne", coldkey="5ColdOne", block_at_registration=5_000_000),
        ),
    )
    # Every read at the same block, so the three maps describe one instant.
    assert client.blocks_asked == {9_129_975}


def test_a_half_described_uid_refuses_the_whole_snapshot(monkeypatch):
    source = _source_with(FinneyShapedClient(drop_owner_for=1), monkeypatch)
    with pytest.raises(ChainUnavailable, match="uid 1"):
        run(source.snapshot(NETUID, block=1))


# ── configuration ──────────────────────────────────────────────────────────


def test_the_subnet_is_the_one_the_emissions_worker_pays():
    """Registrations buy submissions on the subnet that pays for them, and no other."""
    from emissions_worker.worker import NETUID as PAID

    assert NETUID == PAID


def test_production_refuses_to_guess_the_competition_database():
    with pytest.raises(SettingsError, match="COMPETITION_DATABASE_URL"):
        RegistrationWatcherSettings.from_env({"APP_MODE": "PROD"})


def test_production_accepts_an_explicit_competition_database():
    settings = RegistrationWatcherSettings.from_env(
        {
            "APP_MODE": "PROD",
            "COMPETITION_DATABASE_URL": "postgresql+psycopg://u:p@db:5432/competition",
        }
    )
    assert settings.database_url.endswith("/competition")
    assert settings.netuid == NETUID
    # The archive defaults to the head's network: Finney's default endpoint answered for a
    # registration height from early 2025 when this was checked.
    assert settings.archive_network == settings.network == "finney"


# ── the health probe ───────────────────────────────────────────────────────


def test_the_probe_fails_until_a_pass_completes_and_again_once_passes_stop(tmp_path: Path):
    beat = tmp_path / "heartbeat"
    assert not healthcheck.check(beat)
    beat.touch()
    assert healthcheck.check(beat)
    stale = time.time() - healthcheck.MAX_AGE_SECONDS - 60
    os.utime(beat, (stale, stale))
    assert not healthcheck.check(beat)
