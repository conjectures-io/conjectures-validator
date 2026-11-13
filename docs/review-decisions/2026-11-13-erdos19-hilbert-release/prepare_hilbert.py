"""Bind the two eligible Hilbert-related statements to their extracted types."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from prepare_erdos import DATE, ROOT, digest, read, write


TARGETS = (
    {
        "slug": "Hilbert13",
        "name": "Hilbert’s thirteenth problem, algebraic form",
        "scope": "The generic complex septimic has resolvent degree exactly three.",
        "sources": [
            "https://www.math.uchicago.edu/~farb/papers/RD.pdf",
            "https://doi.org/10.4171/DM/984",
        ],
        "documented_by_year": 1900,
    },
    {
        "slug": "RankOneAbelianStark",
        "name": "Rank-one abelian Stark conjecture, a route associated with Hilbert 12",
        "scope": "All admissible finite abelian number-field extensions and normalized places, with the partial-zeta derivative and abelian root-extension conditions.",
        "sources": [
            "https://math.berkeley.edu/~hhao/notes/Rank-one%20abelian%20Stark%20conjecture.pdf",
            "https://www.cambridge.org/core/services/aop-cambridge-core/content/view/853876BFEE7F6C3686DB9381CB3BF08F/S0010437X03100371a.pdf/local_stark_conjecture_at_a_real_place.pdf",
        ],
        "documented_by_year": 1980,
    },
)


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
    assert len(targets["targets"]) == 325
    for item in TARGETS:
        slug = item["slug"]
        name = f"HilbertCatalog.{slug}.conjecture"
        source = f"FormalConjectures/HistoricalOpen/{slug}.lean"
        declaration = declarations[name]
        assert declaration["source_path"] == source
        assert declaration["classification"] == "DIRECT_PROP"
        assert declaration["is_prop"] and declaration["depends_on_sorry"]
        assert not declaration["contains_sorry_in_type"]
        assert name not in {row["theorem"] for row in targets["targets"]}
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
            "The statement was accepted after four Fable 5.2 review rounds. "
            "The release wrapper uses that exact proposition. A bounded source and "
            "claim search on November 13 found no full resolution of this target. "
            + item["scope"]
        )
        admission["targets"].append({
            "theorem": name, "number": slug, "source_path": source,
            "source_family": "historical-open", "target_source_status": "open",
            "decision": "eligible", "review_note": note,
            "production_type_sha256": declaration["type_hash"],
            "references": item["sources"], "checked_date": DATE,
        })
        historical["candidates"].append({
            "theorem": name, "source_path": source, "source_family": "historical-open",
            "source_problem_number": slug, "name": item["name"], "scope": item["scope"],
            "disposition": "retain_provisionally_open", "full_original_question": True,
            "full_resolution_claim_found": False, "sources": item["sources"],
            "documented_by_year": item["documented_by_year"],
            "formalization_kind": "closed proposition with reviewed definitions",
            "claim_screen": note, "checked_date": DATE,
            "production_type_sha256": declaration["type_hash"],
        })
    for value, key in ((selection, "selected"), (targets, "targets"), (admission, "targets")):
        value[key].sort(key=lambda row: row["theorem"])
        value["repository_commit"] = revision
    write(metadata / "historical-open-status-audit.json", historical)
    historical_hash = hashlib.sha256((metadata / "historical-open-status-audit.json").read_bytes()).hexdigest()
    for row in selection["source_status_sources"]:
        if row["family"] == "historical-open":
            row["revision"] = historical_hash
    write(metadata / "erdos-admission-audit.json", admission)
    selection["target_status_audit_sha256"] = digest(metadata / "erdos-admission-audit.json")
    write(metadata / "selection-audit.json", selection)
    write(metadata / "task-targets.json", targets)
    for name in ("held-source-theorems.json", "retired-source-theorems.json",
                 "retired-conjectures.json"):
        value = read(metadata / name)
        value["repository_commit"] = revision
        write(metadata / name, value)
    shutil.copyfile(args.patch, metadata / "formal-conjectures-audit-fixes.patch")
    # Publish the complete reviewed package with its original checksum manifest.
    packet = ROOT / "docs/review-decisions/2026-11-12-hilbert-formulations"
    destination = tasks / "staged/hilbert-formulations-20261113"
    assert not destination.exists()
    files = [line.split("  ", 1)[1] for line in (packet / "sha256sums.txt").read_text().splitlines()]
    for relative in [*files, "sha256sums.txt"]:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(packet / relative, target)
    print(json.dumps({"targets": len(targets["targets"]), "bundles": len(targets["targets"]) * 2,
                      "source_commit": revision, "added_hilbert_targets": [item["slug"] for item in TARGETS]}))


if __name__ == "__main__":
    main()
