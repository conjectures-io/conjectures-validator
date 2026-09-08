from dataclasses import replace
import json

import pytest

from conftest import catalog, declaration
from verifier.errors import VerifierError
from verifier.task_generator import generate_task, task_id
from verifier.task_loader import load_task_bundle
from verifier.task_policy import (
    FORMALIZATION,
    OPEN_CONJECTURE,
    compiled_source_policy_valid,
    production_policy_violations,
    review_policy_for_track,
)
from verifier.task_pool import (
    ADDITIONAL_SOURCE_FAMILIES,
    AuditedSelectionEntry,
    SelectionAudit,
    RetiredSources,
    RetiredConjectures,
    TaskTarget,
    TaskTargets,
    TaskGrouping,
    DIRECT_PROPOSITION_POLICY,
    build_task_allowlist,
    reward_target_identity,
    select_task_declarations,
)
from verifier.task_registry import TaskNotAllowed, TaskPoolRegistry, _valid_tier_policy

REFERENCE = {"url": "https://example.org/paper", "location": "Theorem 3"}
HASH = "sha256:" + "a" * 64


def solved():
    return replace(
        declaration(theorem="Erdos1.known_result", category="research solved"),
        module="FormalConjectures.ErdosProblems.1",
        source_path="FormalConjectures/ErdosProblems/1.lean",
    )


def test_solved_requires_explicit_track_and_exact_resolution():
    item = solved()
    assert production_policy_violations(item, ())
    assert production_policy_violations(item, (), track=FORMALIZATION)
    assert not production_policy_violations(
        item, (), track=FORMALIZATION, resolution_reference=REFERENCE
    )
    assert production_policy_violations(
        item, (), "counterexample", track=FORMALIZATION, resolution_reference=REFERENCE
    )
    assert production_policy_violations(
        declaration(), (), track=FORMALIZATION, resolution_reference=REFERENCE
    )
    for mode in ("formalized", "counterexample"):
        assert not production_policy_violations(declaration(), (), mode)


@pytest.mark.parametrize(
    "change",
    [
        {"contains_sorry_in_type": True},
        {"depends_on_sorry": False},
        {"formal_proof_link": "https://example.org/proof"},
        {"contains_answer_annotation": True},
        {"is_prop": False},
    ],
)
def test_formalization_retains_soundness_and_existing_proof_screens(change):
    assert production_policy_violations(
        replace(solved(), **change),
        (),
        track=FORMALIZATION,
        resolution_reference=REFERENCE,
    )


def test_formalization_rejects_proved_collision():
    assert production_policy_violations(
        solved(), ("Other.proved",), track=FORMALIZATION, resolution_reference=REFERENCE
    )


@pytest.mark.parametrize(
    "reference",
    [
        None,
        {},
        {"url": REFERENCE["url"]},
        {"url": "file:///paper", "location": "Theorem 3"},
        {"url": REFERENCE["url"], "location": " "},
    ],
)
def test_missing_or_invalid_reference_is_not_admitted(reference):
    assert production_policy_violations(
        solved(), (), track=FORMALIZATION, resolution_reference=reference
    )


@pytest.mark.parametrize("family", ["erdos", *ADDITIONAL_SOURCE_FAMILIES])
def test_formalization_generation_selection_and_allowlist_roundtrip(tmp_path, family):
    from verifier.task_pool import SOURCE_FAMILY_PREFIXES

    item = solved()
    number = 1 if family == "erdos" else None
    if number is None:
        item = replace(
            item,
            theorem="Known.result",
            source_path=SOURCE_FAMILY_PREFIXES[family] + "Known.lean",
            module=SOURCE_FAMILY_PREFIXES[family].replace("/", ".") + "Known",
        )
    cat = catalog(item)
    audit = SelectionAudit(
        cat.repository_commit,
        cat.repository_commit,
        "2026-09-08",
        1,
        (
            AuditedSelectionEntry(
                item.theorem,
                item.source_path,
                family,
                number,
                "proved",
                ("compact-formal-target",),
                (),
                REFERENCE,
            ),
        ),
        HASH,
        track=FORMALIZATION,
    )
    targets = TaskTargets(
        cat.repository_commit,
        DIRECT_PROPOSITION_POLICY,
        "direct_proposition",
        (
            TaskTarget(
                item.theorem, item.source_path, family, number, reward_target_identity(item.theorem)
            ),
        ),
        HASH,
    )
    retired = RetiredSources(cat.repository_commit, frozenset(), frozenset(), HASH)
    selected = select_task_declarations(
        catalog=cat, retired=retired, selection_audit=audit, task_targets=targets, pool_size=1
    )
    manifest = generate_task(
        catalog=cat,
        declaration=item,
        mode="formalized",
        output=tmp_path / "task",
        track=FORMALIZATION,
        resolution_reference=REFERENCE,
        validate_target=lambda *_: item.type_hash,
    )
    bundle = load_task_bundle(tmp_path / "task")
    assert bundle.manifest == manifest
    assert manifest.task_id != task_id(
        cat.repository_commit, item.theorem, "formalized", manifest.adapter_version
    )
    raw = build_task_allowlist(
        catalog=cat,
        retired=retired,
        retired_conjectures=RetiredConjectures(cat.repository_commit, {}, HASH),
        selection_audit=audit,
        task_targets=targets,
        grouping=TaskGrouping((), HASH),
        selected=((selected[0],),),
        bundles=(bundle,),
        audit_date_utc="2026-09-08",
        tier="tier-2",
    )
    path = tmp_path / "allowlist.json"
    path.write_bytes(raw)
    registry = TaskPoolRegistry.load(path)
    assert registry.assert_bundle(bundle).track == FORMALIZATION
    policy = json.loads(raw)["tier_policies"]["tier-2"]
    assert _valid_tier_policy(policy)
    for update in (
        {"source_category": "research open"},
        {"track": OPEN_CONJECTURE},
        {"policy_version": True},
        {"policy_version": 999},
        {"modes": ["formalized", "counterexample"]},
        {"outcomes_per_problem": True},
    ):
        assert not _valid_tier_policy({**policy, **update})
    # A valid policy with a different track cannot authorize this bundle, even if hashed.
    bad = replace(registry.tasks[manifest.task_id], track=OPEN_CONJECTURE)
    with pytest.raises(TaskNotAllowed, match="track policy"):
        replace(registry, tasks={manifest.task_id: bad}).assert_bundle(bundle)
    changed = replace(
        audit,
        entries=(
            replace(audit.entries[0], resolution_reference={**REFERENCE, "location": "Theorem 4"}),
        ),
    )
    with pytest.raises(VerifierError):
        build_task_allowlist(
            catalog=cat,
            retired=retired,
            retired_conjectures=RetiredConjectures(cat.repository_commit, {}, HASH),
            selection_audit=changed,
            task_targets=targets,
            grouping=TaskGrouping((), HASH),
            selected=((item,),),
            bundles=(bundle,),
            audit_date_utc="2026-09-08",
            tier="tier-2",
        )


@pytest.mark.parametrize(
    "update",
    [
        {"track": "unknown"},
        {"track": OPEN_CONJECTURE},
        {"policy_version": 2},
        {"policy_version": True},
        {"resolution_reference": {}},
    ],
)
def test_tampered_manifest_policy_is_rejected(tmp_path, update):
    item = solved()
    generate_task(
        catalog=catalog(item),
        declaration=item,
        mode="formalized",
        output=tmp_path / "task",
        track=FORMALIZATION,
        resolution_reference=REFERENCE,
        validate_target=lambda *_: item.type_hash,
    )
    path = tmp_path / "task/manifest.json"
    value = json.loads(path.read_text())
    value.update(update)
    path.write_text(json.dumps(value))
    with pytest.raises(VerifierError):
        load_task_bundle(path.parent)


def test_compiled_category_cannot_drift_between_tracks():
    item = solved()
    inspection = {
        "source_category": item.category,
        "source_declaration_kind": "theorem",
        "source_depends_on_sorry": True,
        "source_has_formal_proof": False,
        "target_contains_sorry": False,
        "source_axioms": tuple(sorted(item.transitive_axioms)),
    }
    assert compiled_source_policy_valid(inspection, item, "formalized")
    assert not compiled_source_policy_valid(inspection, item, "counterexample")
    assert not compiled_source_policy_valid(
        {**inspection, "source_category": "research open"}, item, "formalized"
    )


def test_review_policy_does_not_rewrite_open_contract():
    assert review_policy_for_track(OPEN_CONJECTURE, "v2") == "v2"
    assert review_policy_for_track(OPEN_CONJECTURE, "custom-v3") == "custom-v3"
    assert review_policy_for_track(FORMALIZATION, "v2") == "formalization-v1"


def test_track_terms_preserve_open_policy_and_allow_published_proofs():
    from datetime import date
    from pathlib import Path
    from submission_api.credits import SubmissionTerms

    terms = SubmissionTerms.load(
        Path(__file__).resolve().parents[1] / "docs/SUBMISSION_TERMS.md",
        version="v4",
        effective_from=date(2026, 8, 10),
    )
    assert terms.for_track(OPEN_CONJECTURE) is terms
    formal = terms.for_track(FORMALIZATION)
    assert formal.version == "v4.formalization-v1"
    assert formal.effective_from == date(2026, 9, 8)
    assert "Implementing the cited paper" in formal.body_md
    assert "dated public" not in dict(formal.disqualification_reasons)["NOT_NOVEL"]
    assert (
        dict(formal.disqualification_reasons)["PRIOR_EXTERNAL_FORMALIZATION"]
        == dict(terms.disqualification_reasons)["PRIOR_EXTERNAL_FORMALIZATION"]
    )


def test_task_discovery_filters_track_and_exposes_its_contract():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from submission_api.routers.tasks import list_tasks, read_task
    from submission_api.taskpool import catalog_from_entries
    from conftest_api import task_entry

    opened = task_entry()
    formal = replace(
        opened,
        task_id="formal-task",
        reward_target_id="formal-target",
        manifest=replace(opened.manifest, track=FORMALIZATION, resolution_reference=REFERENCE),
    )
    entries = (opened, formal)
    services = SimpleNamespace(
        catalog=catalog_from_entries(
            repository_commit=opened.manifest.repository_commit, entries=entries
        ),
        settings=SimpleNamespace(
            max_bundle_bytes=1000,
            payment_amount_rao=1,
            payment_recipient="recipient",
            review_policy_version="v2",
        ),
        pricing=SimpleNamespace(
            quote_many=AsyncMock(
                return_value=SimpleNamespace(
                    quotes={e.reward_target_id: SimpleNamespace(available=True) for e in entries}
                )
            ),
            quote=AsyncMock(return_value=SimpleNamespace(available=True)),
        ),
    )

    async def run():
        session = SimpleNamespace(commit=AsyncMock())
        result = await list_tasks(services, session, track=FORMALIZATION)
        assert len(result.tasks) == 1
        task = result.tasks[0]
        assert task.task_id == formal.task_id
        assert task.track == FORMALIZATION
        assert task.review_policy_version == "formalization-v1"
        assert task.resolution_reference == REFERENCE
        assert task.submission_terms_url.endswith("track=formalization")
        assert (await read_task(formal.task_id, services, session)) == task
        assert len((await list_tasks(services, session)).tasks) == 2

    asyncio.run(run())


@pytest.mark.parametrize("family", ["erdos", *ADDITIONAL_SOURCE_FAMILIES])
def test_audit_track_metadata_and_resolution_are_required(tmp_path, family):
    from verifier.task_pool import load_selection_audit, SCREENING_STATEMENT

    value = {
        "schema_version": 2,
        "track": FORMALIZATION,
        "policy_version": 1,
        "repository_commit": "a" * 40,
        "source_main_commit": "b" * 40,
        "source_repository": "google-deepmind/formal-conjectures",
        "audit_date_utc": "2026-09-08",
        "github_open_pr_count": 1,
        "screening_statement": SCREENING_STATEMENT,
        "source_status_sources": [],
        "selected": [
            {
                "theorem": solved().theorem,
                "source_path": solved().source_path,
                "source_family": "erdos",
                "source_problem_number": 1,
                "source_status": "proved",
                "upstream_status": "research solved",
                "resolution_reference": REFERENCE,
                "feasibility_signals": ["compact-formal-target"],
                "active_resolution_prs": [],
                "open_prs_touching_source": [],
            }
        ],
    }
    if family != "erdos":
        value["selected"][0].update(
            source_family=family, source_problem_number=None, theorem="Known.result",
            source_path=f"FormalConjectures/{ADDITIONAL_SOURCE_FAMILIES[family]}/Known.lean",
        )
    path = tmp_path / "audit.json"
    path.write_text(json.dumps(value))
    assert load_selection_audit(path).entries[0].resolution_reference == REFERENCE
    for key in ("track", "policy_version"):
        bad = {k: v for k, v in value.items() if k != key}
        path.write_text(json.dumps(bad))
        with pytest.raises(VerifierError):
            load_selection_audit(path)
    value["selected"][0].pop("resolution_reference")
    path.write_text(json.dumps(value))
    with pytest.raises(VerifierError):
        load_selection_audit(path)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"track": "unknown"},
        {"track": FORMALIZATION},
        {"track": FORMALIZATION, "policy_version": True, "resolution_reference": REFERENCE},
    ],
)
def test_development_override_cannot_generate_invalid_track_metadata(tmp_path, kwargs):
    item = solved()
    with pytest.raises(VerifierError):
        generate_task(
            catalog=catalog(item),
            declaration=item,
            mode="formalized",
            output=tmp_path / "task",
            allow_non_open=True,
            validate_target=lambda *_: item.type_hash,
            **kwargs,
        )


@pytest.mark.parametrize("family,directory", ADDITIONAL_SOURCE_FAMILIES.items())
def test_additional_source_identity_is_canonical(family, directory):
    from verifier.task_pool import _valid_source_identity, source_family_from_path

    path = f"FormalConjectures/{directory}/Known.lean"
    assert source_family_from_path(path) == family
    assert _valid_source_identity(family, None, path, "Known.result")
    assert not _valid_source_identity(family, 1, path, "Known.result")
    assert not _valid_source_identity("erdos", None, path, "Known.result")
    for suffix in ("../Known.lean", "/Known.lean", "./Known.lean", "Known.txt", "Known\\file.lean"):
        assert source_family_from_path(f"FormalConjectures/{directory}/{suffix}") is None


def test_public_catalog_exposes_formalization_contract():
    from types import SimpleNamespace
    from submission_api.routers.catalog import _task

    entry = SimpleNamespace(
        task_id="test",
        task_bundle_sha256=HASH,
        manifest=SimpleNamespace(
            task_mode="formalized",
            track=FORMALIZATION,
            policy_version=1,
            resolution_reference=REFERENCE,
        ),
    )
    result = _task(entry, attempts=0)
    assert result.track == FORMALIZATION
    assert result.resolution_reference == REFERENCE
    assert result.submission_terms_url.endswith("track=formalization")
