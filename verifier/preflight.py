from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from verifier.bundle import (
    ProofBundle,
    admit_proof_bundle,
    load_proof_bundle,
    read_bundle_file,
)
from verifier.errors import ReasonCode, VerifierError
from verifier.models import VerificationReport
from verifier.service_adapter import ProductionVerifierAdapter
from verifier.task_loader import load_task_bundle


@dataclass(frozen=True)
class BundlePreflight:
    """The exact bundle bytes and the authoritative local verifier result."""

    raw: bytes
    bundle: ProofBundle
    report: VerificationReport


def verify_proof_bundle_bytes(
    *,
    raw: bytes,
    task_dir: Path,
    project_root: Path,
    expected_task_id: str | None = None,
    expected_task_sha256: str | None = None,
    expected_hotkey: str | None = None,
    allow_insecure_development: bool = False,
) -> BundlePreflight:
    """Bind a miner bundle to a local task and run the production proof verifier.

    ``bundle scan`` intentionally stops at archive and static-policy admission. This preflight
    continues through the same task reconstruction, Comparator, axiom, statement, and Lean kernel
    checks used by the validator worker. The development override changes only the sandbox used
    for this local run; it does not weaken any proof check.
    """

    task = load_task_bundle(task_dir)
    if expected_task_id is not None and expected_task_id != task.manifest.task_id:
        raise VerifierError(
            ReasonCode.INVALID_ARGUMENT,
            "requested task_id does not match the local task bundle",
        )
    if expected_task_sha256 is not None and expected_task_sha256 != task.sha256:
        raise VerifierError(
            ReasonCode.TASK_COMMITMENT_MISMATCH,
            "requested task digest does not match the local task bundle",
        )

    # Read the manifest before admission only to obtain the claimed hotkey for standalone local
    # verification. A submitting client supplies its authenticated hotkey instead. Admission then
    # reparses and binds every field against the trusted task and that expected identity.
    claimed = load_proof_bundle(
        raw, max_proof_bytes=task.manifest.max_submission_bytes
    )
    admitted = admit_proof_bundle(
        raw,
        task_manifest=task.manifest,
        expected_task_sha256=task.sha256,
        expected_hotkey=expected_hotkey or claimed.manifest.miner_hotkey,
    )
    report = ProductionVerifierAdapter(
        project_root=project_root,
        allow_insecure_development=allow_insecure_development,
    ).verify_bytes(
        task_dir=task_dir,
        submission=admitted.proof.raw,
        expected_task_sha256=task.sha256,
    )
    return BundlePreflight(raw=raw, bundle=admitted, report=report)


def verify_proof_bundle_file(
    *,
    bundle_path: Path,
    task_dir: Path,
    project_root: Path,
    expected_task_id: str | None = None,
    expected_task_sha256: str | None = None,
    expected_hotkey: str | None = None,
    allow_insecure_development: bool = False,
) -> BundlePreflight:
    return verify_proof_bundle_bytes(
        raw=read_bundle_file(bundle_path),
        task_dir=task_dir,
        project_root=project_root,
        expected_task_id=expected_task_id,
        expected_task_sha256=expected_task_sha256,
        expected_hotkey=expected_hotkey,
        allow_insecure_development=allow_insecure_development,
    )
