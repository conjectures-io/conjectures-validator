"""Loading the retired-conjecture set, and the boundary it must never cross.

Two things are being tested, and the second matters more than the first.

The first is ordinary loading: the file is found through the tier policy, its bytes are checked
against the digest that policy publishes, and the entries come out shaped the way the catalog
needs them.

The second is that none of this can widen admission. A retired conjecture is *readable* — that
is the whole point, since the results and attribution earned against it have to stay citable —
and it is *never admissible*. Those two live in separate objects on purpose: `TaskCatalog` is
built from `allowlist.json` and the bundles on disk, and the submission path resolves against
`TaskCatalog` alone. The last test in this module is the one that pins that down.

No database and no HTTP. The endpoint behaviour is in `test_api_retired.py`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from conftest import declaration

from submission_api.retired import (
    DIGEST_FIELD,
    RETIRED_FILE_NAME,
    RETIRED_SCHEMA_VERSION,
    RetiredIndex,
    RetiredPoolError,
)
from verifier.repository import tasks_repository_root

ROOT = Path(__file__).resolve().parents[1]
TASKS_ROOT = tasks_repository_root(ROOT)
TIER = "tier-1"

THEOREM = "Erdos11.erdos_11"
REWARD_TARGET = f"fc-target:{THEOREM}"
# Derived from the reward target, not from a task id, so it survives a pin rotation. Spelled out
# because it is a public URL.
SLUG = "erdos11-erdos-11"


def _task(mode: str) -> dict:
    return {
        "task_id": f"task-{mode}",
        "task_mode": mode,
        "problem_id": "fixture-problem",
        "task_bundle_sha256": "sha256:" + "a" * 64,
        "target_type_sha256": "sha256:" + "b" * 64,
        "challenge_lean": f"-- {mode}\n",
    }


def _payload(**overrides) -> dict:
    entry = {
        "reward_target_id": REWARD_TARGET,
        "theorem": THEOREM,
        "tier": TIER,
        "retired_on": "2026-08-06",
        "reason_code": "SOLVED",
        "reason": "SOLVED (a verified submission settled the target)",
        "decision_url": "https://example.invalid/decisions/erdos-11.md",
        "recovered_from_commit": "c" * 40,
        "source": declaration(theorem=THEOREM).to_dict(),
        # Written the way the generator writes them: sorted by mode name, so `counterexample`
        # comes first. The loader is expected to restore production order.
        "tasks": [_task("counterexample"), _task("formalized")],
    }
    entry.update(overrides)
    return {
        "schema_version": RETIRED_SCHEMA_VERSION,
        "repository_commit": "d" * 40,
        "retired": [entry],
    }


def _write(tmp_path: Path, payload: dict | None, *, digest: str | None = "") -> Path:
    """Lay out a checkout: an allowlist with a tier policy, and the file it points at.

    `digest=""` means "whatever the file actually hashes to" — the healthy case. Passing a
    string pins a wrong digest; passing None omits the field entirely.
    """
    allowlist_path = tmp_path / "allowlist.json"
    policy: dict[str, object] = {}

    if payload is not None:
        tier_dir = tmp_path / "tiers" / TIER
        tier_dir.mkdir(parents=True)
        raw = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
        (tier_dir / RETIRED_FILE_NAME).write_bytes(raw)
        actual = "sha256:" + hashlib.sha256(raw).hexdigest()
        if digest is not None:
            policy[DIGEST_FIELD] = actual if digest == "" else digest
    elif digest not in (None, ""):
        policy[DIGEST_FIELD] = digest

    allowlist_path.write_text(
        json.dumps({"tier_policies": {TIER: policy}}), encoding="utf-8"
    )
    return allowlist_path


# --- ordinary loading -------------------------------------------------------------------------


def test_a_retired_target_is_addressable_by_its_stable_slug(tmp_path):
    index = RetiredIndex.load(allowlist_path=_write(tmp_path, _payload()))

    item = index.get(SLUG)
    assert item is not None
    assert item.reward_target_id == REWARD_TARGET
    assert item.retired_on == "2026-08-06"
    assert item.reason_code == "SOLVED"
    assert item.decision_url == "https://example.invalid/decisions/erdos-11.md"
    # Recovered from the bundle, which is the only reason the page can still be rendered.
    assert item.source.theorem == THEOREM
    assert item.classification == "DIRECT_PROP"


def test_the_attack_directions_are_ordered_as_they_are_on_a_live_conjecture(tmp_path):
    """A reader must not see the two modes swap places when a target closes.

    The generator writes them sorted by name, so this is a real reordering rather than a
    coincidence of the fixture.
    """
    index = RetiredIndex.load(allowlist_path=_write(tmp_path, _payload()))

    assert index.get(SLUG).task_modes == ("formalized", "counterexample")


def test_a_task_id_from_a_deleted_bundle_still_names_its_conjecture(tmp_path):
    """These ids are on every report and result already published for the target."""
    index = RetiredIndex.load(allowlist_path=_write(tmp_path, _payload()))

    assert index.slug_by_task_id["task-formalized"] == SLUG
    assert index.slug_by_task_id["task-counterexample"] == SLUG


def test_a_tier_that_retires_nothing_publishes_no_digest_and_loads_empty(tmp_path):
    """Absent is a valid state — a pool that has never retired anything. Only wrong is fatal."""
    index = RetiredIndex.load(allowlist_path=_write(tmp_path, None, digest=None))

    assert index.all() == ()


# --- the fail-closed checks -------------------------------------------------------------------


def test_bytes_that_do_not_match_the_published_digest_stop_startup(tmp_path):
    """The digest is the only thing tying this file to the audited allowlist beside it.

    Without the check, an edited retired set would publish an arbitrary statement under a slug
    readers reach from a result — a target's *recorded history* rewritten after the fact.
    """
    allowlist_path = _write(tmp_path, _payload(), digest="sha256:" + "f" * 64)

    with pytest.raises(RetiredPoolError, match="hashes to"):
        RetiredIndex.load(allowlist_path=allowlist_path)


def test_a_published_digest_with_no_file_behind_it_stops_startup(tmp_path):
    """The pinned checkout is then not the release the allowlist describes."""
    allowlist_path = _write(tmp_path, None, digest="sha256:" + "f" * 64)

    with pytest.raises(RetiredPoolError, match="is missing"):
        RetiredIndex.load(allowlist_path=allowlist_path)


def test_a_future_schema_version_is_refused(tmp_path):
    payload = _payload()
    payload["schema_version"] = RETIRED_SCHEMA_VERSION + 1

    with pytest.raises(RetiredPoolError, match="schema version"):
        RetiredIndex.load(allowlist_path=_write(tmp_path, payload))


def test_tasks_disagreeing_about_their_problem_id_are_refused(tmp_path):
    """A conjecture publishes one `problem_id`, so its tasks cannot be allowed to differ."""
    tasks = [_task("counterexample"), _task("formalized")]
    tasks[0]["problem_id"] = "other-problem"

    with pytest.raises(RetiredPoolError, match="spans problem ids"):
        RetiredIndex.load(allowlist_path=_write(tmp_path, _payload(tasks=tasks)))


def test_two_retired_targets_at_one_slug_are_refused(tmp_path):
    """Same rule the live grouping applies: one conjecture must not be served at another's URL."""
    payload = _payload()
    payload["retired"].append(dict(payload["retired"][0]))

    with pytest.raises(RetiredPoolError, match="both produce the slug"):
        RetiredIndex.load(allowlist_path=_write(tmp_path, payload))


# --- the boundary -----------------------------------------------------------------------------


def test_the_checked_in_retired_set_loads_against_its_own_allowlist():
    """The real file in the pinned task repository, verified against the real tier policy.

    This is what actually runs at startup, so a retirement committed without refreshing
    `retired_conjectures_sha256` fails here rather than in production.
    """
    index = RetiredIndex.load(allowlist_path=TASKS_ROOT / "allowlist.json")

    assert index.all(), "the task repository has retirements but none loaded"
    for item in index.all():
        assert item.tasks, f"{item.slug} has no recovered tasks"
        assert item.source.type_pretty, f"{item.slug} has no statement to render"
        assert item.retired_on, f"{item.slug} has no retirement date"


def test_no_retired_target_is_on_the_allowlist():
    """The property the whole design rests on, checked against the real repository.

    `retired-conjectures.json` is presentation only. If a reward target in it were also in
    `allowed_source_theorems`, a target could be advertised as closed while still accepting paid
    submissions — the one failure this split exists to make impossible.
    """
    index = RetiredIndex.load(allowlist_path=TASKS_ROOT / "allowlist.json")
    allowlist = json.loads(
        (TASKS_ROOT / "allowlist.json").read_text(encoding="utf-8")
    )

    admitted = {row["theorem"] for row in allowlist["allowed_source_theorems"]}
    admitted_tasks = {row["task_id"] for row in allowlist["allowed_task_bundles"]}

    for item in index.all():
        assert item.source.theorem not in admitted, (
            f"{item.source.theorem} is retired but still admitted"
        )
        for task in item.tasks:
            assert task.task_id not in admitted_tasks, (
                f"retired task {task.task_id} is still allowlisted"
            )
