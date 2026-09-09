from fractions import Fraction

import pytest

from conjectures_subnet.bounty_factors import parse_tier_factors, resolve_target_tiers


def test_factors_are_exact_and_default_is_empty():
    assert parse_tier_factors("{}") == {}
    assert parse_tier_factors('{"tier-1":"1.0","tier-2":"0.25"}') == {
        "tier-1": Fraction(1),
        "tier-2": Fraction(1, 4),
    }


@pytest.mark.parametrize(
    "raw",
    [
        "[]",
        "null",
        "{",
        '{"tier-2":0.25}',
        '{"tier-2":"0"}',
        '{"tier-2":"2"}',
        '{"tier-2":"nan"}',
        '{"tier-2":"1/0"}',
        '{"":"1"}',
    ],
)
def test_invalid_config(raw):
    with pytest.raises(ValueError):
        parse_tier_factors(raw)


def test_variants_share_tier():
    assert resolve_target_tiers([("a", "tier-1"), ("a", "tier-1")], {}) == {"a": "tier-1"}
    with pytest.raises(ValueError, match="conflicting"):
        resolve_target_tiers([("a", "tier-1"), ("a", "tier-2")], {})
    with pytest.raises(ValueError, match="unknown"):
        resolve_target_tiers([("a", "tier-1")], {"typo": Fraction(1)})
