"""Exact tier discounts for bounty quotes; omitted tiers retain existing pricing."""

import json
from collections.abc import Mapping
from fractions import Fraction


def parse_tier_factors(raw: str) -> dict[str, Fraction]:
    """Read JSON decimal strings, avoiding binary floating point in money calculations."""
    try:
        values = json.loads(raw)
    except ValueError as exc:
        raise ValueError("BOUNTY_TIER_FACTORS must be a JSON object") from exc
    if not isinstance(values, dict):
        raise ValueError("BOUNTY_TIER_FACTORS must be a JSON object")
    result = {}
    for tier, value in values.items():
        if not tier or tier.strip() != tier or not isinstance(value, str):
            raise ValueError("tier factors require nonempty tier names and decimal strings")
        try:
            factor = Fraction(value)
        except (ValueError, ZeroDivisionError) as exc:
            raise ValueError(f"invalid bounty factor for {tier}") from exc
        if not 0 < factor <= 1:
            raise ValueError("bounty tier factors must be in (0, 1]")
        result[tier] = factor
    return result


def resolve_target_tiers(
    pairs: list[tuple[str, str]], factors: Mapping[str, Fraction]
) -> dict[str, str]:
    """Bind all variants to a single pricing tier and reject misspelled configuration."""
    result: dict[str, str] = {}
    for target, tier in pairs:
        if target in result and result[target] != tier:
            raise ValueError(f"reward target {target} appears in conflicting tiers")
        result[target] = tier
    unknown = set(factors) - set(result.values())
    if unknown:
        raise ValueError(f"bounty factors name unknown tiers: {sorted(unknown)}")
    return result
