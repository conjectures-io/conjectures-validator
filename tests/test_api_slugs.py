"""Stable conjecture slug derivation and legacy task-id compatibility."""

from __future__ import annotations

import pytest

from submission_api.slugs import (
    SlugError,
    legacy_theorem_slug,
    matches_legacy_slug,
    slug_for,
)
from verifier.task_generator import task_id


def test_slug_uses_the_whole_stable_reward_target():
    assert slug_for("fc-target:Archive.Erdos11.erdos_11") == "archive-erdos11-erdos-11"


@pytest.mark.parametrize("reward_target_id", ["Erdos11.erdos_11", "fc-target:", "fc-target:___"])
def test_slug_rejects_a_target_without_a_usable_theorem(reward_target_id: str):
    with pytest.raises(SlugError):
        slug_for(reward_target_id)


def test_slug_rejects_a_value_too_long_for_the_public_path_contract():
    with pytest.raises(SlugError):
        slug_for("fc-target:" + "a" * 256)


def test_legacy_task_id_exposes_the_pin_independent_theorem_fragment():
    theorem = "Erdos11.erdos_11"
    old_task_id = task_id("f" * 40, theorem, "formalized", 1)

    embedded = legacy_theorem_slug(old_task_id)

    assert embedded == "erdos11-erdos-11"
    assert matches_legacy_slug(theorem, embedded)


@pytest.mark.parametrize(
    "candidate",
    [
        "erdos11-erdos-11",
        "fc-not-a-commit-erdos11-erdos-11-0123456789-formalized-v1",
        "fc-ffffffff-erdos11-erdos-11-notdigest-formalized-v1",
        "fc-ffffffff-erdos11-erdos-11-0123456789-unknown-v1",
        "fc-ffffffff-erdos11-erdos-11-0123456789-formalized-v",
        "fc-ffffffff-erdos11-erdos-11-0123456789-formalized-v0",
    ],
)
def test_legacy_parser_rejects_values_that_are_not_generated_task_ids(candidate: str):
    assert legacy_theorem_slug(candidate) is None
