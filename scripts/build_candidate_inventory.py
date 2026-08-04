#!/usr/bin/env python3
"""Build an inspectable inventory from a pinned candidate-audit snapshot."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


MECHANICAL_PR_NUMBERS = {4631}
LOW_RISK_PR_NUMBERS = {3525, 4619, 4650}
LOW_RISK_PR_TITLE_RE = re.compile(
    r"^(?:"
    r"chore(?:\(|:)|"
    r"docs?(?:\(|:)|"
    r"feat\(ErdosProblems\): All \d+ Erdős conjectures formalized|"
    r"Add the first \d+ files from AutoOeis|"
    r"Erdős .* discharge test/textbook scaffolding sorries"
    r")",
    re.IGNORECASE,
)
VARIANT_OR_PART_RE = re.compile(
    r"(?:^|\.)(?:variants?|parts?)(?:\.|$)|"
    r"(?:^|_)(?:variants?|parts?)(?:_|$)",
    re.IGNORECASE,
)
ANSWER_HOLE_RE = re.compile(r"\banswer\s*\(\s*sorry\s*\)")
DOCSTRING_STATUS_CONFLICT_RE = re.compile(
    r"\b(?:proved|proven|showed|resolved|disproved|counterexample|"
    r"known to be true|known to be false)\b",
    re.IGNORECASE,
)
DECLARATION_RE_TEMPLATE = (
    r"(?m)^[ \t]*(?:theorem|lemma)\s+{name}(?=[\s(:])"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--site-data", type=Path, required=True)
    parser.add_argument("--pr-paths", type=Path, required=True)
    parser.add_argument("--allowlist", type=Path, required=True)
    parser.add_argument("--selection-audit", type=Path)
    parser.add_argument("--review-decisions", type=Path)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--upstream-commit", required=True)
    parser.add_argument("--audit-date", required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--expected-count", type=int, default=274)
    return parser.parse_args()


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def clean_docstring(raw: str) -> str:
    lines = []
    for line in raw.splitlines():
        line = re.sub(r"^[ \t]*\*?[ \t]?", "", line.rstrip())
        lines.append(line)
    return "\n".join(lines).strip()


def source_context(
    source_root: Path, relative_path: str, local_name: str
) -> tuple[int | None, str, str]:
    source_path = source_root / "FormalConjectures" / relative_path
    if not source_path.is_file():
        return None, "", ""

    text = source_path.read_text(encoding="utf-8")
    declaration_re = re.compile(
        DECLARATION_RE_TEMPLATE.format(name=re.escape(local_name))
    )
    declaration = declaration_re.search(text)
    if declaration is None:
        return None, "", ""

    line = text.count("\n", 0, declaration.start()) + 1
    proof = re.search(r":=\s*by\b", text[declaration.end() :])
    declaration_type = ""
    if proof is not None:
        declaration_type = text[
            declaration.end() : declaration.end() + proof.start()
        ].strip()

    doc_start = text.rfind("/--", 0, declaration.start())
    if doc_start < 0:
        return line, "", declaration_type
    doc_end = text.find("-/", doc_start + 3)
    if doc_end < 0 or doc_end > declaration.start():
        return line, "", declaration_type

    between = text[doc_end + 2 : declaration.start()]
    without_attributes = re.sub(r"@\[[\s\S]*?\]", "", between)
    without_comments = re.sub(r"/-(?!-)[\s\S]*?-/", "", without_attributes)
    if without_comments.strip():
        return line, "", declaration_type

    return (
        line,
        clean_docstring(text[doc_start + 3 : doc_end]),
        declaration_type,
    )


def compact_summary(docstring: str, theorem: str, limit: int = 180) -> str:
    summary = re.sub(r"\s+", " ", docstring).strip()
    if not summary:
        summary = theorem
    if len(summary) <= limit:
        return summary
    return summary[: limit - 1].rstrip() + "…"


def review_lane(
    collection: str,
    flags: list[str],
    hard_reject_reasons: list[str],
    hold_reasons: list[str],
) -> str:
    if hard_reject_reasons:
        return "rejected"
    if hold_reasons:
        return "hold"
    structural_flags = {
        "named_variant_or_part",
        "multiple_candidates_in_source",
        "open_pr_review_required",
        "missing_docstring",
    }
    needs_structural_review = bool(structural_flags.intersection(flags))
    if collection == "ErdosProblems":
        return "a" if not needs_structural_review else "b"
    return "c" if not needs_structural_review else "d"


def markdown_escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def main() -> None:
    args = parse_args()
    candidates = load_json(args.candidates)
    site_data = load_json(args.site_data)
    pr_paths = load_json(args.pr_paths)
    allowlist = load_json(args.allowlist)
    selection_by_theorem = {}
    if args.selection_audit:
        selection_audit = load_json(args.selection_audit)
        selection_by_theorem = {
            entry["theorem"]: entry for entry in selection_audit["selected"]
        }
    review_decisions = {}
    if args.review_decisions:
        decision_data = load_json(args.review_decisions)
        review_decisions = {
            entry["theorem"]: entry for entry in decision_data["decisions"]
        }

    live_theorems = {
        entry["theorem"] for entry in allowlist["allowed_source_theorems"]
    }
    additional = [
        candidate
        for candidate in candidates
        if candidate["theorem"] not in live_theorems
    ]
    if len(additional) != args.expected_count:
        raise SystemExit(
            f"expected {args.expected_count} additional candidates, "
            f"found {len(additional)}"
        )
    unknown_decisions = sorted(
        set(review_decisions).difference(
            candidate["theorem"] for candidate in additional
        )
    )
    if unknown_decisions:
        raise SystemExit(
            "review decisions do not match the candidate snapshot: "
            + ", ".join(unknown_decisions)
        )

    site_by_theorem = {
        record["theorem"]: record for record in site_data["conjectures"]
    }
    prs_by_path: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for pull_request in pr_paths:
        for path in pull_request["paths"]:
            prs_by_path[path].append(pull_request)

    candidates_per_path = Counter(item["path"] for item in additional)
    records = []
    for candidate in additional:
        theorem = candidate["theorem"]
        site = site_by_theorem.get(theorem, {})
        relative_path = candidate["path"]
        source_line, docstring, declaration_type = source_context(
            args.source_root, relative_path, candidate["name"]
        )
        repository_path = f"FormalConjectures/{relative_path}"
        open_prs = []
        for pull_request in prs_by_path.get(repository_path, []):
            if pull_request["number"] in MECHANICAL_PR_NUMBERS:
                continue
            pr_classification = (
                "low_risk_source_churn"
                if (
                    pull_request["number"] in LOW_RISK_PR_NUMBERS
                    or LOW_RISK_PR_TITLE_RE.search(pull_request["title"])
                )
                else "review_required"
            )
            open_prs.append(
                {
                    "number": pull_request["number"],
                    "title": pull_request["title"],
                    "url": pull_request["url"],
                    "draft": pull_request["draft"],
                    "classification": pr_classification,
                }
            )
        open_prs.sort(key=lambda item: item["number"], reverse=True)

        flags = []
        if VARIANT_OR_PART_RE.search(theorem):
            flags.append("named_variant_or_part")
        if candidates_per_path[relative_path] > 1:
            flags.append("multiple_candidates_in_source")
        if candidate["collection"] != "ErdosProblems":
            flags.append("external_status_review_required")
        if any(
            item["classification"] == "review_required" for item in open_prs
        ):
            flags.append("open_pr_review_required")
        if any(
            item["classification"] == "low_risk_source_churn"
            for item in open_prs
        ):
            flags.append("low_risk_source_churn_pr_recorded")
        if not docstring:
            flags.append("missing_docstring")
        if docstring and DOCSTRING_STATUS_CONFLICT_RE.search(docstring):
            flags.append("docstring_status_conflict")
        if candidate["collection"] == "Millenium":
            flags.append("millennium_problem")

        hard_reject_reasons = []
        hold_reasons = []
        if ANSWER_HOLE_RE.search(declaration_type):
            flags.append("answer_hole_in_type")
            hard_reject_reasons.append("unsupported_answer_hole")
        elif re.search(r"\btype_of%", declaration_type):
            flags.append("pointer_alias")
            hard_reject_reasons.append("pointer_alias_not_distinct_task")
        elif re.search(r"\bsorry\b", declaration_type):
            flags.append("sorry_in_type")
            hard_reject_reasons.append("type_contains_sorry")
        if not declaration_type:
            flags.append("type_extraction_failed")
            hard_reject_reasons.append("type_extraction_failed")
        manual_decision = review_decisions.get(theorem)
        if manual_decision:
            if manual_decision["disposition"] == "reject":
                hard_reject_reasons.append(manual_decision["reason_code"])
                flags.append("manual_reject")
            elif manual_decision["disposition"] == "hold":
                hold_reasons.append(manual_decision["reason_code"])
                flags.append("manual_hold")
            elif manual_decision["disposition"] == "retain":
                flags.append("manual_status_reviewed")

        subjects = site.get("subjects", [])
        pinned_url = (
            "https://github.com/google-deepmind/formal-conjectures/blob/"
            f"{args.upstream_commit}/{repository_path}"
        )
        if source_line is not None:
            pinned_url += f"#L{source_line}"

        record = {
            "theorem": theorem,
            "local_name": candidate["name"],
            "source_path": repository_path,
            "source_line": source_line,
            "collection": candidate["collection"],
            "subjects": subjects,
            "docstring": docstring,
            "summary": compact_summary(docstring, theorem),
            "declaration_type_source": declaration_type,
            "pinned_source_url": pinned_url,
            "candidate_declarations_in_source": candidates_per_path[relative_path],
            "other_open_prs": open_prs,
            "flags": flags,
            "hard_reject_reasons": hard_reject_reasons,
            "hold_reasons": hold_reasons,
            "manual_decision": manual_decision,
            "prior_feasibility_signals": selection_by_theorem.get(
                theorem, {}
            ).get("feasibility_signals", []),
        }
        if record["prior_feasibility_signals"]:
            record["flags"].append("prior_feasibility_screen")
        record["review_lane"] = review_lane(
            record["collection"], flags, hard_reject_reasons, hold_reasons
        )
        records.append(record)

    records.sort(
        key=lambda item: (
            item["review_lane"],
            item["collection"].lower(),
            item["source_path"].lower(),
            item["theorem"].lower(),
        )
    )

    lane_counts = Counter(record["review_lane"] for record in records)
    collection_counts = Counter(record["collection"] for record in records)
    flag_counts = Counter(
        flag for record in records for flag in record["flags"]
    )
    subject_counts = Counter(
        (subject["code"], subject["name"])
        for record in records
        for subject in record["subjects"]
    )

    payload = {
        "schema_version": 1,
        "audit_date_utc": args.audit_date,
        "formal_conjectures_commit": args.upstream_commit,
        "candidate_policy": (
            "Direct research-open theorem or lemma; not in a retired release; "
            "current structured Erdős status allowed; passed the preliminary "
            "path-level PR screen; excludes the 29 tasks admitted at the audit boundary. "
            "Row-level rejection and hold decisions are recorded separately."
        ),
        "candidate_count": len(records),
        "remaining_after_exact_shape_rescan": sum(
            not {
                "unsupported_answer_hole",
                "pointer_alias_not_distinct_task",
                "type_contains_sorry",
                "type_extraction_failed",
            }.intersection(record["hard_reject_reasons"])
            for record in records
        ),
        "provisional_remaining_after_manual_decisions": sum(
            not record["hard_reject_reasons"] and not record["hold_reasons"]
            for record in records
        ),
        "source_file_count": len(candidates_per_path),
        "review_lane_definitions": {
            "a": (
                "Erdős tracker-backed, whole-looking single candidate in its "
                "source, with a docstring and no open PR requiring diff review."
            ),
            "b": (
                "Erdős tracker-backed but needs structural, grouping, "
                "docstring, or open-PR review."
            ),
            "c": (
                "Non-Erdős, whole-looking single candidate in its source, "
                "with a docstring and no open PR requiring diff review; independent "
                "literature review is still required."
            ),
            "d": (
                "Non-Erdős and also needs structural, grouping, docstring, "
                "or open-PR review."
            ),
            "rejected": (
                "Fails the current exact-task type policy, most commonly "
                "because the declaration type still contains an answer hole, "
                "or has been manually confirmed as ineligible."
            ),
            "hold": (
                "Potentially eligible, but blocked on an active correction or "
                "another concrete external-state review."
            ),
        },
        "counts": {
            "by_review_lane": dict(sorted(lane_counts.items())),
            "by_collection": dict(sorted(collection_counts.items())),
            "by_flag": dict(sorted(flag_counts.items())),
            "by_subject": [
                {"code": code, "name": name, "count": count}
                for (code, name), count in sorted(
                    subject_counts.items(),
                    key=lambda item: (-item[1], item[0][0]),
                )
            ],
        },
        "candidates": records,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# Additional direct-candidate inventory",
        "",
        f"Audit date: `{args.audit_date}`",
        "",
        f"Formal Conjectures commit: `{args.upstream_commit}`",
        "",
        (
            f"This inventory contains **{len(records)} declarations across "
            f"{len(candidates_per_path)} source files**. It excludes the 29 "
            "tasks admitted at the audit boundary. A row is a review candidate, not an "
            "admitted or certified task."
        ),
        "",
        (
            f"After exact-shape rejection and current manual decisions, "
            f"**{payload['provisional_remaining_after_manual_decisions']}** "
            "remain as provisional additions."
        ),
        "",
        "## Review lanes",
        "",
        "| Lane | Count | Meaning |",
        "|---|---:|---|",
    ]
    for lane in ("a", "b", "c", "d", "hold", "rejected"):
        lines.append(
            f"| `{lane}` | {lane_counts[lane]} | "
            f"{payload['review_lane_definitions'][lane]} |"
        )

    lines.extend(
        [
            "",
            "## Collections",
            "",
            "| Collection | Declarations |",
            "|---|---:|",
        ]
    )
    for collection, count in sorted(
        collection_counts.items(), key=lambda item: (-item[1], item[0])
    ):
        lines.append(f"| {collection} | {count} |")

    lines.extend(
        [
            "",
            "## Review flags",
            "",
            "| Flag | Declarations |",
            "|---|---:|",
        ]
    )
    for flag, count in sorted(
        flag_counts.items(), key=lambda item: (-item[1], item[0])
    ):
        lines.append(f"| `{flag}` | {count} |")

    lines.extend(
        [
            "",
            "## Candidates",
            "",
            "| Lane | Collection | Theorem | Subjects | Flags | Summary |",
            "|---|---|---|---|---|---|",
        ]
    )
    for record in records:
        subjects = ", ".join(
            f"{subject['code']} {subject['name']}"
            for subject in record["subjects"]
        )
        flags = ", ".join(record["flags"])
        theorem_link = (
            f"[`{markdown_escape(record['theorem'])}`]"
            f"({record['pinned_source_url']})"
        )
        lines.append(
            f"| `{record['review_lane']}` | "
            f"{markdown_escape(record['collection'])} | "
            f"{theorem_link} | "
            f"{markdown_escape(subjects)} | "
            f"{markdown_escape(flags)} | "
            f"{markdown_escape(record['summary'])} |"
        )

    args.output_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
