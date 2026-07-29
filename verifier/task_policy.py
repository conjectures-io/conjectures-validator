from __future__ import annotations

from typing import Any

from verifier.models import Catalog, CatalogDeclaration, Classification


EXACT_TASK_MODE = "formalized"


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


def known_proof_collisions(
    catalog: Catalog, declaration: CatalogDeclaration
) -> tuple[str, ...]:
    return proved_type_collisions(catalog, declaration.type_hash, declaration.theorem)


def production_policy_violations(
    declaration: CatalogDeclaration,
    collisions: tuple[str, ...],
    mode: str = EXACT_TASK_MODE,
) -> tuple[str, ...]:
    checks = (
        (mode != EXACT_TASK_MODE, "production tasks must use the exact formalized mode"),
        (
            declaration.classification != Classification.DIRECT_PROP,
            "source is not a direct proposition",
        ),
        (declaration.category != "research open", "source is not categorized as research open"),
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
) -> tuple[bool, tuple[str, ...], tuple[str, ...]]:
    collisions = known_proof_collisions(catalog, declaration)
    violations = production_policy_violations(declaration, collisions, mode)
    return not violations, violations, collisions
