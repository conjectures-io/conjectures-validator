"""Stable public slugs and compatibility parsing for conjecture URLs.

The task id is a build identity: it contains the pinned repository revision and therefore moves
on every task-pool rotation.  ``reward_target_id`` is the durable bounty identity, so public URLs
derive from that instead.

Legacy task-id URLs remain useful.  Their readable theorem fragment is independent of the pin,
which lets the catalog redirect an old task URL when exactly one current conjecture matches it.
This module only extracts that fragment; the catalog owns the ambiguity check.
"""

from __future__ import annotations

import re

from verifier.task_generator import task_slug
from verifier.task_pool import REWARD_TARGET_PREFIX
from verifier.task_policy import PRODUCTION_TASK_MODES


MAX_SLUG_LENGTH = 255
_NON_SLUG = re.compile(r"[^a-z0-9]+")
_PRODUCTION_MODE = "|".join(re.escape(mode) for mode in PRODUCTION_TASK_MODES)

# task_generator.task_id emits:
#
#   fc-{8-char commit}-{theorem slug}-{10-char digest}-{mode}-v{adapter version}
#
# Require a mode that the generator can actually issue and every structural delimiter.  The
# theorem group is greedy so hyphens inside it are preserved; the fixed-width hexadecimal digest
# anchors its right edge.
_LEGACY_TASK_ID = re.compile(
    r"^fc-[0-9a-f]{8}-"
    r"(?P<theorem>[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?)-"
    rf"[0-9a-f]{{10}}-(?:{_PRODUCTION_MODE})-v[1-9][0-9]*$"
)


class SlugError(ValueError):
    """A reward target cannot be represented as a public conjecture slug."""


def slug_for(reward_target_id: str) -> str:
    """Return the stable public slug for one theorem reward target.

    The whole qualified theorem name participates.  Unlike the shorter fragment embedded in a
    task id, no leading namespace segments are discarded: doing so would make two distinct
    reward targets needlessly collide.  The catalog performs a second, pool-wide collision check
    because slugification itself is intentionally lossy.
    """
    if not isinstance(reward_target_id, str) or not reward_target_id.startswith(
        REWARD_TARGET_PREFIX
    ):
        raise SlugError("reward target must start with 'fc-target:'")

    theorem = reward_target_id[len(REWARD_TARGET_PREFIX) :]
    slug = _NON_SLUG.sub("-", theorem.lower()).strip("-")
    if not slug:
        raise SlugError("reward target does not contain a slug-compatible theorem name")
    if len(slug) > MAX_SLUG_LENGTH:
        raise SlugError(f"conjecture slug exceeds {MAX_SLUG_LENGTH} characters")
    return slug


def legacy_theorem_slug(candidate: str) -> str | None:
    """Extract the theorem fragment from a task-id-shaped legacy URL.

    ``None`` means the candidate is not a generated task id.  It is deliberately not enough for
    this function to find a vaguely slug-like substring: redirects are permanent, so malformed
    or ambiguous inputs must fall through to a 404 instead of being guessed at.
    """
    if not isinstance(candidate, str):
        return None
    matched = _LEGACY_TASK_ID.fullmatch(candidate)
    return matched.group("theorem") if matched is not None else None


def matches_legacy_slug(theorem: str, candidate: str) -> bool:
    """Whether ``candidate`` is the fragment a historical task id used for ``theorem``."""
    return task_slug(theorem) == candidate


__all__ = [
    "SlugError",
    "legacy_theorem_slug",
    "matches_legacy_slug",
    "slug_for",
]
