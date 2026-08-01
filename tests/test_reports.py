from dataclasses import replace

from verifier.errors import ReasonCode
from verifier.reports import build_report

from conftest import manifest


def test_report_has_stable_complete_checks():
    task_manifest = manifest()
    report = build_report(
        manifest=task_manifest,
        task_bundle_sha256="sha256:" + "0" * 64,
        submission_sha256="sha256:x",
        accepted=False,
        stage="STATIC_POLICY_CHECK",
        reason=ReasonCode.SUBMISSION_POLICY_VIOLATION,
        checks={"manifest_valid": True},
        duration_ms=0,
    ).to_dict()
    assert report["reason_code"] == "SUBMISSION_POLICY_VIOLATION"
    assert report["schema_version"] == 2
    assert report["problem_id"].endswith("-problem")
    assert report["checks"]["manifest_valid"] is True
    assert report["checks"]["lean_kernel_passed"] is False

    counterexample = build_report(
        manifest=replace(
            task_manifest,
            task_id="counterexample-fixture",
            task_mode="counterexample",
        ),
        task_bundle_sha256="sha256:" + "1" * 64,
        submission_sha256="sha256:y",
        accepted=True,
        stage="COMPLETED",
        reason=ReasonCode.VERIFIED,
        checks={},
        duration_ms=0,
    ).to_dict()
    assert counterexample["problem_id"] == report["problem_id"]
