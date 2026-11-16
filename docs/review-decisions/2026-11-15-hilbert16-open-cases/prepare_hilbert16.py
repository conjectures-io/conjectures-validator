"""Admit the reviewed quintic-surface case of Hilbert 16 and record the quadratic hold."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PACKET = Path(__file__).resolve().parent
DATE = "2026-11-15"
REVIEW_URL = (
    "https://github.com/conjectures-io/conjectures-validator/blob/main/"
    "docs/review-decisions/2026-11-15-hilbert16-open-cases/README.md"
)
ADMITTED = {
    "slug": "Hilbert16QuinticSurfaces",
    "name": "Hilbert 16, first part: components of real quintic surfaces",
    "scope": (
        "Every real quintic surface in RP^3 with nonsingular complexification has at most "
        "23 connected components. The maximum is known to be 23, 24 or 25. The target is the "
        "bound 23 only; a refutation leaves 24 and 25 to be separated. It is one case of one "
        "clause of Hilbert 16, not the whole problem."
    ),
    "sources": [
        "https://mathcs.clarku.edu/~djoyce/hilbert/problems.html#prob16",
        "https://www.math.univ-toulouse.fr/~orevkov/q23.pdf",
        "https://arxiv.org/abs/math/0502127",
        "https://arxiv.org/abs/1805.02030",
        "https://arxiv.org/abs/2603.08094",
        REVIEW_URL,
    ],
    "documented_by_year": 1900,
}
HELD = {
    "slug": "Hilbert16QuadraticLimitCycles",
    "name": "Hilbert 16, second part: quadratic vector fields have at most four limit cycles",
    "scope": (
        "Every real planar polynomial vector field of degree at most two has at most four "
        "limit cycles, counted as point sets in the whole affine plane."
    ),
    "claim_sources": ["https://arxiv.org/abs/2103.07193v4"],
    "sources": [
        "https://mathcs.clarku.edu/~djoyce/hilbert/problems.html#prob16",
        "https://www.ams.org/journals/bull/2002-39-03/S0273-0979-02-00946-1/",
        "https://arxiv.org/abs/2407.13465",
        "https://arxiv.org/abs/2610.12218",
        REVIEW_URL,
    ],
    "reason": (
        "A live unrefereed preprint (Pedregal, arXiv:2103.07193v4, 3 November 2026) claims "
        "exactly H(2) = 4. The review found the claim neither established nor refuted. "
        "Admission is withheld pending an owner decision or a review that disposes of the "
        "claim. No judgment that the claimed proof is correct."
    ),
}


def read(path: Path) -> dict:
    return json.loads(path.read_text())


def write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n")


def digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tasks", type=Path)
    parser.add_argument("catalog", type=Path)
    parser.add_argument("patch", type=Path)
    args = parser.parse_args()
    tasks = args.tasks.resolve()
    metadata = tasks / "tiers/tier-1"
    catalog = read(args.catalog)
    declarations = {row["theorem"]: row for row in catalog["declarations"]}
    revision = catalog["repository_commit"]
    selection = read(metadata / "selection-audit.json")
    targets = read(metadata / "task-targets.json")
    admission = read(metadata / "erdos-admission-audit.json")
    historical = read(metadata / "historical-open-status-audit.json")
    assert len(targets["targets"]) == 327

    def declaration_for(slug: str) -> tuple[str, str, dict]:
        name = f"HilbertCatalog.{slug}.conjecture"
        source = f"FormalConjectures/HistoricalOpen/{slug}.lean"
        row = declarations[name]
        assert row["source_path"] == source
        assert row["classification"] == "DIRECT_PROP" and row["category"] == "research open"
        assert row["is_prop"] and row["depends_on_sorry"]
        assert not row["contains_sorry_in_type"]
        assert name not in {item["theorem"] for item in targets["targets"]}
        return name, source, row

    slug = ADMITTED["slug"]
    name, source, declaration = declaration_for(slug)
    entry = {
        "active_resolution_prs": [], "open_prs_touching_source": [],
        "feasibility_signals": ["standard-mathlib-surface"],
        "source_family": "historical-open", "source_path": source,
        "source_problem_number": slug, "source_status": "open",
        "theorem": name, "upstream_status": "research open",
    }
    selection["selected"].append(entry)
    targets["targets"].append({key: entry[key] for key in (
        "theorem", "source_path", "source_family", "source_problem_number",
    )} | {"reward_target_id": "fc-target:" + name})
    note = (
        "Two independent fresh-context reviews on November 15 accepted the statement, and "
        "machine-checked witnesses show it is not vacuous. A bounded literature search on "
        "November 15 found the maximum still known only to be 23, 24 or 25, with no later "
        "construction or bound. " + ADMITTED["scope"]
    )
    admission["targets"].append({
        "theorem": name, "number": slug, "source_path": source,
        "source_family": "historical-open", "target_source_status": "open",
        "decision": "eligible", "review_note": note,
        "production_type_sha256": declaration["type_hash"],
        "references": ADMITTED["sources"], "checked_date": DATE,
    })
    historical["candidates"].append({
        "theorem": name, "source_path": source, "source_family": "historical-open",
        "source_problem_number": slug, "name": ADMITTED["name"], "scope": ADMITTED["scope"],
        "disposition": "retain_provisionally_open", "full_original_question": True,
        "full_resolution_claim_found": False, "sources": ADMITTED["sources"],
        "documented_by_year": ADMITTED["documented_by_year"],
        "formalization_kind": "closed proposition with reviewed definitions",
        "claim_screen": note, "checked_date": DATE,
        "production_type_sha256": declaration["type_hash"],
    })

    held_name, held_source, held_declaration = declaration_for(HELD["slug"])
    admission["excluded_candidates"].append({
        "theorem": held_name, "number": HELD["slug"], "decision": "hold_resolution_claim",
        "production_type_sha256": held_declaration["type_hash"],
        "reason": HELD["reason"], "references": [*HELD["claim_sources"], REVIEW_URL],
    })
    historical["exclusions"].append({
        "theorem": held_name, "declaration": "HilbertDrafts.Hilbert16Open.QuadraticFourCycles",
        "source_path": held_source, "source_family": "historical-open",
        "source_problem_number": HELD["slug"], "name": HELD["name"], "scope": HELD["scope"],
        "disposition": "hold_identified_full_resolution_claim",
        "full_original_question": True, "full_resolution_claim_found": True,
        "claim_author": "Pablo Pedregal", "claim_title": "Hilbert's 16th problem through Morse theory",
        "claim_sources": HELD["claim_sources"],
        "claim_validity": "unverified; assessed as neither established nor refuted",
        "decision_basis": HELD["reason"], "sources": HELD["sources"],
        "documented_by_year": 1900, "checked_date": DATE,
        "formalization_kind": "closed proposition with reviewed definitions",
        "production_type_sha256": held_declaration["type_hash"],
    })

    for value, key in ((selection, "selected"), (targets, "targets"), (admission, "targets")):
        value[key].sort(key=lambda row: row["theorem"])
        value["repository_commit"] = revision
    selection["audit_date_utc"] = DATE
    admission["audit_date_utc"] = DATE
    write(metadata / "historical-open-status-audit.json", historical)
    historical_hash = hashlib.sha256(
        (metadata / "historical-open-status-audit.json").read_bytes()).hexdigest()
    for row in selection["source_status_sources"]:
        if row["family"] == "historical-open":
            row["revision"] = historical_hash
    write(metadata / "erdos-admission-audit.json", admission)
    selection["target_status_audit_sha256"] = digest(metadata / "erdos-admission-audit.json")
    write(metadata / "selection-audit.json", selection)
    write(metadata / "task-targets.json", targets)
    for filename in ("held-source-theorems.json", "retired-source-theorems.json",
                     "retired-conjectures.json"):
        value = read(metadata / filename)
        value["repository_commit"] = revision
        write(metadata / filename, value)
    shutil.copyfile(args.patch, metadata / "formal-conjectures-audit-fixes.patch")
    # Publish the reviewed packet with its checksum manifest.
    destination = tasks / "staged/hilbert16-open-cases-20261115"
    assert not destination.exists()
    files = [line.split("  ", 1)[1] for line in (PACKET / "sha256sums.txt").read_text().splitlines()]
    for relative in [*files, "sha256sums.txt"]:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PACKET / relative, target)
    print(json.dumps({"targets": len(targets["targets"]), "bundles": len(targets["targets"]) * 2,
                      "source_commit": revision, "admitted": [name], "held": [held_name]}))


if __name__ == "__main__":
    main()
