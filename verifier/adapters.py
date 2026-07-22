from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from verifier.models import CatalogDeclaration, Classification


@dataclass(frozen=True)
class GenerationContext:
    repository_commit: str
    adapter_version: int = 1


@dataclass(frozen=True)
class GeneratedLean:
    target_type: str
    definition_declarations: tuple[str, ...]
    answer_policy: Mapping[str, Any]


class TaskAdapter(Protocol):
    classification: Classification
    version: int

    def supports(self, declaration: CatalogDeclaration) -> bool: ...

    def supported_modes(self, declaration: CatalogDeclaration) -> tuple[str, ...]: ...

    def generate(
        self, declaration: CatalogDeclaration, mode: str, context: GenerationContext
    ) -> GeneratedLean: ...


@dataclass(frozen=True)
class AdapterSpec:
    classification: Classification
    modes: tuple[str, ...]
    generator: Callable[[CatalogDeclaration, str, GenerationContext], GeneratedLean]
    version: int = 1

    def supports(self, declaration: CatalogDeclaration) -> bool:
        return declaration.classification == self.classification

    def supported_modes(self, declaration: CatalogDeclaration) -> tuple[str, ...]:
        return self.modes if self.supports(declaration) else ()

    def generate(self, declaration: CatalogDeclaration, mode: str, context: GenerationContext) -> GeneratedLean:
        return self.generator(declaration, mode, context)


def _lean_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _direct(declaration: CatalogDeclaration, mode: str, _context: GenerationContext) -> GeneratedLean:
    source = _lean_string(declaration.theorem)
    target = f"fcTypeOfName% {source}" if mode == "positive" else f"¬ (fcTypeOfName% {source})"
    return GeneratedLean(target, (), {})


def _prop_answer(declaration: CatalogDeclaration, mode: str, _context: GenerationContext) -> GeneratedLean:
    source = _lean_string(declaration.theorem)
    base = f"fcPropAnswerTargetName% {source}"
    target = base if mode == "positive" else f"¬ ({base})"
    return GeneratedLean(target, (), {})


def _value_answer(
    type_name: str, syntax: str
) -> Callable[[CatalogDeclaration, str, GenerationContext], GeneratedLean]:
    def generate(declaration: CatalogDeclaration, _mode: str, _context: GenerationContext) -> GeneratedLean:
        answer = "submittedAnswer"
        source = _lean_string(declaration.theorem)
        return GeneratedLean(
            f"fcValueAnswerTargetName% {source} using {answer}",
            (f"def {answer} : {type_name} := by\n  sorry",),
            {"definition_name": f"Bounty.{answer}", "syntax": syntax},
        )

    return generate


def _finite(declaration: CatalogDeclaration, _mode: str, _context: GenerationContext) -> GeneratedLean:
    source = _lean_string(declaration.theorem)
    return GeneratedLean(
        f"fcValueAnswerTargetName% {source} using submittedAnswer",
        (f"def submittedAnswer : fcAnswerType% {source} := by\n  sorry",),
        {
            "definition_name": "Bounty.submittedAnswer",
            "syntax": "finite_constructor",
            "allowed_constructors": list(declaration.finite_constructors),
        },
    )


ADAPTERS: Mapping[Classification, AdapterSpec] = {
    Classification.DIRECT_PROP: AdapterSpec(Classification.DIRECT_PROP, ("positive", "negative"), _direct),
    Classification.PROP_ANSWER_WRAPPER: AdapterSpec(
        Classification.PROP_ANSWER_WRAPPER, ("positive", "negative"), _prop_answer
    ),
    Classification.BOOL_ANSWER: AdapterSpec(Classification.BOOL_ANSWER, ("answer",), _value_answer("Bool", "bool_literal")),
    Classification.NAT_ANSWER: AdapterSpec(Classification.NAT_ANSWER, ("answer",), _value_answer("ℕ", "nat_literal")),
    Classification.INT_ANSWER: AdapterSpec(Classification.INT_ANSWER, ("answer",), _value_answer("ℤ", "int_literal")),
    Classification.FINITE_ANSWER: AdapterSpec(Classification.FINITE_ANSWER, ("answer",), _finite),
}


def adapter_for(declaration: CatalogDeclaration) -> AdapterSpec | None:
    return ADAPTERS.get(declaration.classification)
