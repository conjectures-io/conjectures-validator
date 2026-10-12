#!/usr/bin/env python3
"""Generate the published schema, blank template, and explicitly synthetic teaching example."""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission_api.trajectory_schema import TrajectoryPackage, digest


def known(value):
    return {"value": value, "unknown_reason": None}


def unknown(why):
    return {"value": None, "unknown_reason": why}


def example():
    proof = "example : True := by\n  trivial\n"
    h = digest(proof.encode())
    messages = [
        ("user_message", "Prove True in Lean. This entire conversation is a synthetic teaching example.", None),
        ("assistant_message", "I will try an initial constructor-based proof; this synthetic attempt will fail.", None),
        ("tool_call", "lean Solution.lean; file contains: example : True := by exact False.elim", "call-1"),
        ("tool_result", "error: type mismatch (synthetic result, no Lean invocation occurred)", "call-1"),
        ("failure", "The initial synthetic attempt used the wrong constructor.", None),
        ("revision", "Replaced the body with trivial; see the final-proof artifact.", None),
        ("tool_call", "lean Solution.lean", "call-2"),
        ("tool_result", "exit code 0 (synthetic result, not a verification claim)", "call-2"),
        ("assistant_message", "The final source is attached. No actual bounty or accepted submission exists in this example.", None),
    ]
    data = {
        "schema_version": "conjectures-trajectory/v1", "synthetic": True,
        "submission_id": "00000000-0000-4000-8000-000000000001", "task_id": "synthetic-example",
        "task_bundle_sha256": "sha256:" + "0" * 64, "final_proof_sha256": h,
        "exported_at": "2026-10-12T12:01:00Z",
        "runs": [{"id": "run-1", "provider": known("Synthetic Provider"), "model": known("Synthetic Model"),
                  "version": unknown("Illustrative data; no real model was invoked."),
                  "interface": "Handwritten synthetic example", "settings": "No actual generation settings.",
                  "account_agreement": unknown("No provider account was used."),
                  "terms_reference": unknown("No provider terms apply to these invented example messages."),
                  "export_method": "Created for documentation; never claim this is authentic evidence.",
                  "source_export_sha256": None,
                  "source_export_unavailable_reason": "No original conversation exists; this is synthetic."}],
        "events": [{"sequence": i, "id": f"event-{i}", "kind": kind, "visibility": "user_visible",
                    "timestamp": known(f"2026-10-12T12:00:{i:02d}Z"),
                    "run_id": "run-1", "tool_call_id": call, "tool_name": "terminal" if call else None,
                    "content": content, "artifact_ids": ["final-proof"] if i in (6, 9) else []}
                   for i, (kind, content, call) in enumerate(messages, 1)],
        "artifacts": [{"id": "final-proof", "path": "Solution.lean", "kind": "final_proof", "sha256": h,
                       "availability": "embedded", "content": proof, "omission_reason": None}],
        "reproduction": {"lean_toolchain": known("leanprover/lean4:v4.27.0"),
                         "mathlib_commit": unknown("This standalone synthetic proof does not import Mathlib."),
                         "task_repository_commit": unknown("There is no real task repository for this example."),
                         "code_commit": unknown("Example source was never committed."),
                         "environment": "Illustrative Lean command; no actual environment was run.",
                         "commands": ["lean Solution.lean"], "expected_result": "Exit code 0 for the standalone True example.",
                         "limitations": "Synthetic event log, fake submission UUID and task hash. Never eligible for a bonus."},
        "completeness": "gaps_disclosed",
        "missing": [{"field_or_event": "entire example", "reason": "Synthetic data has no source export or accepted proof.",
                     "impact_on_reproduction": "Shows the file format only. Authenticity cannot be established."}],
        "redactions": [],
        "permissions": {"terms_version": "trajectory-review-draft-v1", "authentic_user_visible_export": True,
                        "authorized_to_submit": True, "secrets_and_personal_data_checked": True,
                        "third_party_consent_or_removal": True, "private_review_and_storage_only": True,
                        "upload_alone_grants_no_commercial_rights": True},
    }
    for key in data["permissions"]:
        if key != "terms_version":
            data["permissions"][key] = False  # This fabricated example cannot make real attestations.
    data["commercial_election"] = {
        "terms_version": "trajectory-commercial-draft-v1",
        "terms_sha256": digest((ROOT / "docs/trajectories/commercial-license-draft-v1.md").read_bytes()),
        "election": "decline", "purposes": [], "sources": [], "explicit_assent": False,
    }
    TrajectoryPackage.model_validate(data)
    return data


def main():
    destination = ROOT / "docs/trajectories"
    destination.mkdir(parents=True, exist_ok=True)
    sample = example()
    template = copy.deepcopy(sample)
    template["synthetic"] = False
    template["submission_id"] = "REPLACE_WITH_YOUR_SUBMISSION_UUID"
    template["task_bundle_sha256"] = "REPLACE_WITH_TASK_BUNDLE_SHA256"
    template["final_proof_sha256"] = "REPLACE_WITH_FINAL_PROOF_SHA256"
    template["exported_at"] = "REPLACE_WITH_REAL_EXPORT_TIMESTAMP"
    template["reproduction"] = {
        **{k: unknown("REPLACE_WITH_KNOWN_PIN_OR_HONEST_REASON") for k in ("lean_toolchain", "mathlib_commit", "task_repository_commit", "code_commit")},
        "environment": "REPLACE_WITH_ENVIRONMENT", "commands": ["REPLACE_WITH_COMMANDS"],
        "expected_result": "REPLACE_WITH_OBSERVED_RESULT", "limitations": "REPLACE_WITH_LIMITATIONS_OR_NONE",
    }
    template["task_id"] = "REPLACE_WITH_YOUR_ACCEPTED_TASK_ID"
    template["events"] = []
    template["artifacts"] = []
    template["runs"] = []
    template["missing"] = []
    for key in template["permissions"]:
        if key != "terms_version":
            template["permissions"][key] = False
    grant = copy.deepcopy(sample)
    grant["commercial_election"].update(election="conditional_grant", purposes=["evaluation"], explicit_assent=True,
        sources=[{
            "source_id": source, "rights_status": "unknown", "agreement_or_license": unknown("Synthetic example; no real license or agreement."),
            "evidence_reference": unknown("No actual rights evidence exists."), "allowed_purposes": [],
            "restrictions": "Illustrative only; no commercial rights are granted or cleared.",
            "third_party_rights": "No real rights representation is made by this synthetic example.",
        } for source in ["run:run-1", "artifact:final-proof"]])
    TrajectoryPackage.model_validate(grant)
    schema = TrajectoryPackage.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://conjectures.io/trajectory/trajectory.schema.json"
    for name, data in [("trajectory.schema.json", schema), ("synthetic-example.json", sample),
                       ("template.json", template), ("synthetic-rights-example.json", grant)]:
        (destination / name).write_text(json.dumps(data, indent=2) + "\n")


if __name__ == "__main__":
    main()
