"""Prospective bounty economics; historical submission quotes remain immutable."""

TREASURY_CHAT_POLICY = "linear-age-v3-chat-logs"
LEGACY_TARGET_POLICY = "legacy"


def legacy_chat_bonus_cents(source_module: str) -> int:
    """Classify the authenticated frozen source, never a contributor's display label."""
    if not source_module:
        raise ValueError("a verified source module is required")
    # Owner decision, October 13: $500 for Erdős problems only; every other family is $250.
    if source_module.startswith("FormalConjectures.ErdosProblems."):
        return 50_000
    return 25_000
