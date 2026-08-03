from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

from verifier.errors import ReasonCode, VerifierError
from verifier.hashing import pretty_json, sha256_bytes
from verifier.models import Catalog, CatalogDeclaration, TaskManifest
from verifier.task_loader import TaskBundle
from verifier.task_generator import problem_id
from verifier.task_policy import (
    COUNTEREXAMPLE_TASK_MODE,
    EXACT_TASK_MODE,
    PRODUCTION_TASK_MODES,
    production_eligibility,
)


TASK_POOL_SCHEMA_VERSION = 7
SELECTION_AUDIT_SCHEMA_VERSION = 1
TASK_GROUP_SCHEMA_VERSION = 1
TASK_TARGET_SCHEMA_VERSION = 2
DEFAULT_TASK_TIER = "tier-1"
SUBPROBLEM_TASK_TIER = "tier-2"
TASK_TIER = re.compile(r"^tier-[1-9][0-9]*$")
DEFAULT_TIER_SIZE = 29
SUBPROBLEM_TIER_SIZE = 45
DEFAULT_TIER_TASK_COUNT = DEFAULT_TIER_SIZE * len(PRODUCTION_TASK_MODES)
SUBPROBLEM_TIER_TASK_COUNT = SUBPROBLEM_TIER_SIZE * len(PRODUCTION_TASK_MODES)
MINIMUM_ERDOS_TASKS = DEFAULT_TIER_SIZE
TASK_POOL_SELECTION = "audited-erdos-whole-problem-v1"
SUBPROBLEM_POOL_SELECTION = "audited-erdos-part-or-variant-v1"
TASK_POOL_GROUPING = "none-single-target-v1"
TASK_POOL_TASK_SCOPE = "whole_problem"
SUBPROBLEM_TASK_SCOPE = "part_or_variant"
WHOLE_PROBLEM_POLICY = "one_task_one_complete_problem"
SUBPROBLEM_POLICY = "one_task_one_audited_subproblem"
REWARD_FAMILY_POLICY = "stable-erdos-number-v1"
ERDOS_SOURCE_PREFIX = "FormalConjectures/ErdosProblems/"
EXCLUDED_SOURCE_PREFIXES: tuple[str, ...] = ()
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
class TaskTarget:
    theorem: str
    source_path: str
    erdos_problem_number: int
    reward_family_id: str


@dataclass(frozen=True)
class TaskTargets:
    repository_commit: str
    policy: str
    task_scope: str
    targets: tuple[TaskTarget, ...]
    sha256: str

    @property
    def theorems(self) -> tuple[str, ...]:
        return tuple(target.theorem for target in self.targets)

    @property
    def reward_families(self) -> dict[str, str]:
        return {target.theorem: target.reward_family_id for target in self.targets}


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
            or value["schema_version"] != SELECTION_AUDIT_SCHEMA_VERSION
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
            f"cannot load task-pool selection audit: {exc}",
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


def load_task_targets(path: Path) -> TaskTargets:
    try:
        content = path.read_bytes()
        value = json.loads(content.decode("utf-8", errors="strict"))
        targets = value.get("targets") if isinstance(value, dict) else None
        if (
            not isinstance(value, dict)
            or set(value)
            != {"policy", "repository_commit", "schema_version", "task_scope", "targets"}
            or value.get("schema_version") != TASK_TARGET_SCHEMA_VERSION
            or value.get("policy") not in {WHOLE_PROBLEM_POLICY, SUBPROBLEM_POLICY}
            or value.get("task_scope") not in {TASK_POOL_TASK_SCOPE, SUBPROBLEM_TASK_SCOPE}
            or (
                value.get("policy") == WHOLE_PROBLEM_POLICY
                and value.get("task_scope") != TASK_POOL_TASK_SCOPE
            )
            or (
                value.get("policy") == SUBPROBLEM_POLICY
                and value.get("task_scope") != SUBPROBLEM_TASK_SCOPE
            )
            or not _is_commit(value.get("repository_commit"))
            or not isinstance(targets, list)
            or not targets
        ):
            raise ValueError("task target metadata is invalid")
        parsed = tuple(
            TaskTarget(
                theorem=item["theorem"],
                source_path=item["source_path"],
                erdos_problem_number=item["erdos_problem_number"],
                reward_family_id=item["reward_family_id"],
            )
            for item in targets
            if isinstance(item, dict)
            and set(item)
            == {"erdos_problem_number", "reward_family_id", "source_path", "theorem"}
            and type(item.get("erdos_problem_number")) is int
            and item["erdos_problem_number"] > 0
            and isinstance(item.get("source_path"), str)
            and item["source_path"]
            == f"{ERDOS_SOURCE_PREFIX}{item['erdos_problem_number']}.lean"
            and isinstance(item.get("theorem"), str)
            and isinstance(item.get("reward_family_id"), str)
            and item["reward_family_id"] == f"erdos-{item['erdos_problem_number']}"
            and (
                (
                    value.get("policy") == WHOLE_PROBLEM_POLICY
                    and item["theorem"]
                    in {
                        (
                            f"Erdos{item['erdos_problem_number']}."
                            f"erdos_{item['erdos_problem_number']}"
                        ),
                        "Erdos274.herzog_schonheim",
                    }
                )
                or (
                    value.get("policy") == SUBPROBLEM_POLICY
                    and item["theorem"].startswith(
                        f"Erdos{item['erdos_problem_number']}.erdos_{item['erdos_problem_number']}."
                    )
                )
            )
        )
        if (
            len(parsed) != len(targets)
            or tuple(target.theorem for target in parsed)
            != tuple(sorted(target.theorem for target in parsed))
            or len({target.theorem for target in parsed}) != len(parsed)
            or (
                value.get("policy") == WHOLE_PROBLEM_POLICY
                and (
                    len({target.source_path for target in parsed}) != len(parsed)
                    or len({target.erdos_problem_number for target in parsed}) != len(parsed)
                )
            )
        ):
            raise ValueError("task targets must be sorted, canonical, and unique")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, KeyError) as exc:
        raise VerifierError(
            ReasonCode.INVALID_ARGUMENT,
            f"cannot load task targets: {exc}",
        ) from exc
    return TaskTargets(
        repository_commit=value["repository_commit"],
        policy=value["policy"],
        task_scope=value["task_scope"],
        targets=parsed,
        sha256=sha256_bytes(content),
    )


def load_whole_problem_targets(path: Path) -> TaskTargets:
    targets = load_task_targets(path)
    if targets.policy != WHOLE_PROBLEM_POLICY:
        raise VerifierError(
            ReasonCode.INVALID_ARGUMENT,
            "target file is not a whole-problem policy",
        )
    return targets


def load_task_grouping(path: Path) -> TaskGrouping:
    try:
        content = path.read_bytes()
        value = json.loads(content.decode("utf-8", errors="strict"))
        groups = value.get("groups") if isinstance(value, dict) else None
        if (
            not isinstance(value, dict)
            or set(value)
            != {"completion_policy", "groups", "schema_version"}
            or value.get("schema_version") != TASK_GROUP_SCHEMA_VERSION
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
            f"cannot load task-pool grouping: {exc}",
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
            f"cannot load retired task-pool sources: {exc}",
        ) from exc
    return RetiredSources(
        repository_commit=repository_commit,
        theorems=frozenset(theorems),
        type_hashes=frozenset(type_hashes),
        sha256=sha256_bytes(content),
    )


def select_task_declarations(
    *,
    catalog: Catalog,
    retired: RetiredSources,
    selection_audit: SelectionAudit,
    task_targets: TaskTargets | None = None,
    whole_problem_targets: TaskTargets | None = None,
    pool_size: int = DEFAULT_TIER_SIZE,
) -> tuple[CatalogDeclaration, ...]:
    targets = task_targets if task_targets is not None else whole_problem_targets
    if targets is None or (task_targets is not None and whole_problem_targets is not None):
        raise VerifierError(
            ReasonCode.INVALID_ARGUMENT,
            "exactly one task target policy is required",
        )
    if (
        retired.repository_commit != catalog.repository_commit
        or selection_audit.repository_commit != catalog.repository_commit
        or targets.repository_commit != catalog.repository_commit
    ):
        raise VerifierError(
            ReasonCode.REPOSITORY_COMMIT_MISMATCH,
            "task-pool audit inputs and catalog use different repository commits",
        )
    if pool_size <= 0:
        raise VerifierError(ReasonCode.INVALID_ARGUMENT, "task tier size must be positive")
    if len(targets.targets) != pool_size:
        raise VerifierError(
            ReasonCode.INVALID_ARGUMENT,
            (
                "task target policy contains "
                f"{len(targets.targets)} tasks, expected {pool_size}"
            ),
        )

    by_theorem = {declaration.theorem: declaration for declaration in catalog.declarations}
    audited_by_theorem = {
        entry.theorem: entry
        for entry in selection_audit.entries
    }
    selected: list[CatalogDeclaration] = []
    used_types: set[str] = set()
    for target in targets.targets:
        entry = audited_by_theorem.get(target.theorem)
        if (
            entry is None
            or entry.source_path != target.source_path
            or entry.erdos_problem_number != target.erdos_problem_number
        ):
            raise VerifierError(
                ReasonCode.INVALID_ARGUMENT,
                f"task target is not in the audited selection: {target.theorem}",
            )
        declaration = by_theorem.get(target.theorem)
        if declaration is None:
            raise VerifierError(
                ReasonCode.THEOREM_NOT_FOUND,
                f"audited task theorem is missing from the pinned catalog: {entry.theorem}",
            )
        eligible, violations, _collisions = production_eligibility(
            catalog,
            declaration,
            EXACT_TASK_MODE,
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
                f"audited task theorem is ineligible: {entry.theorem}: {detail}",
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
            f"task tier has {erdos_count} Erdős tasks, expected at least {MINIMUM_ERDOS_TASKS}",
        )
    return tuple(selected)


def group_task_declarations(
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


def build_task_allowlist(
    *,
    catalog: Catalog,
    retired: RetiredSources,
    selection_audit: SelectionAudit,
    task_targets: TaskTargets,
    grouping: TaskGrouping,
    selected: Iterable[tuple[CatalogDeclaration, ...]],
    bundles: Iterable[TaskBundle],
    audit_date_utc: str,
    tier: str = DEFAULT_TASK_TIER,
) -> bytes:
    if not isinstance(tier, str) or TASK_TIER.fullmatch(tier) is None:
        raise VerifierError(
            ReasonCode.INVALID_ARGUMENT,
            f"unsupported task-pool tier: {tier}",
        )
    declaration_groups = tuple(selected)
    task_bundles = tuple(bundles)
    if (
        len(task_bundles) != len(declaration_groups) * len(PRODUCTION_TASK_MODES)
        or not declaration_groups
    ):
        raise VerifierError(
            ReasonCode.INVALID_ARGUMENT,
            "every task group must have one generated bundle for each production mode",
        )
    declarations = tuple(
        declaration
        for group in declaration_groups
        for declaration in group
    )
    if (
        {declaration.theorem for declaration in declarations}
        != set(task_targets.theorems)
        or not set(task_targets.theorems) <= set(selection_audit.theorems)
        or any(len(group) != 1 for group in declaration_groups)
    ):
        raise VerifierError(
            ReasonCode.INVALID_ARGUMENT,
            "generated declarations are not audited single-target tasks",
        )
    catalog_indices = {
        declaration.theorem: index
        for index, declaration in enumerate(catalog.declarations)
    }
    sources = [
        {
            "index": catalog_indices[declaration.theorem],
            "source_path": declaration.source_path,
            "source_type_sha256": declaration.type_hash,
            "theorem": declaration.theorem,
            "tier": tier,
        }
        for declaration in declarations
    ]
    bundles_by_identity = {
        (
            tuple(source.theorem for source in bundle.sources),
            bundle.manifest.task_mode,
        ): bundle
        for bundle in task_bundles
    }
    expected_identities = {
        (tuple(item.theorem for item in group), mode)
        for group in declaration_groups
        for mode in PRODUCTION_TASK_MODES
    }
    if len(bundles_by_identity) != len(task_bundles) or set(bundles_by_identity) != expected_identities:
        raise VerifierError(
            ReasonCode.INVALID_MANIFEST,
            "generated task bundles do not form complete proof/counterexample pairs",
        )

    tasks = []
    reward_families = task_targets.reward_families
    for declarations_for_task in declaration_groups:
        primary = declarations_for_task[0]
        theorem_names = tuple(item.theorem for item in declarations_for_task)
        source_indices = [catalog_indices[item.theorem] for item in declarations_for_task]
        source_hashes = tuple(item.type_hash for item in declarations_for_task)
        for mode in PRODUCTION_TASK_MODES:
            bundle = bundles_by_identity[(theorem_names, mode)]
            manifest: TaskManifest = bundle.manifest
            target_hashes = (
                source_hashes
                if mode == EXACT_TASK_MODE
                else (manifest.generated_target_type_hash,)
            )
            if (
                manifest.source_theorem != primary.theorem
                or manifest.task_mode != mode
                or not manifest.production_eligible
                or manifest.source_type_hash != primary.type_hash
                or tuple(item.theorem for item in bundle.sources) != theorem_names
                or tuple(item.type_hash for item in bundle.sources) != source_hashes
                or (mode == EXACT_TASK_MODE and target_hashes != source_hashes)
                or (
                    mode == COUNTEREXAMPLE_TASK_MODE
                    and (
                        len(declarations_for_task) != 1
                        or target_hashes[0] == source_hashes[0]
                    )
                )
            ):
                raise VerifierError(
                    ReasonCode.INVALID_MANIFEST,
                    f"generated task has an invalid target relation: {primary.theorem} ({mode})",
                )
            tasks.append(
                {
                    "completion_policy": "all_of",
                    "mode": mode,
                    "problem_id": problem_id(catalog.repository_commit, theorem_names),
                    "reward_family_id": reward_families[primary.theorem],
                    "source_indices": source_indices,
                    "source_path": primary.source_path,
                    "target_type_sha256s": list(target_hashes),
                    "task_bundle_sha256": bundle.sha256,
                    "task_id": manifest.task_id,
                    "theorems": list(theorem_names),
                    "tier": tier,
                }
            )
    value = {
        "allowed_source_theorems": sources,
        "allowed_task_bundles": tasks,
        "audit_date_utc": audit_date_utc,
        "default": "DENY",
        "tier_order": [tier],
        "tier_policies": {
            tier: {
                "classification": "DIRECT_PROP",
                "compiled_target_validation": True,
                "excluded_source_prefixes": list(EXCLUDED_SOURCE_PREFIXES),
                "grouping": TASK_POOL_GROUPING,
                "minimum_erdos_tasks": MINIMUM_ERDOS_TASKS,
                "modes": list(PRODUCTION_TASK_MODES),
                "multi_target_tasks": sum(
                    len(group) > 1
                    for group in declaration_groups
                ) * len(PRODUCTION_TASK_MODES),
                "one_reward_per_problem": True,
                "one_reward_per_reward_family": True,
                "pool_size": len(tasks),
                "reward_family_count": len(set(reward_families.values())),
                "reward_family_policy": REWARD_FAMILY_POLICY,
                "retired_source_theorems_sha256": retired.sha256,
                "selection": (
                    TASK_POOL_SELECTION
                    if task_targets.policy == WHOLE_PROBLEM_POLICY
                    else SUBPROBLEM_POOL_SELECTION
                ),
                "selection_audit_sha256": selection_audit.sha256,
                "source_category": "research open",
                "source_theorem_count": len(declarations),
                "task_scope": task_targets.task_scope,
                "target_relations": {
                    COUNTEREXAMPLE_TASK_MODE: "logical-negation",
                    EXACT_TASK_MODE: "definitionally-equal",
                },
                "task_groups_sha256": grouping.sha256,
                "outcomes_per_problem": len(PRODUCTION_TASK_MODES),
                "task_targets_sha256": task_targets.sha256,
            }
        },
        "repository_commit": catalog.repository_commit,
        "schema_version": TASK_POOL_SCHEMA_VERSION,
    }
    return pretty_json(value).encode("utf-8")


def merge_task_allowlists(allowlists: Iterable[bytes]) -> bytes:
    documents = tuple(json.loads(content.decode("utf-8")) for content in allowlists)
    if not documents:
        raise VerifierError(ReasonCode.INVALID_ARGUMENT, "no tier allowlists to merge")
    first = documents[0]
    shared = ("audit_date_utc", "default", "repository_commit", "schema_version")
    if any(any(document.get(key) != first.get(key) for key in shared) for document in documents):
        raise VerifierError(
            ReasonCode.INVALID_ARGUMENT,
            "tier allowlists do not share one release identity",
        )
    tier_order = [tier for document in documents for tier in document["tier_order"]]
    policies = {
        tier: policy
        for document in documents
        for tier, policy in document["tier_policies"].items()
    }
    sources = [source for document in documents for source in document["allowed_source_theorems"]]
    tasks = [task for document in documents for task in document["allowed_task_bundles"]]
    if (
        len(tier_order) != len(set(tier_order))
        or set(tier_order) != set(policies)
        or len({source["theorem"] for source in sources}) != len(sources)
        or len({source["index"] for source in sources}) != len(sources)
        or len({task["task_id"] for task in tasks}) != len(tasks)
    ):
        raise VerifierError(ReasonCode.INVALID_ARGUMENT, "merged tier identities are duplicated")
    value = {
        "allowed_source_theorems": sources,
        "allowed_task_bundles": tasks,
        "audit_date_utc": first["audit_date_utc"],
        "default": first["default"],
        "tier_order": tier_order,
        "tier_policies": policies,
        "repository_commit": first["repository_commit"],
        "schema_version": first["schema_version"],
    }
    return pretty_json(value).encode("utf-8")
