from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

from verifier.errors import ReasonCode, VerifierError
from verifier.hashing import pretty_json, sha256_bytes
from verifier.models import Catalog, CatalogDeclaration, TaskManifest
from verifier.task_loader import TaskBundle
from verifier.task_policy import GOLD_TASK_MODE, production_eligibility


GOLD_POOL_SCHEMA_VERSION = 4
GOLD_SELECTION_AUDIT_SCHEMA_VERSION = 1
GOLD_TASK_GROUP_SCHEMA_VERSION = 1
GOLD_WHOLE_PROBLEM_SCHEMA_VERSION = 1
DEFAULT_GOLD_POOL_SIZE = 29
DEFAULT_GOLD_TASK_COUNT = 29
MINIMUM_ERDOS_TASKS = DEFAULT_GOLD_POOL_SIZE
GOLD_POOL_SELECTION = "audited-erdos-whole-problem-v1"
GOLD_POOL_GROUPING = "none-single-target-v1"
GOLD_POOL_TASK_SCOPE = "whole_problem"
ERDOS_SOURCE_PREFIX = "FormalConjectures/ErdosProblems/"
EXCLUDED_SOURCE_PREFIXES = ("FormalConjectures/WrittenOnTheWallII/",)
FEASIBILITY_SIGNALS = frozenset(
    {
        "compact-formal-target",
        "discrete-domain",
        "finite-or-finitary-structure",
        "partial-results-in-source",
        "standard-mathlib-surface",
    }
)
UNSOLVED_ERDOS_STATUSES = frozenset(
    {
        "decidable",
        "falsifiable",
        "open",
        "verifiable",
    }
)
SCREENING_STATEMENT = (
    "Plausibly attackable solver target; this is a comparative screen, not a "
    "claim that the conjecture is easy or guaranteed solvable."
)


@dataclass(frozen=True)
class RetiredSources:
    repository_commit: str
    theorems: frozenset[str]
    type_hashes: frozenset[str]
    sha256: str


@dataclass(frozen=True)
class AuditedSelectionEntry:
    theorem: str
    source_path: str
    erdos_problem_number: int
    problem_tracker_status: str
    feasibility_signals: tuple[str, ...]
    open_prs_touching_source: tuple[int, ...]


@dataclass(frozen=True)
class SelectionAudit:
    repository_commit: str
    source_main_commit: str
    problem_tracker_commit: str
    audit_date_utc: str
    github_open_pr_count: int
    entries: tuple[AuditedSelectionEntry, ...]
    sha256: str

    @property
    def theorems(self) -> tuple[str, ...]:
        return tuple(entry.theorem for entry in self.entries)


@dataclass(frozen=True)
class AuditedTaskGroup:
    identifier: str
    source_path: str
    theorems: tuple[str, ...]


@dataclass(frozen=True)
class TaskGrouping:
    groups: tuple[AuditedTaskGroup, ...]
    sha256: str


@dataclass(frozen=True)
class WholeProblemTarget:
    theorem: str
    source_path: str
    erdos_problem_number: int


@dataclass(frozen=True)
class WholeProblemTargets:
    repository_commit: str
    targets: tuple[WholeProblemTarget, ...]
    sha256: str

    @property
    def theorems(self) -> tuple[str, ...]:
        return tuple(target.theorem for target in self.targets)


def _is_commit(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(character in "0123456789abcdef" for character in value)
    )


def load_selection_audit(path: Path) -> SelectionAudit:
    try:
        content = path.read_bytes()
        value = json.loads(content.decode("utf-8", errors="strict"))
        if not isinstance(value, dict) or set(value) != {
            "audit_date_utc",
            "github_open_pr_count",
            "problem_tracker_commit",
            "problem_tracker_repository",
            "repository_commit",
            "schema_version",
            "screening_statement",
            "selected",
            "source_main_commit",
            "source_repository",
        }:
            raise ValueError("selection audit field set is not exact")
        audit_date_utc = value["audit_date_utc"]
        selected = value["selected"]
        if (
            type(value["schema_version"]) is not int
            or value["schema_version"] != GOLD_SELECTION_AUDIT_SCHEMA_VERSION
            or not _is_commit(value["repository_commit"])
            or not _is_commit(value["source_main_commit"])
            or not _is_commit(value["problem_tracker_commit"])
            or value["problem_tracker_repository"] != "teorth/erdosproblems"
            or value["source_repository"] != "google-deepmind/formal-conjectures"
            or value["screening_statement"] != SCREENING_STATEMENT
            or type(value["github_open_pr_count"]) is not int
            or value["github_open_pr_count"] <= 0
            or not isinstance(audit_date_utc, str)
            or date.fromisoformat(audit_date_utc).isoformat() != audit_date_utc
            or not isinstance(selected, list)
            or not selected
        ):
            raise ValueError("selection audit metadata is invalid")
        entries = tuple(
            AuditedSelectionEntry(
                theorem=item["theorem"],
                source_path=item["source_path"],
                erdos_problem_number=item["erdos_problem_number"],
                problem_tracker_status=item["problem_tracker_status"],
                feasibility_signals=tuple(item["feasibility_signals"]),
                open_prs_touching_source=tuple(item["open_prs_touching_source"]),
            )
            for item in selected
            if isinstance(item, dict)
            and set(item)
            == {
                "active_resolution_prs",
                "erdos_problem_number",
                "feasibility_signals",
                "open_prs_touching_source",
                "problem_tracker_status",
                "source_path",
                "theorem",
                "upstream_status",
            }
            and isinstance(item["theorem"], str)
            and item["theorem"]
            and isinstance(item["source_path"], str)
            and item["source_path"].startswith(ERDOS_SOURCE_PREFIX)
            and item["source_path"].endswith(".lean")
            and type(item["erdos_problem_number"]) is int
            and item["erdos_problem_number"] > 0
            and item["source_path"]
            == f"{ERDOS_SOURCE_PREFIX}{item['erdos_problem_number']}.lean"
            and isinstance(item["problem_tracker_status"], str)
            and item["problem_tracker_status"] in UNSOLVED_ERDOS_STATUSES
            and isinstance(item["feasibility_signals"], list)
            and item["feasibility_signals"]
            and item["feasibility_signals"] == sorted(item["feasibility_signals"])
            and len(item["feasibility_signals"]) == len(set(item["feasibility_signals"]))
            and set(item["feasibility_signals"]) <= FEASIBILITY_SIGNALS
            and isinstance(item["open_prs_touching_source"], list)
            and item["open_prs_touching_source"]
            == sorted(item["open_prs_touching_source"])
            and len(item["open_prs_touching_source"])
            == len(set(item["open_prs_touching_source"]))
            and all(
                type(number) is int and number > 0
                for number in item["open_prs_touching_source"]
            )
            and item["active_resolution_prs"] == []
            and item["upstream_status"] == "research open"
        )
        if (
            len(entries) != len(selected)
            or tuple(entry.theorem for entry in entries)
            != tuple(sorted(entry.theorem for entry in entries))
            or len({entry.theorem for entry in entries}) != len(entries)
        ):
            raise ValueError("selection audit entries are invalid")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, KeyError) as exc:
        raise VerifierError(
            ReasonCode.INVALID_ARGUMENT,
            f"cannot load gold selection audit: {exc}",
        ) from exc
    return SelectionAudit(
        repository_commit=value["repository_commit"],
        source_main_commit=value["source_main_commit"],
        problem_tracker_commit=value["problem_tracker_commit"],
        audit_date_utc=audit_date_utc,
        github_open_pr_count=value["github_open_pr_count"],
        entries=entries,
        sha256=sha256_bytes(content),
    )


def load_whole_problem_targets(path: Path) -> WholeProblemTargets:
    try:
        content = path.read_bytes()
        value = json.loads(content.decode("utf-8", errors="strict"))
        targets = value.get("targets") if isinstance(value, dict) else None
        if (
            not isinstance(value, dict)
            or set(value)
            != {"policy", "repository_commit", "schema_version", "targets"}
            or value.get("schema_version") != GOLD_WHOLE_PROBLEM_SCHEMA_VERSION
            or value.get("policy") != "one_task_one_complete_problem"
            or not _is_commit(value.get("repository_commit"))
            or not isinstance(targets, list)
            or not targets
        ):
            raise ValueError("whole-problem target metadata is invalid")
        parsed = tuple(
            WholeProblemTarget(
                theorem=item["theorem"],
                source_path=item["source_path"],
                erdos_problem_number=item["erdos_problem_number"],
            )
            for item in targets
            if isinstance(item, dict)
            and set(item) == {"erdos_problem_number", "source_path", "theorem"}
            and type(item.get("erdos_problem_number")) is int
            and item["erdos_problem_number"] > 0
            and isinstance(item.get("source_path"), str)
            and item["source_path"]
            == f"{ERDOS_SOURCE_PREFIX}{item['erdos_problem_number']}.lean"
            and isinstance(item.get("theorem"), str)
            and item["theorem"]
            in {
                (
                    f"Erdos{item['erdos_problem_number']}."
                    f"erdos_{item['erdos_problem_number']}"
                ),
                "Erdos274.herzog_schonheim",
            }
        )
        if (
            len(parsed) != len(targets)
            or tuple(target.theorem for target in parsed)
            != tuple(sorted(target.theorem for target in parsed))
            or len({target.theorem for target in parsed}) != len(parsed)
            or len({target.source_path for target in parsed}) != len(parsed)
            or len({target.erdos_problem_number for target in parsed}) != len(parsed)
        ):
            raise ValueError(
                "whole-problem targets must be sorted, canonical, and one per problem"
            )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, KeyError) as exc:
        raise VerifierError(
            ReasonCode.INVALID_ARGUMENT,
            f"cannot load whole-problem targets: {exc}",
        ) from exc
    return WholeProblemTargets(
        repository_commit=value["repository_commit"],
        targets=parsed,
        sha256=sha256_bytes(content),
    )


def load_task_grouping(path: Path) -> TaskGrouping:
    try:
        content = path.read_bytes()
        value = json.loads(content.decode("utf-8", errors="strict"))
        groups = value.get("groups") if isinstance(value, dict) else None
        if (
            not isinstance(value, dict)
            or set(value)
            != {"completion_policy", "groups", "schema_version"}
            or value.get("schema_version") != GOLD_TASK_GROUP_SCHEMA_VERSION
            or value.get("completion_policy") != "all_of"
            or not isinstance(groups, list)
        ):
            raise ValueError("task grouping metadata is invalid")
        parsed = tuple(
            AuditedTaskGroup(
                identifier=item["id"],
                source_path=item["source_path"],
                theorems=tuple(item["theorems"]),
            )
            for item in groups
            if isinstance(item, dict)
            and set(item) == {"id", "source_path", "theorems"}
            and isinstance(item.get("id"), str)
            and item["id"]
            and all(
                character in "abcdefghijklmnopqrstuvwxyz0123456789-"
                for character in item["id"]
            )
            and isinstance(item.get("source_path"), str)
            and item["source_path"].startswith(ERDOS_SOURCE_PREFIX)
            and item["source_path"].endswith(".lean")
            and isinstance(item.get("theorems"), list)
            and len(item["theorems"]) >= 2
            and all(isinstance(theorem, str) and theorem for theorem in item["theorems"])
            and len(item["theorems"]) == len(set(item["theorems"]))
        )
        theorem_names = tuple(
            theorem
            for group in parsed
            for theorem in group.theorems
        )
        if (
            len(parsed) != len(groups)
            or tuple(group.identifier for group in parsed)
            != tuple(sorted(group.identifier for group in parsed))
            or len({group.identifier for group in parsed}) != len(parsed)
            or len(theorem_names) != len(set(theorem_names))
        ):
            raise ValueError("task grouping entries are invalid or duplicate")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, KeyError) as exc:
        raise VerifierError(
            ReasonCode.INVALID_ARGUMENT,
            f"cannot load gold task grouping: {exc}",
        ) from exc
    return TaskGrouping(groups=parsed, sha256=sha256_bytes(content))


def load_retired_sources(path: Path) -> RetiredSources:
    try:
        content = path.read_bytes()
        value = json.loads(content.decode("utf-8", errors="strict"))
        if not isinstance(value, dict) or set(value) != {
            "repository_commit",
            "schema_version",
            "source_theorems",
            "source_type_sha256",
        }:
            raise ValueError("retired source field set is not exact")
        repository_commit = value["repository_commit"]
        theorems = value["source_theorems"]
        type_hashes = value["source_type_sha256"]
        if (
            type(value["schema_version"]) is not int
            or value["schema_version"] != 2
            or not isinstance(repository_commit, str)
            or len(repository_commit) != 40
            or any(character not in "0123456789abcdef" for character in repository_commit)
            or not isinstance(theorems, list)
            or not theorems
            or not all(isinstance(theorem, str) and theorem for theorem in theorems)
            or theorems != sorted(theorems)
            or len(theorems) != len(set(theorems))
            or not isinstance(type_hashes, list)
            or not type_hashes
            or not all(
                isinstance(type_hash, str)
                and len(type_hash) == 71
                and type_hash.startswith("sha256:")
                and all(character in "0123456789abcdef" for character in type_hash[7:])
                for type_hash in type_hashes
            )
            or type_hashes != sorted(type_hashes)
            or len(type_hashes) != len(set(type_hashes))
        ):
            raise ValueError("retired source data is invalid")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise VerifierError(
            ReasonCode.INVALID_ARGUMENT,
            f"cannot load retired gold sources: {exc}",
        ) from exc
    return RetiredSources(
        repository_commit=repository_commit,
        theorems=frozenset(theorems),
        type_hashes=frozenset(type_hashes),
        sha256=sha256_bytes(content),
    )


def select_gold_declarations(
    *,
    catalog: Catalog,
    retired: RetiredSources,
    selection_audit: SelectionAudit,
    whole_problem_targets: WholeProblemTargets,
    pool_size: int = DEFAULT_GOLD_POOL_SIZE,
) -> tuple[CatalogDeclaration, ...]:
    if (
        retired.repository_commit != catalog.repository_commit
        or selection_audit.repository_commit != catalog.repository_commit
        or whole_problem_targets.repository_commit != catalog.repository_commit
    ):
        raise VerifierError(
            ReasonCode.REPOSITORY_COMMIT_MISMATCH,
            "gold audit inputs and catalog use different repository commits",
        )
    if pool_size <= 0:
        raise VerifierError(ReasonCode.INVALID_ARGUMENT, "gold pool size must be positive")
    if len(whole_problem_targets.targets) != pool_size:
        raise VerifierError(
            ReasonCode.INVALID_ARGUMENT,
            (
                "whole-problem target policy contains "
                f"{len(whole_problem_targets.targets)} tasks, expected {pool_size}"
            ),
        )

    by_theorem = {declaration.theorem: declaration for declaration in catalog.declarations}
    audited_by_theorem = {
        entry.theorem: entry
        for entry in selection_audit.entries
    }
    selected: list[CatalogDeclaration] = []
    used_types: set[str] = set()
    for target in whole_problem_targets.targets:
        entry = audited_by_theorem.get(target.theorem)
        if (
            entry is None
            or entry.source_path != target.source_path
            or entry.erdos_problem_number != target.erdos_problem_number
        ):
            raise VerifierError(
                ReasonCode.INVALID_ARGUMENT,
                f"whole-problem target is not in the audited selection: {target.theorem}",
            )
        declaration = by_theorem.get(target.theorem)
        if declaration is None:
            raise VerifierError(
                ReasonCode.THEOREM_NOT_FOUND,
                f"audited gold theorem is missing from the pinned catalog: {entry.theorem}",
            )
        eligible, violations, _collisions = production_eligibility(
            catalog,
            declaration,
            GOLD_TASK_MODE,
        )
        if (
            not eligible
            or declaration.source_path != entry.source_path
            or declaration.theorem in retired.theorems
            or declaration.type_hash in retired.type_hashes
            or declaration.type_hash in used_types
            or declaration.source_path.startswith(EXCLUDED_SOURCE_PREFIXES)
        ):
            detail = "; ".join(violations) if violations else "freshness or source policy failed"
            raise VerifierError(
                ReasonCode.INVALID_ARGUMENT,
                f"audited gold theorem is ineligible: {entry.theorem}: {detail}",
            )
        used_types.add(declaration.type_hash)
        selected.append(declaration)
    erdos_count = sum(
        declaration.source_path.startswith(ERDOS_SOURCE_PREFIX)
        for declaration in selected
    )
    if erdos_count < MINIMUM_ERDOS_TASKS:
        raise VerifierError(
            ReasonCode.INVALID_ARGUMENT,
            f"gold pool has {erdos_count} Erdős tasks, expected at least {MINIMUM_ERDOS_TASKS}",
        )
    return tuple(selected)


def group_gold_declarations(
    declarations: Iterable[CatalogDeclaration],
    grouping: TaskGrouping,
) -> tuple[tuple[CatalogDeclaration, ...], ...]:
    selected = tuple(declarations)
    by_theorem = {item.theorem: item for item in selected}
    grouped_theorems = {
        theorem
        for group in grouping.groups
        for theorem in group.theorems
    }
    if not grouped_theorems <= set(by_theorem):
        missing = sorted(grouped_theorems - set(by_theorem))
        raise VerifierError(
            ReasonCode.INVALID_ARGUMENT,
            "task grouping contains unselected theorems: " + ", ".join(missing),
        )
    group_by_theorem = {
        theorem: group
        for group in grouping.groups
        for theorem in group.theorems
    }
    emitted: set[str] = set()
    result = []
    for declaration in selected:
        group = group_by_theorem.get(declaration.theorem)
        if group is None:
            result.append((declaration,))
            continue
        if group.identifier in emitted:
            continue
        members = tuple(by_theorem[theorem] for theorem in group.theorems)
        if (
            any(item.source_path != group.source_path for item in members)
            or len({item.module for item in members}) != 1
            or len({item.type_hash for item in members}) != len(members)
        ):
            raise VerifierError(
                ReasonCode.INVALID_ARGUMENT,
                f"task group is not a coherent exact-source group: {group.identifier}",
            )
        result.append(members)
        emitted.add(group.identifier)
    if (
        emitted != {group.identifier for group in grouping.groups}
        or sum(len(group) for group in result) != len(selected)
        or {
            item.theorem
            for group in result
            for item in group
        }
        != set(by_theorem)
    ):
        raise VerifierError(
            ReasonCode.INVALID_ARGUMENT,
            "task grouping does not cover the selected theorem set exactly once",
        )
    return tuple(result)


def build_gold_allowlist(
    *,
    catalog: Catalog,
    retired: RetiredSources,
    selection_audit: SelectionAudit,
    whole_problem_targets: WholeProblemTargets,
    grouping: TaskGrouping,
    selected: Iterable[tuple[CatalogDeclaration, ...]],
    bundles: Iterable[TaskBundle],
    audit_date_utc: str,
) -> bytes:
    declaration_groups = tuple(selected)
    task_bundles = tuple(bundles)
    if len(declaration_groups) != len(task_bundles) or not declaration_groups:
        raise VerifierError(
            ReasonCode.INVALID_ARGUMENT,
            "gold task groups and generated bundles must be non-empty and one-to-one",
        )
    declarations = tuple(
        declaration
        for group in declaration_groups
        for declaration in group
    )
    if (
        {declaration.theorem for declaration in declarations}
        != set(whole_problem_targets.theorems)
        or not set(whole_problem_targets.theorems) <= set(selection_audit.theorems)
        or any(len(group) != 1 for group in declaration_groups)
    ):
        raise VerifierError(
            ReasonCode.INVALID_ARGUMENT,
            "generated gold declarations are not one-target whole-problem tasks",
        )
    catalog_indices = {
        declaration.theorem: index
        for index, declaration in enumerate(catalog.declarations)
    }
    sources = []
    tasks = []
    for declarations_for_task, bundle in zip(
        declaration_groups,
        task_bundles,
        strict=True,
    ):
        manifest: TaskManifest = bundle.manifest
        primary = declarations_for_task[0]
        if (
            manifest.source_theorem != primary.theorem
            or manifest.task_mode != GOLD_TASK_MODE
            or not manifest.production_eligible
            or manifest.source_type_hash != primary.type_hash
            or manifest.generated_target_type_hash != primary.type_hash
            or tuple(item.theorem for item in bundle.sources)
            != tuple(item.theorem for item in declarations_for_task)
            or tuple(item.type_hash for item in bundle.sources)
            != tuple(item.type_hash for item in declarations_for_task)
        ):
            raise VerifierError(
                ReasonCode.INVALID_MANIFEST,
                f"generated gold task is not the exact source group: {primary.theorem}",
            )
        source_indices = []
        for declaration in declarations_for_task:
            source_index = catalog_indices[declaration.theorem]
            source_indices.append(source_index)
            sources.append(
                {
                    "index": source_index,
                    "source_path": declaration.source_path,
                    "source_type_sha256": declaration.type_hash,
                    "theorem": declaration.theorem,
                }
            )
        tasks.append(
            {
                "completion_policy": "all_of",
                "mode": GOLD_TASK_MODE,
                "source_indices": source_indices,
                "source_path": primary.source_path,
                "target_type_sha256s": [
                    declaration.type_hash
                    for declaration in declarations_for_task
                ],
                "task_bundle_sha256": bundle.sha256,
                "task_id": manifest.task_id,
                "theorems": [
                    declaration.theorem
                    for declaration in declarations_for_task
                ],
            }
        )
    value = {
        "allowed_source_theorems": sources,
        "allowed_task_bundles": tasks,
        "audit_date_utc": audit_date_utc,
        "default": "DENY",
        "pool_policy": {
            "classification": "DIRECT_PROP",
            "compiled_target_validation": True,
            "exact_source_type": True,
            "excluded_source_prefixes": list(EXCLUDED_SOURCE_PREFIXES),
            "grouping": GOLD_POOL_GROUPING,
            "minimum_erdos_tasks": MINIMUM_ERDOS_TASKS,
            "mode": GOLD_TASK_MODE,
            "multi_target_tasks": sum(
                len(group) > 1
                for group in declaration_groups
            ),
            "one_task_per_source_path": True,
            "pool_size": len(declaration_groups),
            "retired_source_theorems_sha256": retired.sha256,
            "selection": GOLD_POOL_SELECTION,
            "selection_audit_sha256": selection_audit.sha256,
            "source_category": "research open",
            "source_theorem_count": len(declarations),
            "synthetic_negation": False,
            "task_scope": GOLD_POOL_TASK_SCOPE,
            "task_groups_sha256": grouping.sha256,
            "whole_problem_targets_sha256": whole_problem_targets.sha256,
        },
        "repository_commit": catalog.repository_commit,
        "schema_version": GOLD_POOL_SCHEMA_VERSION,
    }
    return pretty_json(value).encode("utf-8")
