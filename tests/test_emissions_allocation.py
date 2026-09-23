"""Where Subnet 66's emissions go when a competition is in the picture.

Everything here is pure -- no chain, no database, no clock -- because the one number the
whole subnet's payouts turn on should be checkable without either.

The tests that matter are the ones about what happens when something is *missing*. A vector
that pays correctly when everything works is the easy half; the half that decides whether a
bad hour costs the subnet real money is where a stale score file, a deregistered hotkey or an
unreachable API ends up. All of it must land on the treasury, and none of it may skip an
epoch: emissions cannot be set retroactively, so declining to submit is a decision to pay
nobody.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest

from emissions_worker import allocation
from emissions_worker.source import HttpVectorSource, NoVectorSource, from_env
from emissions_worker.vector import combine, treasury_only

TREASURY = 121


def metagraph(**placements: int) -> list[str]:
    """A metagraph of 200 uids, with named hotkeys at the given uids."""
    hotkeys = [f"5Filler{uid}" for uid in range(200)]
    for hotkey, uid in placements.items():
        hotkeys[uid] = hotkey
    hotkeys[TREASURY] = "5TreasuryHotkey"
    return hotkeys


# ── the split ──────────────────────────────────────────────────────────────


def test_the_split_totals_one_hundred_percent():
    # Also asserted at import, so a bad edit cannot reach an epoch; restated here so the
    # failure names this rule rather than surfacing as a collection error.
    total = allocation.TREASURY_BPS + sum(allocation.COMPETITION_BPS.values())
    assert total == allocation.BASIS_POINTS


def test_a_competition_with_no_reviewed_share_is_paid_nothing():
    assert allocation.share("a-competition-nobody-approved") == 0.0


# ── combining ──────────────────────────────────────────────────────────────


def test_scores_are_normalised_before_they_are_scaled():
    """A scorer's numbers are proportional to each other, not a claim on the subnet."""
    uids, weights = combine(
        metagraph_hotkeys=metagraph(**{"5Alice": 3, "5Bob": 7}),
        treasury_uid=TREASURY,
        # Deliberately not summing to 1: the scorer's scale must not matter.
        competition_scores={"miniz-oxide": {"5Alice": 300.0, "5Bob": 100.0}},
    )
    by_uid = dict(zip(uids, weights, strict=True))
    share = allocation.share("miniz-oxide")
    assert by_uid[3] == pytest.approx(share * 0.75)
    assert by_uid[7] == pytest.approx(share * 0.25)
    assert by_uid[TREASURY] == pytest.approx(allocation.treasury_share())
    assert sum(weights) == pytest.approx(1.0)


def test_a_deregistered_hotkeys_share_goes_to_the_treasury_not_to_its_old_uid():
    """Whoever holds that uid now did not earn it."""
    uids, weights = combine(
        metagraph_hotkeys=metagraph(**{"5Alice": 3}),
        treasury_uid=TREASURY,
        competition_scores={"miniz-oxide": {"5Alice": 1.0, "5Vanished": 1.0}},
    )
    by_uid = dict(zip(uids, weights, strict=True))
    share = allocation.share("miniz-oxide")
    assert by_uid[3] == pytest.approx(share / 2)
    assert by_uid[TREASURY] == pytest.approx(allocation.treasury_share() + share / 2)
    assert sum(weights) == pytest.approx(1.0)


def test_a_competition_with_no_scores_burns_its_share_to_the_treasury():
    uids, weights = combine(
        metagraph_hotkeys=metagraph(),
        treasury_uid=TREASURY,
        competition_scores={"miniz-oxide": {}},
    )
    assert uids == [TREASURY]
    assert weights == [pytest.approx(1.0)]


def test_a_competition_absent_from_the_payload_still_has_its_share_accounted_for():
    """Silence is not the same as zero: the share must not simply vanish from the vector."""
    uids, weights = combine(
        metagraph_hotkeys=metagraph(),
        treasury_uid=TREASURY,
        competition_scores={},
    )
    assert uids == [TREASURY]
    assert sum(weights) == pytest.approx(1.0)


def test_an_unfunded_competition_cannot_take_value_from_the_treasury():
    uids, weights = combine(
        metagraph_hotkeys=metagraph(**{"5Alice": 3}),
        treasury_uid=TREASURY,
        competition_scores={"not-a-funded-competition": {"5Alice": 1.0}},
    )
    assert uids == [TREASURY]
    assert weights == [pytest.approx(1.0)]


def test_negative_and_zero_scores_are_not_paid():
    uids, weights = combine(
        metagraph_hotkeys=metagraph(**{"5Alice": 3, "5Bob": 7}),
        treasury_uid=TREASURY,
        competition_scores={"miniz-oxide": {"5Alice": 1.0, "5Bob": 0.0, "5Eve": -5.0}},
    )
    by_uid = dict(zip(uids, weights, strict=True))
    assert by_uid[3] == pytest.approx(allocation.share("miniz-oxide"))
    assert 7 not in by_uid
    assert sum(weights) == pytest.approx(1.0)


def test_the_fallback_pays_the_treasury_and_nothing_else():
    assert treasury_only(TREASURY) == ([TREASURY], [1.0])


# ── reading the vector ─────────────────────────────────────────────────────


class _Response:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _source(**overrides) -> HttpVectorSource:
    return HttpVectorSource(
        url="http://api.internal/v1/competitions/miniz-oxide/weights/current",
        slug="miniz-oxide",
        **overrides,
    )


def _payload(*, age_seconds: float = 0.0, weights=None) -> bytes:
    stamp = dt.datetime.now(dt.UTC) - dt.timedelta(seconds=age_seconds)
    return json.dumps(
        {
            "competition": "miniz-oxide",
            "computed_at": stamp.isoformat().replace("+00:00", "Z"),
            "weights": {"5Alice": 0.75, "5Bob": 0.25} if weights is None else weights,
            "scored_submissions": 2,
        }
    ).encode()


def test_a_fresh_vector_is_read(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **k: _Response(_payload(age_seconds=10))
    )
    assert _source().scores() == {"miniz-oxide": {"5Alice": 0.75, "5Bob": 0.25}}


def test_a_stale_vector_is_refused(monkeypatch):
    """A scorer that died an hour ago still answers, with a leaderboard that has moved."""
    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **k: _Response(_payload(age_seconds=9_000))
    )
    assert _source(max_age_seconds=1200).scores() == {}


def test_an_unreachable_api_pays_the_treasury(monkeypatch):
    import urllib.error

    def boom(*_a, **_k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert _source().scores() == {}


def test_a_malformed_body_pays_the_treasury(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Response(b"{not json"))
    assert _source().scores() == {}

    monkeypatch.setattr(
        "urllib.request.urlopen", lambda *a, **k: _Response(b'{"weights": {}}')
    )
    assert _source().scores() == {}


def test_non_numeric_weights_are_dropped_rather_than_coerced(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **k: _Response(
            _payload(weights={"5Alice": 1.0, "5Bad": "lots", "5Worse": True})
        ),
    )
    assert _source().scores() == {"miniz-oxide": {"5Alice": 1.0}}


def test_an_unconfigured_deployment_gets_todays_behaviour():
    source = from_env({})
    assert isinstance(source, NoVectorSource)
    assert source.scores() == {}


def test_a_configured_deployment_reads_over_http():
    source = from_env(
        {"EMISSIONS_COMPETITION_WEIGHTS_URL": "http://api.internal/v1/x", }
    )
    assert isinstance(source, HttpVectorSource)


# ── the worker's plan ──────────────────────────────────────────────────────


def test_an_unconfigured_worker_sets_exactly_what_it_set_before():
    """The safety property for shipping this: unconfigured is bit-identical to today."""
    from emissions_worker.worker import TREASURY_UID as WORKER_TREASURY
    from emissions_worker.worker import TreasuryWeightWorker

    worker = TreasuryWeightWorker(client=object(), wallet=object())
    assert worker.plan() == ([WORKER_TREASURY], [1.0])


def test_a_worker_whose_metagraph_read_fails_pays_the_treasury():
    from emissions_worker.worker import TREASURY_UID as WORKER_TREASURY
    from emissions_worker.worker import TreasuryWeightWorker

    def broken() -> list[str]:
        raise RuntimeError("subtensor unreachable")

    class Source:
        def scores(self):
            return {"miniz-oxide": {"5Alice": 1.0}}

    worker = TreasuryWeightWorker(
        client=object(), wallet=object(), source=Source(), hotkeys=broken
    )
    assert worker.plan() == ([WORKER_TREASURY], [1.0])
