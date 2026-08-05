"""What the image's own readiness verdict may and may not depend on."""

from __future__ import annotations

from verifier.doctor import image_pins_satisfied


def _pins(**overrides: bool) -> dict[str, dict[str, bool]]:
    status = {"formal_conjectures": True, "mathlib": True, "lean": True, "tasks": True}
    status.update(overrides)
    return {name: {"pinned": pinned} for name, pinned in status.items()}


def test_readiness_ignores_the_task_pool_pin():
    """A container's doctor run mounts nothing, so requiring this pin made `ready` unreachable.

    This is the whole production container runner: `assert_container_ready` refuses to start a
    worker whose image reports `ready: false`, and every image reports the task pool as unpinned
    because the pool is mounted one task at a time, per verification, and never for the doctor.
    """
    assert image_pins_satisfied(_pins(tasks=False)) is True


def test_readiness_still_requires_every_pin_inside_the_image():
    """The exclusion is the task pool alone, not a general softening of the pin check."""
    assert image_pins_satisfied(_pins()) is True
    assert image_pins_satisfied(_pins(mathlib=False)) is False
    assert image_pins_satisfied(_pins(formal_conjectures=False)) is False
    assert image_pins_satisfied(_pins(lean=False)) is False


def test_verification_and_readiness_agree_on_which_pins_matter():
    """They disagreed once, and the verifier then failed every proof at LOAD_TASK.

    `assert_dependency_pins` runs inside the container on the verification path; `ready` runs on
    the doctor path. Both must exclude the task pool, or one gate passes and the other rejects
    every submission for a reason that has nothing to do with the submission.
    """
    from verifier.repository import image_pin_statuses

    statuses = _pins(tasks=False)
    assert "tasks" not in image_pin_statuses(statuses)
    assert set(image_pin_statuses(statuses)) == {"formal_conjectures", "mathlib", "lean"}
    assert image_pins_satisfied(statuses) is True
