#!/usr/bin/env python3
"""Build the single audited Erdős task-tier metadata snapshot."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TIER = ROOT / "tasks/tiers/tier-1"
CANDIDATES = ROOT / "tasks/candidates/direct-candidates-2026-07-28.json"
CATALOG = ROOT / "data/catalog.json"
PATCHED_COMMIT = "379fc0298dc146df549e7061c3ede0353a5bb51f"
SOURCE_MAIN_COMMIT = "f7349f32ba6df6e7b7baf77467a3c6c7777a634d"
TRACKER_COMMIT = "2e7e7a630f9814f3df562bc1b207d9ad41451a55"
AUDIT_DATE = "2026-08-03"
GITHUB_OPEN_PR_COUNT = 281
SCREENING_STATEMENT = (
    "Plausibly attackable solver target; this is a comparative screen, not a "
    "claim that the conjecture is easy or guaranteed solvable."
)

WHOLE_PROBLEM_THEOREMS = (
    "Erdos1094.erdos_1094",
    "Erdos11.erdos_11",
    "Erdos1107.erdos_1107",
    "Erdos184.erdos_184",
    "Erdos233.erdos_233",
    "Erdos236.erdos_236",
    "Erdos242.erdos_242",
    "Erdos243.erdos_243",
    "Erdos274.herzog_schonheim",
    "Erdos28.erdos_28",
    "Erdos282.erdos_282",
    "Erdos340.erdos_340",
    "Erdos364.erdos_364",
    "Erdos371.erdos_371",
    "Erdos373.erdos_373",
    "Erdos41.erdos_41",
    "Erdos535.erdos_535",
    "Erdos617.erdos_617",
    "Erdos624.erdos_624",
    "Erdos677.erdos_677",
    "Erdos779.erdos_779",
    "Erdos82.erdos_82",
    "Erdos859.erdos_859",
    "Erdos889.erdos_889",
    "Erdos89.erdos_89",
    "Erdos912.erdos_912",
    "Erdos932.erdos_932",
    "Erdos952.erdos_952",
    "Erdos982.erdos_982",
)

ADDITIONAL_THEOREMS = (
    "Erdos10.erdos_10.variants.grechuk",
    "Erdos1055.erdos_1055.variants.erdos_limit",
    "Erdos1055.erdos_1055.variants.selfridge_limit",
    "Erdos1060.erdos_1060.parts.ii",
    "Erdos1074.erdos_1074.variants.EHSNumbers_one_half",
    "Erdos1093.erdos_1093.parts.ii",
    "Erdos1095.erdos_1095.variants.log_isTheta",
    "Erdos1095.erdos_1095.variants.lower_conjecture",
    "Erdos126.erdos_126.variants.isLittleO",
    "Erdos137.erdos_137.variants.multiple_powerful_factors",
    "Erdos142.erdos_142.variants.lower",
    "Erdos143.erdos_143.parts.ii",
    "Erdos208.erdos_208.variants.log_bound",
    "Erdos218.erdos_218.variants.ge",
    "Erdos218.erdos_218.variants.infinite_equal_prime_gap",
    "Erdos218.erdos_218.variants.le",
    "Erdos241.erdos_241.variants.generalization",
    "Erdos242.erdos_242.variants.schinzel_generalization",
    "Erdos272.erdos_272.variants.szabo_strong",
    "Erdos313.erdos_313.variants.primary_pseudoperfect_are_infinite",
    "Erdos324.erdos_324.variants.quintic",
    "Erdos340.erdos_340.variants.sub_hasPosDensity",
    "Erdos357.erdos_357.parts.i",
    "Erdos357.erdos_357.variants.infinite_set_density",
    "Erdos357.erdos_357.variants.infinite_set_sum",
    "Erdos357.erdos_357.variants.monotone.parts.i",
    "Erdos359.erdos_359.parts.i",
    "Erdos359.erdos_359.parts.ii",
    "Erdos359.erdos_359.variants.isGoodFor_1_asymptotic",
    "Erdos364.erdos_364.variants.strong",
    "Erdos373.erdos_373.variants.maximal_solution",
    "Erdos373.erdos_373.variants.suranyi",
    "Erdos406.erdos_406.variants.one_two",
    "Erdos409.erdos_409.variants.sigma_termination",
    "Erdos416.erdos_416.parts.i",
    "Erdos477.erdos_477.variants.X_pow_three",
    "Erdos477.erdos_477.variants.monomial",
    "Erdos535.erdos_535.variants.first_open_case",
    "Erdos770.erdos_770.variants.three",
    "Erdos853.erdos_853.parts.i",
    "Erdos853.erdos_853.parts.ii",
    "Erdos887.erdos_887.parts.ii",
    "Erdos889.erdos_889.variants.general",
    "Erdos912.erdos_912.variants.tao",
    "Erdos913.erdos_913.variants.infinite_many_8p_sq_sub_one_primes",
)

RENAMED_CANDIDATES = {
    "Erdos1095.erdos_1095.variants.log_isTheta": (
        "Erdos1095.erdos_1095.variants.log_equivalent"
    ),
    "Erdos913.erdos_913.variants.infinite_many_8p_sq_sub_one_primes": (
        "Erdos913.erdos_913.variants.infinite_many_8p_sq_add_one_primes"
    ),
}

TRACKER_STATUSES = {
    242: "falsifiable",
    364: "verifiable",
}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def problem_number(theorem: str) -> int:
    return int(theorem.split(".", 1)[0].removeprefix("Erdos"))


def audit_document(entries: list[dict]) -> dict:
    return {
        "audit_date_utc": AUDIT_DATE,
        "github_open_pr_count": GITHUB_OPEN_PR_COUNT,
        "problem_tracker_commit": TRACKER_COMMIT,
        "problem_tracker_repository": "teorth/erdosproblems",
        "repository_commit": PATCHED_COMMIT,
        "schema_version": 1,
        "screening_statement": SCREENING_STATEMENT,
        "selected": sorted(entries, key=lambda item: item["theorem"]),
        "source_main_commit": SOURCE_MAIN_COMMIT,
        "source_repository": "google-deepmind/formal-conjectures",
    }


def target_document(theorems: list[str]) -> dict:
    return {
        "policy": "one_task_one_audited_proposition",
        "repository_commit": PATCHED_COMMIT,
        "schema_version": 2,
        "task_scope": "direct_proposition",
        "targets": [
            {
                "erdos_problem_number": problem_number(theorem),
                "reward_family_id": f"erdos-{problem_number(theorem)}",
                "source_path": (
                    f"FormalConjectures/ErdosProblems/{problem_number(theorem)}.lean"
                ),
                "theorem": theorem,
            }
            for theorem in sorted(theorems)
        ],
    }


def main() -> int:
    all_theorems = tuple(sorted(WHOLE_PROBLEM_THEOREMS + ADDITIONAL_THEOREMS))
    if (
        len(WHOLE_PROBLEM_THEOREMS) != 29
        or len(ADDITIONAL_THEOREMS) != 45
        or len(all_theorems) != 74
        or len(set(all_theorems)) != 74
    ):
        raise SystemExit("the task theorem list must contain exactly 74 unique entries")

    catalog_theorems = {
        entry["theorem"] for entry in read_json(CATALOG)["declarations"]
    }
    missing = sorted(set(all_theorems) - catalog_theorems)
    if missing:
        raise SystemExit("task theorems missing from catalog: " + ", ".join(missing))

    old_audit = read_json(TIER / "selection-audit.json")
    old_entries = {item["theorem"]: item for item in old_audit["selected"]}
    missing_existing = sorted(set(WHOLE_PROBLEM_THEOREMS) - set(old_entries))
    if missing_existing:
        raise SystemExit("existing audit entries missing: " + ", ".join(missing_existing))
    existing_entries = [old_entries[theorem] for theorem in WHOLE_PROBLEM_THEOREMS]

    inventory = read_json(CANDIDATES)
    candidate_by_theorem = {
        item["theorem"]: item for item in inventory["candidates"]
    }
    additional_entries = []
    for theorem in ADDITIONAL_THEOREMS:
        number = problem_number(theorem)
        candidate_name = RENAMED_CANDIDATES.get(theorem, theorem)
        candidate = candidate_by_theorem[candidate_name]
        additional_entries.append(
            {
                "active_resolution_prs": [],
                "erdos_problem_number": number,
                "feasibility_signals": candidate["prior_feasibility_signals"],
                "open_prs_touching_source": sorted(
                    item["number"] for item in candidate["other_open_prs"]
                ),
                "problem_tracker_status": TRACKER_STATUSES.get(number, "open"),
                "source_path": candidate["source_path"],
                "theorem": theorem,
                "upstream_status": "research open",
            }
        )

    retired = read_json(TIER / "retired-source-theorems.json")
    retired["repository_commit"] = PATCHED_COMMIT
    empty_groups = {
        "completion_policy": "all_of",
        "groups": [],
        "schema_version": 1,
    }

    write_json(
        TIER / "selection-audit.json",
        audit_document(existing_entries + additional_entries),
    )
    write_json(
        TIER / "task-targets.json",
        target_document(list(all_theorems)),
    )
    write_json(TIER / "retired-source-theorems.json", retired)
    write_json(TIER / "task-groups.json", empty_groups)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
