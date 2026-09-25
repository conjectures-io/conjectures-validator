"""Turning competition scores into one uid-aligned weight vector.

One process sets weights on Subnet 66, and this is what it sets. Everything here is pure:
it takes the metagraph's hotkeys and each competition's per-hotkey scores, and returns the
uids and weights to submit. No chain calls, no database, no clock -- so the one number the
whole subnet's emissions turn on is testable without either.

Three rules, and each is a decision about who gets paid when something is missing:

**Everything unaccounted for burns to the treasury**, never to uid 0 and never to nobody.
The competition this came from burned to uid 0 by convention while the platform pays the
treasury; after combining there can be exactly one, and the treasury is the reviewed one. A
hotkey that scored but is no longer on the metagraph, a competition with no scores, a
competition with no share -- all of it lands there rather than being quietly dropped, which
would emit a vector summing to less than one and burn the remainder invisibly.

**A competition's scores are normalised before they are scaled.** The scorer's numbers only
have to be proportional to each other; what they must not do is decide their own share of
the subnet. That is `allocation.py`'s job, and normalising here is what enforces it.

**The vector covers the whole metagraph.** Omitting a uid is not the same as giving it zero,
and the difference matters to consensus.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from emissions_worker import allocation

# Below this a weight is indistinguishable from rounding noise, and including it costs a uid
# in the extrinsic for nothing.
EPSILON = 1e-9


def combine(
    *,
    metagraph_hotkeys: Sequence[str],
    treasury_uid: int,
    competition_scores: Mapping[str, Mapping[str, float]],
) -> tuple[list[int], list[float]]:
    """The vector to submit: uids and weights, summing to 1.0.

    `metagraph_hotkeys[uid]` is the hotkey registered at that uid, which is how a
    competition's per-hotkey scores become per-uid weights. A scored hotkey that no longer
    appears there has deregistered since it was scored; its share goes to the treasury
    rather than to whoever now holds its old uid.
    """
    uid_of = {hotkey: uid for uid, hotkey in enumerate(metagraph_hotkeys)}
    weights: dict[int, float] = {}
    unallocated = 0.0

    for slug, scores in competition_scores.items():
        budget = allocation.share(slug)
        if budget <= 0.0:
            # Scored but unfunded: a competition with no reviewed share is paid nothing, and
            # its scores cannot quietly take value from the treasury.
            continue
        total = sum(value for value in scores.values() if value > 0)
        if total <= 0:
            unallocated += budget
            continue
        for hotkey, score in scores.items():
            if score <= 0:
                continue
            portion = budget * (score / total)
            uid = uid_of.get(hotkey)
            if uid is None:
                unallocated += portion
                continue
            weights[uid] = weights.get(uid, 0.0) + portion

    # Competitions with a share but no scores at all this epoch.
    for slug, budget_bps in allocation.COMPETITION_BPS.items():
        if slug not in competition_scores:
            unallocated += budget_bps / allocation.BASIS_POINTS

    treasury = allocation.treasury_share() + unallocated
    weights[treasury_uid] = weights.get(treasury_uid, 0.0) + treasury

    ordered = sorted((uid, weight) for uid, weight in weights.items() if weight > EPSILON)
    return [uid for uid, _ in ordered], [weight for _, weight in ordered]


def treasury_only(treasury_uid: int) -> tuple[list[int], list[float]]:
    """The fallback vector: everything to the treasury.

    What every failure resolves to -- a stale score vector, an unreachable API, a malformed
    body. Never an empty vector and never a skipped epoch: an epoch's emissions cannot be
    set retroactively, so not submitting is a decision to pay nobody, which is strictly
    worse than paying the treasury.
    """
    return [treasury_uid], [1.0]
