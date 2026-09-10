from __future__ import annotations

from verifier.models import TaskTrack

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

from verifier.models import Catalog, CatalogDeclaration, Classification


EXACT_TASK_MODE = "formalized"
COUNTEREXAMPLE_TASK_MODE = "counterexample"
PRODUCTION_TASK_MODES = (EXACT_TASK_MODE, COUNTEREXAMPLE_TASK_MODE)


# Missing track metadata means the released open policy.
OPEN_CONJECTURE: TaskTrack = "open_conjecture"
FORMALIZATION: TaskTrack = "formalization"
TASK_POLICY_VERSION = 1
FORMALIZATION_REVIEW_POLICY = "formalization-v1"


@dataclass(frozen=True)
class TrackPolicy:
    source_category: str
    modes: tuple[str, ...]
    requires_resolution_reference: bool
    published_proof_allowed: bool

    @property
    def target_relations(self) -> dict[str, str]:
        return {
            mode: "definitionally-equal" if mode == EXACT_TASK_MODE else "logical-negation"
            for mode in self.modes
        }


TRACK_POLICIES = MappingProxyType(
    {
        OPEN_CONJECTURE: TrackPolicy("research open", PRODUCTION_TASK_MODES, False, False),
        FORMALIZATION: TrackPolicy("research solved", (EXACT_TASK_MODE,), True, True),
    }
)


def track_policy(track: str, policy_version: int = TASK_POLICY_VERSION) -> TrackPolicy:
    if type(policy_version) is not int or policy_version != TASK_POLICY_VERSION:
        raise ValueError("unsupported task policy version")
    if not isinstance(track, str) or track not in TRACK_POLICIES:
        raise ValueError("unsupported task track")
    return TRACK_POLICIES[track]


def valid_resolution_reference(value: object) -> bool:
    # The audit establishes mathematical correspondence; this validates its contract.
    if not isinstance(value, dict) or set(value) != {"url", "location"}:
        return False
    if not all(isinstance(item, str) and item.strip() for item in value.values()):
        return False
    try:
        url = urlsplit(value["url"])
        return url.scheme in {"http", "https"} and bool(url.hostname)
    except ValueError:
        return False


def review_policy_for_track(track: str, open_review_policy: str) -> str:
    policy = track_policy(track)
    return FORMALIZATION_REVIEW_POLICY if policy.published_proof_allowed else open_review_policy


def compiled_source_policy_valid(inspection: Any, source: CatalogDeclaration, mode: str) -> bool:
    # Bind inspection to admitted metadata; accepting either category permits drift.
    policies = [p for p in TRACK_POLICIES.values() if p.source_category == source.category]
    return bool(policies) and (
        mode in policies[0].modes
        and inspection["source_category"] == source.category
        and inspection["source_declaration_kind"] == "theorem"
        and inspection["source_depends_on_sorry"]
        and not inspection["source_has_formal_proof"]
        and not inspection["target_contains_sorry"]
        and inspection["source_axioms"] == tuple(sorted(source.transitive_axioms))
    )


def is_production_task_mode(mode: str) -> bool:
    return mode in PRODUCTION_TASK_MODES


def expected_answer_policy(declaration: CatalogDeclaration) -> dict[str, Any]:
    syntax = {
        Classification.BOOL_ANSWER: "bool_literal",
        Classification.NAT_ANSWER: "nat_literal",
        Classification.INT_ANSWER: "int_literal",
        Classification.FINITE_ANSWER: "finite_constructor",
    }.get(declaration.classification)
    if syntax is None:
        return {}
    policy: dict[str, Any] = {
        "definition_name": "Bounty.submittedAnswer",
        "syntax": syntax,
    }
    if declaration.classification == Classification.FINITE_ANSWER:
        policy["allowed_constructors"] = list(declaration.finite_constructors)
    return policy


def proved_type_collisions(
    catalog: Catalog,
    type_hash: str,
    excluded_theorem: str,
) -> tuple[str, ...]:
    return tuple(
        sorted(
            item.theorem
            for item in catalog.declarations
            if item.theorem != excluded_theorem
            and item.type_hash == type_hash
            and item.declaration_kind == "theorem"
            and item.is_prop
            and not item.depends_on_sorry
        )
    )


def known_proof_collisions(catalog: Catalog, declaration: CatalogDeclaration) -> tuple[str, ...]:
    return proved_type_collisions(catalog, declaration.type_hash, declaration.theorem)


def production_policy_violations(
    declaration: CatalogDeclaration,
    collisions: tuple[str, ...],
    mode: str = EXACT_TASK_MODE,
    *,
    track: TaskTrack = OPEN_CONJECTURE,
    policy_version: int = TASK_POLICY_VERSION,
    resolution_reference: dict[str, str] | None = None,
) -> tuple[str, ...]:
    try:
        policy = track_policy(track, policy_version)
    except ValueError as exc:
        return (str(exc),)
    checks = (
        (
            mode not in policy.modes,
            f"{track} tasks must use modes: {', '.join(policy.modes)}",
        ),
        (
            declaration.classification != Classification.DIRECT_PROP,
            "source is not a direct proposition",
        ),
        (
            declaration.category != policy.source_category,
            f"source is not categorized as {policy.source_category}",
        ),
        (
            policy.requires_resolution_reference
            and not valid_resolution_reference(resolution_reference),
            "formalization requires a resolution reference with URL and exact theorem location",
        ),
        (
            not policy.requires_resolution_reference and bool(resolution_reference),
            "open conjectures cannot declare a known resolution reference",
        ),
        (declaration.declaration_kind != "theorem", "source is not a theorem"),
        (not declaration.is_prop, "source type is not a proposition"),
        (not declaration.depends_on_sorry, "source already has a proof without sorryAx"),
        (
            declaration.contains_sorry_in_type,
            "source proposition contains a sorryAx term",
        ),
        (
            declaration.contains_answer_annotation,
            "source uses answer metadata instead of an exact direct proposition",
        ),
        (
            declaration.formal_proof_kind is not None or declaration.formal_proof_link is not None,
            "source has formal-proof metadata",
        ),
        (bool(collisions), f"matching proved declarations exist: {', '.join(collisions)}"),
    )
    return tuple(message for failed, message in checks if failed)


def production_eligibility(
    catalog: Catalog,
    declaration: CatalogDeclaration,
    mode: str = EXACT_TASK_MODE,
    *,
    track: TaskTrack = OPEN_CONJECTURE,
    policy_version: int = TASK_POLICY_VERSION,
    resolution_reference: dict[str, str] | None = None,
) -> tuple[bool, tuple[str, ...], tuple[str, ...]]:
    collisions = known_proof_collisions(catalog, declaration)
    violations = production_policy_violations(
        declaration,
        collisions,
        mode,
        track=track,
        policy_version=policy_version,
        resolution_reference=resolution_reference,
    )
    return not violations, violations, collisions
