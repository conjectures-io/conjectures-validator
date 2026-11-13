"""Apply the reviewed nineteen-target selection to the existing task release."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
EXCLUDED = "Erdos173.erdos_173"
BASE_COMMIT = "91e6059086b8fba40fffef843b02e9a064e0d41a"
DATE = "2026-11-13"
REVIEW_URL = (
    "https://github.com/conjectures-io/conjectures-validator/blob/main/"
    "docs/review-decisions/2026-11-11-erdos20-literature/README.md"
)


def read(path: Path) -> dict:
    return json.loads(path.read_text())


def write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tasks", type=Path)
    args = parser.parse_args()
    tasks = args.tasks.resolve()
    metadata = tasks / "tiers/tier-1"
    review = read(ROOT / "docs/review-decisions/2026-11-11-erdos20-literature/review.json")
    reviewed = {row["theorem"]: row for row in review["rows"]}
    assert len(reviewed) == 20 and review["excluded_numbers"] == [173]

    selection = read(metadata / "selection-audit.json")
    targets = read(metadata / "task-targets.json")
    admission = read(metadata / "erdos-admission-audit.json")
    retired = read(metadata / "retired-source-theorems.json")
    display = read(metadata / "retired-conjectures.json")
    allowlist = read(tasks / "allowlist.json")
    versions = read(tasks / "task-versions.json")
    assert len(targets["targets"]) == 326
    assert EXCLUDED in {row["theorem"] for row in targets["targets"]}

    selection["selected"] = [row for row in selection["selected"] if row["theorem"] != EXCLUDED]
    targets["targets"] = [row for row in targets["targets"] if row["theorem"] != EXCLUDED]
    admission["targets"] = [row for row in admission["targets"] if row["theorem"] != EXCLUDED]
    for row in admission["targets"]:
        evidence = reviewed.get(row["theorem"])
        if evidence is None:
            continue
        assert row["production_type_sha256"] == evidence["production_type_sha256"]
        row["review_note"] = evidence["review"]
        row["scope_note"] = evidence["finding"]
        row["references"] = sorted(set(row["references"]) | {
            evidence["catalog_url"], evidence["wiki_url"], REVIEW_URL,
            *(url for _, url in evidence["sources"]),
        })
    selection["audit_date_utc"] = DATE
    admission["audit_date_utc"] = DATE
    admission["excluded_candidates"].append({
        "theorem": EXCLUDED,
        "number": 173,
        "decision": "exclude_prior_solution",
        "production_type_sha256": reviewed[EXCLUDED]["production_type_sha256"],
        "review_note": reviewed[EXCLUDED]["review"],
        "references": [REVIEW_URL, reviewed[EXCLUDED]["catalog_url"]],
    })
    retired["source_theorems"] = sorted(set(retired["source_theorems"]) | {EXCLUDED})
    retired["source_type_sha256"] = sorted(set(retired["source_type_sha256"]) | {
        reviewed[EXCLUDED]["production_type_sha256"],
    })

    # Preserve the public statement and the immutable version directories.
    rows = [row for row in versions["versions"]
            if row["state"] == "active" and row["theorems"] == [EXCLUDED]]
    assert {row["mode"] for row in rows} == {"formalized", "counterexample"}
    allowed = {row["task_id"]: row for row in allowlist["allowed_task_bundles"]}
    reason = "PRIOR_SOLUTION (full matching formal solution accepted by erdosproblems.ai)"
    recovered = []
    source = None
    for row in rows:
        bundle = tasks / row["location"]
        current_source = read(bundle / "source-metadata.json")
        assert source is None or source == current_source
        source = current_source
        entry = allowed[row["task_id"]]
        recovered.append({
            "task_id": row["task_id"], "task_mode": row["mode"],
            "problem_id": entry["problem_id"],
            "task_bundle_sha256": row["task_bundle_sha256"],
            "target_type_sha256": entry["target_type_sha256s"][0],
            "challenge_lean": (bundle / "Challenge.lean").read_text(),
        })
    assert EXCLUDED not in {row["theorem"] for row in display["retired"]}
    display["retired"].append({
        "theorem": EXCLUDED, "reward_target_id": "fc-target:" + EXCLUDED,
        "tier": "tier-1", "pool_status": "retired", "retired_on": DATE,
        "reason_code": "PRIOR_SOLUTION", "reason": reason, "decision_url": REVIEW_URL,
        "recovered_from_commit": BASE_COMMIT, "source": source,
        "tasks": sorted(recovered, key=lambda row: row["task_mode"]),
    })
    display["retired"].sort(key=lambda row: row["theorem"])
    log = metadata / "RETIREMENTS.md"
    log.write_text(log.read_text().rstrip() + f"\n- `{EXCLUDED}` — {DATE} — `{reason}`\n")
    write(metadata / "task-targets.json", targets)
    write(metadata / "erdos-admission-audit.json", admission)
    selection["target_status_audit_sha256"] = digest(metadata / "erdos-admission-audit.json")
    write(metadata / "selection-audit.json", selection)
    write(metadata / "retired-source-theorems.json", retired)
    write(metadata / "retired-conjectures.json", display)
    print("Prepared 325 targets, including the nineteen reviewed Erdős additions.")


if __name__ == "__main__":
    main()
