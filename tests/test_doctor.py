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
