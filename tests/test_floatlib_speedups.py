"""FloatLib's speed-up chain: one benchmark per chain, restarted on a new one only by an anchor."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("sqlalchemy", reason="the FloatLib store needs sqlalchemy")

from submission_api.floatlib_store import (  # noqa: E402
    LEGACY,
    SPEEDUP_ANCHORS,
    benchmark_of,
    speedups,
)

SHIPPED = "0" * 64
SUITE = "scoring-v2-test"


def promotion(pid, sha, timed_against, ratio, benchmark=LEGACY):
    return {
        "promotion_id": pid,
        "promoted_sha256": sha,
        "timed_against": timed_against,
        "ratio": ratio,
        "benchmark": benchmark,
    }


# Two legacy promotions, then three on a suite: the first suite one was timed against the last
# legacy champion, like PROD's #265 against #174.
HISTORY = [
    promotion(1, "a", SHIPPED, 0.5),
    promotion(2, "b", "a", 0.8),
    promotion(3, "c", "b", 0.03, SUITE),
    promotion(4, "d", "c", 0.75, SUITE),
    promotion(5, "e", "d", 0.8, SUITE),
]


def test_legacy_chains_are_unchanged_and_a_cross_benchmark_link_breaks_the_chain():
    result = speedups(HISTORY, anchors={})
    assert result[1] == pytest.approx(2.0)
    assert result[2] == pytest.approx(2.0 / 0.8)
    # Not 2.5 / 0.03 / 0.75 / 0.8 = 138.9: ratios from two benchmarks do not multiply.
    assert result[3] is None and result[4] is None and result[5] is None


def test_an_anchor_restarts_the_chain_on_the_new_benchmark():
    result = speedups(HISTORY, anchors={(SUITE, "c"): 2.2})
    assert result[2] == pytest.approx(2.5)
    assert result[3] == pytest.approx(2.2)
    assert result[4] == pytest.approx(2.2 / 0.75)
    assert result[5] == pytest.approx(2.2 / 0.75 / 0.8)
    # An anchor for another benchmark, or another kernel, changes nothing.
    assert speedups(HISTORY, anchors={("other-suite", "c"): 2.2})[3] is None
    assert speedups(HISTORY, anchors={(SUITE, "b"): 2.2})[3] is None


def test_a_suite_promotion_timed_against_the_shipped_code_needs_no_anchor():
    history = [promotion(1, "a", SHIPPED, 0.4, SUITE), promotion(2, "b", "a", 0.5, SUITE)]
    result = speedups(history, anchors={})
    assert result == {1: pytest.approx(2.5), 2: pytest.approx(5.0)}


def test_the_benchmark_is_read_from_the_verdict():
    assert benchmark_of(json.dumps({"suite": {"version": SUITE, "sha256": "f" * 64}})) == SUITE
    assert benchmark_of(json.dumps({"stage": "score", "ratio": 0.9})) == LEGACY
    assert benchmark_of(json.dumps({"suite": {}})) == LEGACY
    assert benchmark_of(None) == LEGACY
    assert benchmark_of("not json") == LEGACY
    assert benchmark_of("[1, 2]") == LEGACY


def test_every_recorded_anchor_names_a_suite_and_a_kernel_hash():
    # A typo here would silently leave a chain broken; a legacy anchor would mask the legacy chain.
    for (benchmark, sha256), value in SPEEDUP_ANCHORS.items():
        assert benchmark != LEGACY and benchmark
        assert len(sha256) == 64 and all(c in "0123456789abcdef" for c in sha256)
        assert 0 < value < 10_000
