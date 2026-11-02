"""Historical bonuses require the exact task bundle accepted with the proof."""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from conftest import catalog, declaration
from conjectures_subnet.bounty_policy import TREASURY_CHAT_POLICY
from submission_api.errors import Conflict
from submission_api.trajectories import amount_for
from verifier.task_generator import generate_task
from verifier.task_loader import load_task_bundle


def archived_legacy(tmp_path, module="FormalConjectures.GreensOpenProblems.«39»"):
    source = replace(declaration(), module=module)
    path = tmp_path / "original"
    generate_task(
        catalog=catalog(source), declaration=source, mode="counterexample", output=path,
        validate_target=lambda *_: "sha256:" + "2" * 64,
    )
    bundle = load_task_bundle(path)
    archived = tmp_path / "history" / "legacy" / bundle.manifest.task_id
    archived.parent.mkdir(parents=True)
    path.rename(archived)
    services = SimpleNamespace(
        settings=SimpleNamespace(task_pool_root=tmp_path / "pool"),
        catalog=SimpleNamespace(entries={}),
    )
    submission = SimpleNamespace(
        task_id=bundle.manifest.task_id,
        task_bundle_sha256=bytes.fromhex(bundle.sha256.removeprefix("sha256:")),
        bounty_policy_version="linear-age-v3-locked",
    )
    return services, submission, archived, bundle


@pytest.mark.parametrize("module,expected", [
    ("FormalConjectures.GreensOpenProblems.«39»", 25_000),
    ("FormalConjectures.ErdosProblems.«579»", 50_000),
])
@pytest.mark.parametrize("changed_catalog_entry", [False, True])
def test_legacy_bonus_uses_the_original_archived_bundle(tmp_path, module, expected, changed_catalog_entry):
    services, submission, _, _ = archived_legacy(tmp_path, module)
    if changed_catalog_entry:
        services.catalog.entries[submission.task_id] = SimpleNamespace(
            task_bundle_sha256="sha256:" + "0" * 64,
            manifest=SimpleNamespace(source_module="Unrelated.NewSource"),
        )
    assert amount_for(services, submission) == expected


@pytest.mark.parametrize("fault", ["missing", "corrupt", "digest", "task_id", "symlink"])
def test_unavailable_or_mismatched_legacy_archive_still_blocks_the_bonus(tmp_path, fault):
    services, submission, archived, _ = archived_legacy(tmp_path)
    if fault == "missing":
        archived.rename(tmp_path / "elsewhere")
    elif fault == "corrupt":
        (archived / "Challenge.lean").write_text("-- altered\n")
    elif fault == "digest":
        submission.task_bundle_sha256 = bytes(32)
    elif fault == "task_id":
        submission.task_id += "-other"
        archived.rename(archived.parent / submission.task_id)
    elif fault == "symlink":
        archived.rename(tmp_path / "elsewhere")
        archived.symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    with pytest.raises(Conflict) as error:
        amount_for(services, submission)
    assert error.value.reason_code == "TRAJECTORY_SOURCE_UNAVAILABLE"


@pytest.mark.parametrize("task_id", ["../outside", "/absolute", "", ".", ".."])
def test_archive_lookup_rejects_unsafe_task_ids(tmp_path, monkeypatch, task_id):
    services, submission, _, _ = archived_legacy(tmp_path)
    submission.task_id = task_id

    def unexpected_read(_):
        pytest.fail("an unsafe task ID reached the filesystem")

    monkeypatch.setattr("submission_api.trajectories.load_task_bundle", unexpected_read)
    with pytest.raises(Conflict) as error:
        amount_for(services, submission)
    assert error.value.reason_code == "TRAJECTORY_SOURCE_UNAVAILABLE"


def test_current_catalog_bundle_needs_no_archive(tmp_path):
    services, submission, archived, bundle = archived_legacy(tmp_path)
    services.catalog.entries[submission.task_id] = SimpleNamespace(
        task_bundle_sha256=bundle.sha256, manifest=bundle.manifest,
    )
    archived.rename(tmp_path / "elsewhere")
    assert amount_for(services, submission) == 25_000


def test_required_chat_logs_have_no_retrospective_bonus():
    assert amount_for(None, SimpleNamespace(bounty_policy_version=TREASURY_CHAT_POLICY)) == 0


def test_v2_bonus_still_uses_the_version_store(tmp_path):
    from test_task_store import planned, stage

    store, plan, source_catalog = planned(tmp_path)
    bundle = store.publish(stage(store, plan, source_catalog), plan.task_id)
    store.root.rename(tmp_path / "versions")
    services = SimpleNamespace(
        settings=SimpleNamespace(task_pool_root=tmp_path / "pool"),
        catalog=SimpleNamespace(entries={}),
    )
    submission = SimpleNamespace(
        task_id=plan.task_id,
        task_bundle_sha256=bytes.fromhex(bundle.sha256.removeprefix("sha256:")),
        bounty_policy_version="linear-age-v3-locked",
    )
    assert amount_for(services, submission) == 25_000
    submission.task_bundle_sha256 = bytes(32)
    with pytest.raises(Conflict) as error:
        amount_for(services, submission)
    assert error.value.reason_code == "TRAJECTORY_SOURCE_UNAVAILABLE"
