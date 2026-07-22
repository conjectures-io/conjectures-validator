from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from verifier.adapters import GenerationContext, GeneratedLean, adapter_for
from verifier.classification import is_adapter_required
from verifier.errors import ReasonCode, VerifierError
from verifier.hashing import hash_named_files, pretty_json, sha256_text
from verifier.models import Catalog, CatalogDeclaration, Classification, TaskManifest
from verifier.task_policy import production_eligibility, proved_type_collisions


PERMITTED_AXIOMS = ("propext", "Quot.sound", "Classical.choice")
DEFAULT_TIMEOUT_SECONDS = 3600
MAX_TIMEOUT_SECONDS = 3600
DEFAULT_MAX_SUBMISSION_BYTES = 1_000_000
MAX_SUBMISSION_BYTES = 1_000_000
TRUSTED_NAMES = (
    "Challenge.lean",
    "SolutionHeader.lean.txt",
    "SolutionFooter.lean.txt",
    "comparator-config.json",
    "source-metadata.json",
)
LEAN_MODULE_NAME = re.compile(
    r"(?:[A-Za-z_][A-Za-z0-9_']*|[0-9]+|«[A-Za-z0-9_'.]+»)"
    r"(?:\.(?:[A-Za-z_][A-Za-z0-9_']*|[0-9]+|«[A-Za-z0-9_'.]+»))*"
)


def task_slug(theorem: str) -> str:
    segments = theorem.split(".")[-2:]
    normalized = "-".join(re.sub(r"[^a-z0-9]+", "-", segment.lower()).strip("-") for segment in segments)
    shortened = normalized[:80].rstrip("-")
    return shortened or sha256_text(theorem)[7:19]


def task_id(repository_commit: str, theorem: str, mode: str, adapter_version: int) -> str:
    seed = f"{repository_commit}\0{theorem}\0{mode}\0{adapter_version}"
    digest = sha256_text(seed)[7:17]
    return f"fc-{repository_commit[:8]}-{task_slug(theorem)}-{digest}-{mode}-v{adapter_version}"


def _imports(declaration: CatalogDeclaration) -> str:
    if LEAN_MODULE_NAME.fullmatch(declaration.module) is None:
        raise VerifierError(ReasonCode.INVALID_MANIFEST, "source module name is unsafe")
    return f"import {declaration.module}\nimport TaskSupport\n\nnamespace Bounty\n"


def _challenge_text(declaration: CatalogDeclaration, generated: GeneratedLean) -> str:
    definitions = "\n\n".join(generated.definition_declarations)
    separator = "\n\n" if definitions else ""
    return (
        f"{_imports(declaration)}\n"
        f"{definitions}{separator}"
        f"theorem target : {generated.target_type} := by\n"
        "  sorry\n\n"
        "end Bounty\n"
    )


def _comparator_config(generated: GeneratedLean, enable_nanoda: bool) -> dict[str, Any]:
    definitions = tuple(
        f"Bounty.{line.split()[1]}" for line in generated.definition_declarations if line.startswith("def ")
    )
    return {
        "challenge_module": "Challenge",
        "solution_module": "Solution",
        "theorem_names": ["Bounty.target"],
        "definition_names": list(definitions),
        "permitted_axioms": list(PERMITTED_AXIOMS),
        "enable_nanoda": enable_nanoda,
    }


def trusted_task_payloads(
    declaration: CatalogDeclaration,
    mode: str,
    enable_nanoda: bool,
    repository_commit: str,
    adapter_version: int,
) -> Mapping[str, bytes]:
    adapter = adapter_for(declaration)
    if adapter is None or adapter.version != adapter_version:
        raise VerifierError(
            ReasonCode.INVALID_MANIFEST,
            "task adapter is unavailable or has a different version",
        )
    generated = adapter.generate(
        declaration,
        mode,
        GenerationContext(repository_commit, adapter_version),
    )
    source_metadata = {
        **declaration.to_dict(),
        "repository_commit": repository_commit,
        "adapter_version": adapter_version,
    }
    texts = {
        "Challenge.lean": _challenge_text(declaration, generated),
        "SolutionHeader.lean.txt": _imports(declaration),
        "SolutionFooter.lean.txt": "\nend Bounty\n",
        "comparator-config.json": pretty_json(_comparator_config(generated, enable_nanoda)),
        "source-metadata.json": pretty_json(source_metadata),
    }
    return {name: content.encode("utf-8") for name, content in texts.items()}


def generate_task(
    *,
    catalog: Catalog,
    declaration: CatalogDeclaration,
    mode: str,
    output: Path,
    enable_nanoda: bool = False,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    max_submission_bytes: int = DEFAULT_MAX_SUBMISSION_BYTES,
    allow_non_open: bool = False,
    validate_target: Callable[[Path, CatalogDeclaration, GeneratedLean, str], str],
) -> TaskManifest:
    if declaration.classification == Classification.POINTER_DECLARATION:
        original = next(
            (item for item in catalog.declarations if item.theorem == declaration.pointer_target),
            None,
        )
        if original is None:
            raise VerifierError(
                ReasonCode.THEOREM_NOT_FOUND,
                f"pointer target is not cataloged: {declaration.pointer_target}",
            )
        return generate_task(
            catalog=catalog,
            declaration=original,
            mode=mode,
            output=output,
            enable_nanoda=enable_nanoda,
            timeout_seconds=timeout_seconds,
            max_submission_bytes=max_submission_bytes,
            allow_non_open=allow_non_open,
            validate_target=validate_target,
        )
    adapter = adapter_for(declaration)
    if adapter is None:
        reason = ReasonCode.ADAPTER_REQUIRED if is_adapter_required(declaration.classification) else ReasonCode.UNSUPPORTED_DECLARATION
        raise VerifierError(reason, f"no automatic adapter for {declaration.classification.value}: {declaration.theorem}")
    if mode not in adapter.supported_modes(declaration):
        raise VerifierError(
            ReasonCode.INVALID_TASK_MODE,
            f"mode {mode!r} is invalid for {declaration.classification.value}; expected {adapter.modes}",
        )
    eligible, policy_violations, collisions = production_eligibility(catalog, declaration)
    if not eligible and not allow_non_open:
        raise VerifierError(
            ReasonCode.INELIGIBLE_TASK,
            f"declaration is not production eligible: {'; '.join(policy_violations)}",
        )
    if not 0 < timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise VerifierError(
            ReasonCode.INVALID_ARGUMENT,
            f"timeout must be between 1 and {MAX_TIMEOUT_SECONDS} seconds",
        )
    if not 0 < max_submission_bytes <= MAX_SUBMISSION_BYTES:
        raise VerifierError(
            ReasonCode.INVALID_ARGUMENT,
            f"submission limit must be between 1 and {MAX_SUBMISSION_BYTES} bytes",
        )
    if output.exists():
        raise VerifierError(ReasonCode.INVALID_ARGUMENT, f"task output already exists: {output}")
    context = GenerationContext(catalog.repository_commit, adapter.version)
    generated = adapter.generate(declaration, mode, context)
    identifier = task_id(catalog.repository_commit, declaration.theorem, mode, adapter.version)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{identifier}.", dir=output.parent))
    try:
        payloads = trusted_task_payloads(
            declaration,
            mode,
            enable_nanoda,
            catalog.repository_commit,
            adapter.version,
        )
        for name, content in payloads.items():
            (temporary / name).write_bytes(content)
        generated_hash = validate_target(temporary, declaration, generated, mode)
        target_collisions = proved_type_collisions(
            catalog,
            generated_hash,
            declaration.theorem,
        )
        collisions = tuple(sorted(frozenset(collisions) | frozenset(target_collisions)))
        if target_collisions and not allow_non_open:
            raise VerifierError(
                ReasonCode.INELIGIBLE_TASK,
                "generated target matches proved declarations: "
                + ", ".join(target_collisions),
            )
        eligible = eligible and not target_collisions
        trusted_hashes = hash_named_files(temporary, TRUSTED_NAMES)
        definition_names = tuple(_comparator_config(generated, enable_nanoda)["definition_names"])
        manifest = TaskManifest(
            schema_version=1,
            task_id=identifier,
            repository_commit=catalog.repository_commit,
            source_theorem=declaration.theorem,
            source_module=declaration.module,
            source_path=declaration.source_path,
            source_type_hash=declaration.type_hash,
            generated_target_type_hash=generated_hash,
            classification=declaration.classification,
            task_mode=mode,
            challenge_module="Challenge",
            solution_module="Solution",
            target_theorem="Bounty.target",
            theorem_names=("Bounty.target",),
            definition_names=definition_names,
            forbidden_dependencies=(declaration.theorem,),
            permitted_axioms=PERMITTED_AXIOMS,
            enable_nanoda=enable_nanoda,
            timeout_seconds=timeout_seconds,
            max_submission_bytes=max_submission_bytes,
            adapter_version=adapter.version,
            trusted_file_hashes=trusted_hashes,
            production_eligible=eligible,
            known_proof_collisions=collisions,
            answer_policy=generated.answer_policy,
        )
        (temporary / "manifest.json").write_text(pretty_json(manifest.to_dict()), encoding="utf-8")
        (temporary / "trusted-hashes.json").write_text(pretty_json(trusted_hashes), encoding="utf-8")
        os.replace(temporary, output)
        return manifest
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def generate_all(
    *,
    catalog: Catalog,
    declarations: Iterable[CatalogDeclaration],
    modes: tuple[str, ...],
    output: Path,
    generate_one: Callable[..., TaskManifest] = generate_task,
    **options: Any,
) -> dict[str, Any]:
    items = tuple(declarations)
    result: dict[str, Any] = {
        "requested": len(items),
        "generated": 0,
        "skipped_adapter_required": 0,
        "skipped_unsupported": 0,
        "failed": 0,
        "tasks": [],
        "skipped": [],
    }
    output.mkdir(parents=True, exist_ok=True)
    for declaration in items:
        adapter = adapter_for(declaration)
        if adapter is None or declaration.classification == Classification.POINTER_DECLARATION:
            adapter_required = is_adapter_required(declaration.classification)
            key = "skipped_adapter_required" if adapter_required else "skipped_unsupported"
            result[key] += 1
            skipped = {
                "theorem": declaration.theorem,
                "classification": declaration.classification.value,
                "reason_code": (ReasonCode.ADAPTER_REQUIRED if adapter_required else ReasonCode.UNSUPPORTED_DECLARATION).value,
            }
            if declaration.classification == Classification.POINTER_DECLARATION:
                skipped["detail"] = f"duplicate pointer to {declaration.pointer_target}"
            result["skipped"].append(skipped)
            continue
        if declaration.classification in {
            Classification.BOOL_ANSWER,
            Classification.NAT_ANSWER,
            Classification.INT_ANSWER,
            Classification.FINITE_ANSWER,
        }:
            applicable = adapter.modes
        else:
            applicable = tuple(mode for mode in modes if mode in adapter.modes)
        if not applicable:
            result["failed"] += 1
            result["skipped"].append(
                {"theorem": declaration.theorem, "reason_code": ReasonCode.INVALID_TASK_MODE.value}
            )
            continue
        for mode in applicable:
            target = output / task_id(catalog.repository_commit, declaration.theorem, mode, adapter.version)
            try:
                manifest = generate_one(
                    catalog=catalog, declaration=declaration, mode=mode, output=target, **options
                )
                result["generated"] += 1
                result["tasks"].append({"task_id": manifest.task_id, "path": str(target)})
            except VerifierError as exc:
                result["failed"] += 1
                result["skipped"].append(
                    {"theorem": declaration.theorem, "mode": mode, "reason_code": exc.reason.value, "detail": str(exc)}
                )
            except Exception as exc:
                result["failed"] += 1
                result["skipped"].append(
                    {
                        "theorem": declaration.theorem,
                        "mode": mode,
                        "reason_code": ReasonCode.INTERNAL_ERROR.value,
                        "detail": str(exc),
                    }
                )
    return result
