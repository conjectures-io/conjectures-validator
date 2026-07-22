from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Mapping

from verifier.errors import ReasonCode, VerifierError


class Classification(StrEnum):
    DIRECT_PROP = "DIRECT_PROP"
    PROP_ANSWER_WRAPPER = "PROP_ANSWER_WRAPPER"
    BOOL_ANSWER = "BOOL_ANSWER"
    NAT_ANSWER = "NAT_ANSWER"
    INT_ANSWER = "INT_ANSWER"
    FINITE_ANSWER = "FINITE_ANSWER"
    GENERAL_VALUE_ANSWER = "GENERAL_VALUE_ANSWER"
    MULTIPLE_ANSWER_HOLES = "MULTIPLE_ANSWER_HOLES"
    DEFINITION_HOLE = "DEFINITION_HOLE"
    POINTER_DECLARATION = "POINTER_DECLARATION"
    UNSUPPORTED = "UNSUPPORTED"


CLASSIFICATION_ORDER = tuple(item.value for item in Classification)
CATEGORY_ORDER = ("research open", "research solved", "textbook", "test", "API")
AUTOMATIC_CLASSIFICATIONS = frozenset(
    {
        Classification.DIRECT_PROP,
        Classification.PROP_ANSWER_WRAPPER,
        Classification.BOOL_ANSWER,
        Classification.NAT_ANSWER,
        Classification.INT_ANSWER,
        Classification.FINITE_ANSWER,
    }
)
ADAPTER_REQUIRED_CLASSIFICATIONS = frozenset(
    {
        Classification.GENERAL_VALUE_ANSWER,
        Classification.MULTIPLE_ANSWER_HOLES,
        Classification.DEFINITION_HOLE,
    }
)


@dataclass(frozen=True)
class CatalogDeclaration:
    theorem: str
    module: str
    source_path: str
    category: str
    ams_subjects: tuple[int, ...]
    formal_proof_kind: str | None
    formal_proof_link: str | None
    declaration_kind: str
    type_pretty: str
    type_hash: str
    contains_answer_annotation: bool
    answer_occurrences: tuple[Mapping[str, Any], ...]
    contains_sorry_in_type: bool
    has_parameters: bool
    is_prop: bool
    docstring: str | None
    supported_modes: tuple[str, ...]
    classification: Classification
    pointer_target: str | None = None
    finite_constructors: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CatalogDeclaration":
        try:
            return cls(
                theorem=str(value["theorem"]),
                module=str(value["module"]),
                source_path=str(value["source_path"]),
                category=str(value["category"]),
                ams_subjects=tuple(int(x) for x in value.get("ams_subjects", ())),
                formal_proof_kind=value.get("formal_proof_kind"),
                formal_proof_link=value.get("formal_proof_link"),
                declaration_kind=str(value.get("declaration_kind", "theorem")),
                type_pretty=str(value["type_pretty"]),
                type_hash=str(value["type_hash"]),
                contains_answer_annotation=bool(value.get("contains_answer_annotation", False)),
                answer_occurrences=tuple(value.get("answer_occurrences", ())),
                contains_sorry_in_type=bool(value.get("contains_sorry_in_type", False)),
                has_parameters=bool(value.get("has_parameters", False)),
                is_prop=bool(value.get("is_prop", False)),
                docstring=value.get("docstring"),
                supported_modes=tuple(str(x) for x in value.get("supported_modes", ())),
                classification=Classification(str(value["classification"])),
                pointer_target=value.get("pointer_target"),
                finite_constructors=tuple(str(x) for x in value.get("finite_constructors", ())),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise VerifierError(ReasonCode.INVALID_ARGUMENT, f"invalid catalog declaration: {exc}") from exc

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["classification"] = self.classification.value
        result["ams_subjects"] = list(self.ams_subjects)
        result["answer_occurrences"] = [dict(x) for x in self.answer_occurrences]
        result["supported_modes"] = list(self.supported_modes)
        result["finite_constructors"] = list(self.finite_constructors)
        return result


@dataclass(frozen=True)
class Catalog:
    schema_version: int
    repository_commit: str
    lean_toolchain: str
    mathlib_commit: str
    generated_by: str
    extraction_duration_ms: int
    declarations: tuple[CatalogDeclaration, ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Catalog":
        try:
            return cls(
                schema_version=int(value["schema_version"]),
                repository_commit=str(value["repository_commit"]),
                lean_toolchain=str(value["lean_toolchain"]),
                mathlib_commit=str(value["mathlib_commit"]),
                generated_by=str(value.get("generated_by", "CatalogExtractor.lean")),
                extraction_duration_ms=int(value.get("extraction_duration_ms", 0)),
                declarations=tuple(CatalogDeclaration.from_dict(x) for x in value["declarations"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise VerifierError(ReasonCode.INVALID_ARGUMENT, f"invalid catalog: {exc}") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "repository_commit": self.repository_commit,
            "lean_toolchain": self.lean_toolchain,
            "mathlib_commit": self.mathlib_commit,
            "generated_by": self.generated_by,
            "extraction_duration_ms": self.extraction_duration_ms,
            "declarations": [item.to_dict() for item in self.declarations],
        }


@dataclass(frozen=True)
class TaskManifest:
    schema_version: int
    task_id: str
    repository_commit: str
    source_theorem: str
    source_module: str
    source_path: str
    source_type_hash: str
    generated_target_type_hash: str
    classification: Classification
    task_mode: str
    challenge_module: str
    solution_module: str
    target_theorem: str
    theorem_names: tuple[str, ...]
    definition_names: tuple[str, ...]
    forbidden_dependencies: tuple[str, ...]
    permitted_axioms: tuple[str, ...]
    enable_nanoda: bool
    timeout_seconds: int
    max_submission_bytes: int
    adapter_version: int
    trusted_file_hashes: Mapping[str, str]
    answer_policy: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TaskManifest":
        try:
            return cls(
                schema_version=int(value["schema_version"]),
                task_id=str(value["task_id"]),
                repository_commit=str(value["repository_commit"]),
                source_theorem=str(value["source_theorem"]),
                source_module=str(value["source_module"]),
                source_path=str(value["source_path"]),
                source_type_hash=str(value["source_type_hash"]),
                generated_target_type_hash=str(value["generated_target_type_hash"]),
                classification=Classification(str(value["classification"])),
                task_mode=str(value["task_mode"]),
                challenge_module=str(value.get("challenge_module", "Challenge")),
                solution_module=str(value.get("solution_module", "Solution")),
                target_theorem=str(value.get("target_theorem", "Bounty.target")),
                theorem_names=tuple(str(x) for x in value["theorem_names"]),
                definition_names=tuple(str(x) for x in value.get("definition_names", ())),
                forbidden_dependencies=tuple(str(x) for x in value.get("forbidden_dependencies", ())),
                permitted_axioms=tuple(str(x) for x in value["permitted_axioms"]),
                enable_nanoda=bool(value.get("enable_nanoda", False)),
                timeout_seconds=int(value.get("timeout_seconds", 3600)),
                max_submission_bytes=int(value.get("max_submission_bytes", 5_000_000)),
                adapter_version=int(value.get("adapter_version", 1)),
                trusted_file_hashes=dict(value["trusted_file_hashes"]),
                answer_policy=dict(value.get("answer_policy", {})),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise VerifierError(ReasonCode.INVALID_MANIFEST, f"invalid task manifest: {exc}") from exc

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["classification"] = self.classification.value
        for key in (
            "theorem_names",
            "definition_names",
            "forbidden_dependencies",
            "permitted_axioms",
        ):
            result[key] = list(result[key])
        result["trusted_file_hashes"] = dict(sorted(self.trusted_file_hashes.items()))
        result["answer_policy"] = dict(self.answer_policy)
        return result


@dataclass(frozen=True)
class StaticCheckResult:
    valid: bool
    violations: tuple[str, ...]
    submitted_answer: str | None = None


@dataclass(frozen=True)
class ProcessResult:
    args: tuple[str, ...]
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool = False
    signal: int | None = None


DEFAULT_CHECKS: Mapping[str, bool] = {
    "manifest_valid": False,
    "source_type_hash_valid": False,
    "trusted_hashes_valid": False,
    "submission_policy_valid": False,
    "challenge_built": False,
    "solution_built": False,
    "same_statement": False,
    "axioms_permitted": False,
    "lean_kernel_passed": False,
    "nanoda_enabled": False,
    "nanoda_passed": False,
}


@dataclass(frozen=True)
class VerificationReport:
    schema_version: int
    task_id: str
    repository_commit: str
    source_theorem: str
    task_mode: str
    submission_sha256: str
    accepted: bool
    stage: str
    reason_code: ReasonCode
    checks: Mapping[str, bool]
    theorem_names: tuple[str, ...]
    permitted_axioms: tuple[str, ...]
    duration_ms: int
    comparator_exit_code: int | None
    stdout_tail: str
    stderr_tail: str
    workspace_retained: bool
    sandbox_mode: str

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["reason_code"] = self.reason_code.value
        result["checks"] = dict(self.checks)
        result["theorem_names"] = list(self.theorem_names)
        result["permitted_axioms"] = list(self.permitted_axioms)
        return result
