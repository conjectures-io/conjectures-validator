"""The decision matrix for package-authored research targets 1-30.

Schema version 1 records what review established about each staged target and which gates remain
open. It cannot activate anything, so a version-1 matrix is consistent only while
`ACTIVATED_RESEARCH_TARGETS` is empty.

Schema version 2 adds an explicit activation record: the activated target numbers, a digest
commitment to the reviewed `activation-policy.json` beside the matrix, and a release status. It is
consistent only when its activated theorems are exactly `ACTIVATED_RESEARCH_TARGETS` and every
technical gate is closed with cited evidence. Two kinds of global gate may stay pending: owner
approvals (`pending-owner-approval`, only while the release is a proposal), and release gates
(`pending-release-gate`) about artefacts built after this file, such as the verifier image built
from the validator that pins it. The release manifest enforces those; this file cannot cite them.

The matrix never admits a target on its own. Admission also needs the theorem in
`ACTIVATED_RESEARCH_TARGETS`, a reviewed validator constant, and a tier policy that names the
family; the selection audit commits to this file's digest.
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

RESEARCH_TARGET_DECISIONS_SCHEMA_VERSION = 2
RESEARCH_TARGET_DECISIONS_SCHEMA_VERSIONS = frozenset({1, 2})
RESEARCH_TARGET_COUNT = 30
ACTIVATION_POLICY_FILE_NAME = "activation-policy.json"
# `source-review-accepted` records only that review left no open target-specific gate. It is not a
# proof, a novelty certificate, production readiness, or permission to activate a reward.
DECISIONS = frozenset({"excluded", "hold", "source-review-accepted"})
# Version 2 only. `activated` is admission of the theorem, never a claim about its truth.
ACTIVATED_DECISION = "activated"
V2_DECISIONS = DECISIONS | {ACTIVATED_DECISION}
REWARD_POLICIES = frozenset({"correlated-group", "excluded-implied-by", "independent"})
GATE_STATUSES = frozenset({"closed", "open"})
# Version 2 only, and only on an approval gate of a release that is still a proposal.
PENDING_OWNER_APPROVAL = "pending-owner-approval"
# Version 2 only, and only on a release gate, which the release manifest closes.
PENDING_RELEASE_GATE = "pending-release-gate"
GATE_KINDS = frozenset({"approval", "release", "technical"})
PENDING_STATUS_FOR_KIND = {"approval": PENDING_OWNER_APPROVAL, "release": PENDING_RELEASE_GATE}
RELEASE_PROPOSED = "proposed-pending-owner-approval"
RELEASE_APPROVED = "owner-approved"
RELEASE_STATUSES = frozenset({RELEASE_PROPOSED, RELEASE_APPROVED})
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
    schema_version: int = 1
    release_status: str | None = None
    pending_approval_gates: tuple[str, ...] = ()
    policy_sha256: str | None = None
    pending_release_gates: tuple[str, ...] = ()

    @property
    def admissible(self) -> frozenset[str]:
        """The theorems a version-2 matrix activates; always empty for version 1."""
        return frozenset(
            target.theorem for target in self.targets if target.decision == ACTIVATED_DECISION
        )


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
        raise TypeError("decision matrix must be a JSON object")
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
    """Version 1: gate id -> `open` or `closed`."""
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


def _gate_table_v2(
    value: object, label: str, *, global_gates: bool
) -> dict[str, tuple[str, str]]:
    """Version 2: gate id -> (status, kind).

    A closed gate cites evidence and nothing else does. Each pending status belongs to exactly one
    gate kind; a technical gate is either closed with evidence or open. Approval and release gates
    are global only: a target-specific gate is always technical.
    """
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be a non-empty list")
    gates: dict[str, tuple[str, str]] = {}
    for gate in value:
        if (
            not isinstance(gate, dict)
            or set(gate) != {"description", "evidence", "id", "kind", "status"}
            or not isinstance(gate["id"], str)
            or GATE_ID.fullmatch(gate["id"]) is None
            or gate["id"] in gates
            or not isinstance(gate["description"], str)
            or not gate["description"].strip()
            or gate["kind"] not in GATE_KINDS
            or (gate["kind"] != "technical" and not global_gates)
            or gate["status"] not in GATE_STATUSES | set(PENDING_STATUS_FOR_KIND.values())
            or (
                gate["status"] in PENDING_STATUS_FOR_KIND.values()
                and gate["status"] != PENDING_STATUS_FOR_KIND.get(gate["kind"])
            )
            or not (gate["evidence"] is None or isinstance(gate["evidence"], str))
            or (gate["status"] == "closed") != bool(gate["evidence"] and gate["evidence"].strip())
        ):
            raise ValueError(f"{label} contains an invalid gate")
        gates[gate["id"]] = (gate["status"], gate["kind"])
    if list(gates) != sorted(gates):
        raise ValueError(f"{label} must be sorted by id")
    return gates


def _activation_record(value: object, matrix_path: Path) -> tuple[frozenset[int], str, str]:
    """Validate a version-2 activation record; return (numbers, release status, policy digest)."""
    if not isinstance(value, dict) or set(value) != {
        "activated",
        "approval_record_sha256",
        "policy_sha256",
        "release_status",
    }:
        raise ValueError("activation record field set is not exact")
    numbers = value["activated"]
    if (
        not isinstance(numbers, list)
        or not numbers
        or not all(type(item) is int and 1 <= item <= RESEARCH_TARGET_COUNT for item in numbers)
        or numbers != sorted(set(numbers))
    ):
        raise ValueError("activated targets must be a sorted, unique, non-empty list of numbers")
    status = value["release_status"]
    approval = value["approval_record_sha256"]
    if status not in RELEASE_STATUSES or (
        (status == RELEASE_PROPOSED) != (approval is None)
    ) or not (approval is None or is_sha256(approval)):
        raise ValueError("activation release status and approval record disagree")
    policy = value["policy_sha256"]
    if not is_sha256(policy):
        raise ValueError("activation policy digest is invalid")
    # The reviewed policy travels with the matrix, and the matrix commits to its exact bytes.
    actual = sha256_bytes(matrix_path.with_name(ACTIVATION_POLICY_FILE_NAME).read_bytes())
    if actual != policy:
        raise ValueError("activation policy digest does not match the policy beside the matrix")
    return frozenset(numbers), status, policy


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
        version = value["schema_version"]
        if (
            type(version) is not int
            or version not in RESEARCH_TARGET_DECISIONS_SCHEMA_VERSIONS
            or value["default"] != "DENY"
            or (version == 1 and value["activation"] != "none")
            or value["source_family"] != RESEARCH_TARGETS_SOURCE_FAMILY
            or value["reward_target_policy"] != REWARD_TARGET_POLICY
            or not is_sha256(value["review_archive_sha256"])
        ):
            raise ValueError("decision matrix metadata is invalid")
        # Read at call time: the admission gate in `task_pool` and this check share one constant.
        activated_names = task_pool.ACTIVATED_RESEARCH_TARGETS
        activated_numbers: frozenset[int] = frozenset()
        release_status = None
        policy_sha256 = None
        if version == 1:
            if activated_names:
                raise ValueError("schema version 1 cannot back an activated research target")
            global_v1 = _gate_table(value["global_gates"], "global gates")
            if all(status == "closed" for status in global_v1.values()):
                # Closing every global gate is an activation decision, which version 1 cannot
                # express.
                raise ValueError("a version-1 decision matrix must keep a global gate open")
            global_gates = {gate: (status, "technical") for gate, status in global_v1.items()}
            target_gates = {
                gate: (status, "technical")
                for gate, status in _gate_table(value["target_gates"], "target gates").items()
            }
        else:
            activated_numbers, release_status, policy_sha256 = _activation_record(
                value["activation"], path
            )
            global_gates = _gate_table_v2(value["global_gates"], "global gates", global_gates=True)
            target_gates = _gate_table_v2(value["target_gates"], "target gates", global_gates=False)
            if any(
                kind == "technical" and status != "closed"
                for status, kind in global_gates.values()
            ):
                raise ValueError("every technical global gate must be closed before activation")
            if any(status == "open" for status, _kind in global_gates.values()):
                raise ValueError("an approval or release gate must be closed or pending")
            if release_status == RELEASE_APPROVED and any(
                kind == "approval" and status != "closed"
                for status, kind in global_gates.values()
            ):
                raise ValueError("an owner-approved release cannot leave an approval pending")
        if set(global_gates) & set(target_gates):
            raise ValueError("global and target gate ids overlap")

        groups = value["reward_groups"]
        if not isinstance(groups, list):
            raise TypeError("reward groups must be a list")
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

        allowed_decisions = DECISIONS if version == 1 else V2_DECISIONS
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
                or row["decision"] not in allowed_decisions
                or row["reward_policy"] not in REWARD_POLICIES
                or not _sorted_unique_strings(row["gates"])
                or not set(row["gates"]) <= set(target_gates)
            ):
                raise ValueError(f"target {number} identity or fields are invalid")
            decision = row["decision"]
            policy = row["reward_policy"]
            implied_by = row["implied_by"]
            group = row["reward_group"]
            open_gates = [gate for gate in row["gates"] if target_gates[gate][0] != "closed"]
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
            if version == 1:
                # Acceptance means no target-specific gate remains; global gates still apply.
                accepted_ok = not row["gates"]
                hold_ok = bool(row["gates"]) or policy == "correlated-group"
            else:
                # Version 2 keeps closed gates on the row, so the record of what closed them stays.
                accepted_ok = not open_gates
                hold_ok = bool(open_gates) or policy == "correlated-group"
            if (
                not consistent
                or (decision == "excluded") != (policy == "excluded-implied-by")
                or (
                    decision == "source-review-accepted"
                    and (not accepted_ok or policy != "independent")
                )
                or (decision == "hold" and not hold_ok)
                or (
                    decision == ACTIVATED_DECISION
                    and (
                        policy != "independent"
                        or open_gates
                        or row["source_type_sha256"] is None
                        or number not in activated_numbers
                    )
                )
                or (number in activated_numbers and decision != ACTIVATED_DECISION)
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
        # The reviewed validator constant and the matrix must name exactly the same theorems.
        activated_in_matrix = frozenset(
            target.theorem for target in targets if target.decision == ACTIVATED_DECISION
        )
        if version == 2 and activated_in_matrix != activated_names:
            raise ValueError(
                "activated research targets differ from ACTIVATED_RESEARCH_TARGETS"
            )
    except (
        OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, KeyError, TypeError
    ) as exc:
        raise VerifierError(
            ReasonCode.INVALID_ARGUMENT,
            f"cannot load research target decisions: {exc}",
        ) from exc
    return ResearchTargetDecisions(
        targets=tuple(targets),
        open_global_gates=tuple(
            gate for gate, (status, _kind) in global_gates.items() if status == "open"
        ),
        sha256=sha256_bytes(content),
        schema_version=version,
        release_status=release_status,
        pending_approval_gates=tuple(
            gate
            for gate, (status, _kind) in global_gates.items()
            if status == PENDING_OWNER_APPROVAL
        ),
        policy_sha256=policy_sha256,
        pending_release_gates=tuple(
            gate
            for gate, (status, _kind) in global_gates.items()
            if status == PENDING_RELEASE_GATE
        ),
    )
