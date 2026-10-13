"""Prospective bounty economics; historical submission quotes remain immutable."""

TREASURY_CHAT_POLICY = "treasury-share-2p5-chat-v1"
LEGACY_TARGET_POLICY = "legacy"
TREASURY_SHARE_NUMERATOR = 1
TREASURY_SHARE_DENOMINATOR = 40


def treasury_share_rao(gross_balance_rao: int) -> int:
    if isinstance(gross_balance_rao, bool) or not isinstance(gross_balance_rao, int):
        raise ValueError("treasury balance must be integer base units")
    if gross_balance_rao < 0:
        raise ValueError("treasury balance cannot be negative")
    return gross_balance_rao // TREASURY_SHARE_DENOMINATOR


def legacy_chat_bonus_cents(source_module: str) -> int:
    """Classify the authenticated frozen source, never a contributor's display label."""
    if not source_module:
        raise ValueError("a verified source module is required")
    # Owner decision, October 13: $500 for Erdős problems only; every other family is $250.
    if source_module.startswith("FormalConjectures.ErdosProblems."):
        return 50_000
    return 25_000
