"""The staged decision matrix for package-authored research targets 1-30.

The matrix records what review established about each staged target and which gates remain open.
It is not an admission input: nothing here can place a theorem in a task pool. Admission of a
`research-targets` source additionally requires the theorem in `ACTIVATED_RESEARCH_TARGETS`, a
reviewed validator constant, and a tier policy that names the family. Schema version 1 has no
activation state at all, so a version-1 matrix is consistent only while that constant is empty.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from verifier import task_pool
from verifier.errors import ReasonCode, VerifierError
from verifier.hashing import is_sha256, sha256_bytes
from verifier.task_pool import (
    NAMED_SOURCE_NAMESPACES,
    RESEARCH_TARGETS_SOURCE_FAMILY,
    REWARD_TARGET_POLICY,
    reward_target_identity,
    source_family_from_path,
)


RESEARCH_TARGET_DECISIONS_SCHEMA_VERSION = 1
RESEARCH_TARGET_COUNT = 30
# `source-review-accepted` records only that review left no target-specific gate. It is not a
# proof, a novelty certificate, production readiness, or permission to activate a reward.
DECISIONS = frozenset({"excluded", "hold", "source-review-accepted"})
REWARD_POLICIES = frozenset({"correlated-group", "excluded-implied-by", "independent"})
GATE_STATUSES = frozenset({"closed", "open"})
GROUP_POLICY = "hold-until-explicit-decision"
GATE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
# Review findings that a matrix edit alone must not undo: the package proves 10 implies 12, and
# 7-9 share one hypothetical minimal-counterexample antecedent.
EXCLUDED_AS_IMPLIED = {12: 10}
CORRELATED_TARGETS = (7, 8, 9)
# Targets 1-15 come from the first reviewed package, 16-30 from the second.
PACKAGE_SOURCES = (
    (range(1, 16), "FormalConjectures/ResearchTargets/Math15.lean"),
    (range(16, 31), "FormalConjectures/ResearchTargets/Math30.lean"),
)


@dataclass(frozen=True)
class ResearchTarget:
    number: int
    theorem: str
    definition: str
    source_path: str
    reward_target_id: str
    source_type_sha256: str | None
    decision: str
    reward_policy: str
    implied_by: int | None
    reward_group: str | None
    gates: tuple[str, ...]


@dataclass(frozen=True)
class ResearchTargetDecisions:
    targets: tuple[ResearchTarget, ...]
    open_global_gates: tuple[str, ...]
    sha256: str

    @property
    def admissible(self) -> frozenset[str]:
        """Always empty for schema version 1: the matrix cannot activate a target."""
        return frozenset()


def _strict_object(content: bytes) -> dict:
    def unique(pairs: list[tuple[str, object]]) -> dict:
        result: dict = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key: {key}")
            result[key] = value
        return result

    value = json.loads(
        content.decode("utf-8", errors="strict"),
        object_pairs_hook=unique,
        parse_constant=lambda item: (_ for _ in ()).throw(ValueError(f"invalid constant {item}")),
    )
    if not isinstance(value, dict):
        raise ValueError("decision matrix must be a JSON object")
    return value


def _expected_source(number: int) -> str:
    return next(path for numbers, path in PACKAGE_SOURCES if number in numbers)


def _sorted_unique_strings(value: object) -> bool:
    return (
        isinstance(value, list)
        and all(isinstance(item, str) and GATE_ID.fullmatch(item) for item in value)
        and value == sorted(value)
        and len(value) == len(set(value))
    )


def _gate_table(value: object, label: str) -> dict[str, str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be a non-empty list")
    statuses: dict[str, str] = {}
    for gate in value:
        if (
            not isinstance(gate, dict)
            or set(gate) != {"description", "evidence", "id", "status"}
            or not isinstance(gate["id"], str)
            or GATE_ID.fullmatch(gate["id"]) is None
            or gate["id"] in statuses
            or not isinstance(gate["description"], str)
            or not gate["description"].strip()
            or gate["status"] not in GATE_STATUSES
            or not (gate["evidence"] is None or isinstance(gate["evidence"], str))
            # A closed gate must name the evidence that closed it; an open one must not claim any.
            or (gate["status"] == "closed") != bool(gate["evidence"])
        ):
            raise ValueError(f"{label} contains an invalid gate")
        statuses[gate["id"]] = gate["status"]
    if list(statuses) != sorted(statuses):
        raise ValueError(f"{label} must be sorted by id")
    return statuses


def load_research_target_decisions(path: Path) -> ResearchTargetDecisions:
    try:
        content = path.read_bytes()
        value = _strict_object(content)
        if set(value) != {
            "activation",
            "default",
            "global_gates",
            "review_archive_sha256",
            "reward_groups",
            "reward_target_policy",
            "schema_version",
            "source_family",
            "target_gates",
            "targets",
        }:
            raise ValueError("decision matrix field set is not exact")
        if (
            type(value["schema_version"]) is not int
            or value["schema_version"] != RESEARCH_TARGET_DECISIONS_SCHEMA_VERSION
            or value["default"] != "DENY"
            or value["activation"] != "none"
            or value["source_family"] != RESEARCH_TARGETS_SOURCE_FAMILY
            or value["reward_target_policy"] != REWARD_TARGET_POLICY
            or not is_sha256(value["review_archive_sha256"])
        ):
            raise ValueError("decision matrix metadata is invalid")
        # Read at call time: the admission gate in `task_pool` and this check share one constant.
        if task_pool.ACTIVATED_RESEARCH_TARGETS:
            raise ValueError("schema version 1 cannot back an activated research target")
        global_gates = _gate_table(value["global_gates"], "global gates")
        if all(status == "closed" for status in global_gates.values()):
            # Closing every global gate is an activation decision, which version 1 cannot express.
            raise ValueError("a version-1 decision matrix must keep a global gate open")
        target_gates = _gate_table(value["target_gates"], "target gates")
        if set(global_gates) & set(target_gates):
            raise ValueError("global and target gate ids overlap")

        groups = value["reward_groups"]
        if not isinstance(groups, list):
            raise ValueError("reward groups must be a list")
        group_members: dict[str, tuple[int, ...]] = {}
        for group in groups:
            if (
                not isinstance(group, dict)
                or set(group) != {"id", "members", "policy", "rationale"}
                or not isinstance(group["id"], str)
                or GATE_ID.fullmatch(group["id"]) is None
                or group["id"] in group_members
                or group["policy"] != GROUP_POLICY
                or not isinstance(group["rationale"], str)
                or not group["rationale"].strip()
                or not isinstance(group["members"], list)
                or len(group["members"]) < 2
                or not all(type(item) is int for item in group["members"])
                or group["members"] != sorted(set(group["members"]))
            ):
                raise ValueError("decision matrix contains an invalid reward group")
            group_members[group["id"]] = tuple(group["members"])

        rows = value["targets"]
        if not isinstance(rows, list) or len(rows) != RESEARCH_TARGET_COUNT:
            raise ValueError(f"decision matrix must list exactly {RESEARCH_TARGET_COUNT} targets")
        targets: list[ResearchTarget] = []
        for expected_number, row in enumerate(rows, start=1):
            if not isinstance(row, dict) or set(row) != {
                "decision",
                "definition",
                "gates",
                "implied_by",
                "number",
                "review_disposition",
                "reward_group",
                "reward_policy",
                "reward_target_id",
                "source_path",
                "source_type_sha256",
                "theorem",
                "title",
            }:
                raise ValueError("decision matrix contains an invalid target entry")
            number = row["number"]
            theorem = row["theorem"]
            source_path = row["source_path"]
            if type(number) is not int or number != expected_number:
                raise ValueError("targets must be numbered 1-30 in order")
            namespace = NAMED_SOURCE_NAMESPACES.get(_expected_source(number))
            if (
                source_path != _expected_source(number)
                or source_family_from_path(source_path) != RESEARCH_TARGETS_SOURCE_FAMILY
                or theorem != f"{namespace}.source{number:02d}"
                or row["reward_target_id"] != reward_target_identity(theorem)
                or not isinstance(row["definition"], str)
                or not row["definition"].startswith(namespace.removesuffix("Catalog") + ".")
                or not (row["source_type_sha256"] is None or is_sha256(row["source_type_sha256"]))
                or not isinstance(row["title"], str)
                or not row["title"].strip()
                or not isinstance(row["review_disposition"], str)
                or not row["review_disposition"].strip()
                or row["decision"] not in DECISIONS
                or row["reward_policy"] not in REWARD_POLICIES
                or not _sorted_unique_strings(row["gates"])
                or not set(row["gates"]) <= set(target_gates)
            ):
                raise ValueError(f"target {number} identity or fields are invalid")
            decision = row["decision"]
            policy = row["reward_policy"]
            implied_by = row["implied_by"]
            group = row["reward_group"]
            consistent = {
                "independent": implied_by is None and group is None,
                "excluded-implied-by": (
                    decision == "excluded"
                    and group is None
                    and type(implied_by) is int
                    and 1 <= implied_by <= RESEARCH_TARGET_COUNT
                    and implied_by != number
                ),
                "correlated-group": (
                    decision == "hold"
                    and implied_by is None
                    and isinstance(group, str)
                    and number in group_members.get(group, ())
                ),
            }[policy]
            if (
                not consistent
                or (decision == "excluded") != (policy == "excluded-implied-by")
                # Acceptance means no target-specific gate remains; global gates still apply.
                or (
                    decision == "source-review-accepted"
                    and (row["gates"] or policy != "independent")
                )
                or (decision == "hold" and not row["gates"] and policy != "correlated-group")
            ):
                raise ValueError(f"target {number} decision and reward policy disagree")
            targets.append(
                ResearchTarget(
                    number=number,
                    theorem=theorem,
                    definition=row["definition"],
                    source_path=source_path,
                    reward_target_id=row["reward_target_id"],
                    source_type_sha256=row["source_type_sha256"],
                    decision=decision,
                    reward_policy=policy,
                    implied_by=implied_by,
                    reward_group=group,
                    gates=tuple(row["gates"]),
                )
            )
        by_number = {target.number: target for target in targets}
        for target in targets:
            if target.implied_by is not None and (
                by_number[target.implied_by].reward_policy == "excluded-implied-by"
            ):
                raise ValueError(f"target {target.number} is implied by an excluded target")
        for group_id, members in group_members.items():
            if any(
                by_number.get(member) is None or by_number[member].reward_group != group_id
                for member in members
            ):
                raise ValueError(f"reward group {group_id} does not match its members")
        hashes = [target.source_type_sha256 for target in targets if target.source_type_sha256]
        if len(hashes) != len(set(hashes)):
            raise ValueError("research target type hashes are duplicated")
        excluded = {
            target.number: target.implied_by
            for target in targets
            if target.reward_policy == "excluded-implied-by"
        }
        if excluded != EXCLUDED_AS_IMPLIED:
            raise ValueError("excluded targets do not match the reviewed implication")
        correlated_groups = {by_number[number].reward_group for number in CORRELATED_TARGETS}
        if (
            len(correlated_groups) != 1
            or None in correlated_groups
            or group_members[next(iter(correlated_groups))] != CORRELATED_TARGETS
        ):
            raise ValueError("targets 7-9 must share one held correlated reward group")
    except (
        OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, KeyError, TypeError
    ) as exc:
        raise VerifierError(
            ReasonCode.INVALID_ARGUMENT,
            f"cannot load research target decisions: {exc}",
        ) from exc
    return ResearchTargetDecisions(
        targets=tuple(targets),
        open_global_gates=tuple(gate for gate, status in global_gates.items() if status == "open"),
        sha256=sha256_bytes(content),
    )
