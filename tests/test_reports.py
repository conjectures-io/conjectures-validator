from verifier.errors import ReasonCode
from verifier.reports import build_report

from conftest import manifest


def test_report_has_stable_complete_checks():
    report = build_report(
        manifest=manifest(),
        task_bundle_sha256="sha256:" + "0" * 64,
        submission_sha256="sha256:x",
        accepted=False,
        stage="STATIC_POLICY_CHECK",
        reason=ReasonCode.SUBMISSION_POLICY_VIOLATION,
        checks={"manifest_valid": True},
        duration_ms=0,
    ).to_dict()
    assert report["reason_code"] == "SUBMISSION_POLICY_VIOLATION"
    assert report["checks"]["manifest_valid"] is True
    assert report["checks"]["lean_kernel_passed"] is False
