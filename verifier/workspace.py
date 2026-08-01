from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from verifier.errors import ReasonCode, VerifierError
from verifier.environment import tool_path, trusted_environment
from verifier.hashing import sha256_text
from verifier.models import CatalogDeclaration, ProcessResult
from verifier.process import run_process
from verifier.submission import Submission
from verifier.task_policy import (
    COUNTEREXAMPLE_TASK_MODE,
    is_production_task_mode,
)


@dataclass(frozen=True)
class WorkspacePaths:
    root: Path
    challenge: Path
    solution: Path
    config: Path
    retained: bool


def _lakefile(project_root: Path) -> str:
    root = json.dumps(str(project_root.resolve()), ensure_ascii=False)
    return (
        'name = "fc_verification_workspace"\n'
        'version = "0.1.0"\n\n'
        '[[require]]\n'
        'name = "formal_conjectures_verifier"\n'
        f"path = {root}\n\n"
        '[[lean_lib]]\nname = "Challenge"\n\n'
        '[lean_lib.leanOptions]\nweak.google.answer = "postpone"\n\n'
        '[[lean_lib]]\nname = "Solution"\n'
        '[lean_lib.leanOptions]\nweak.google.answer = "postpone"\n'
    )


PackageSource = tuple[str, Path, str, str, str]
ROOT_PACKAGE_NAME = "formal_conjectures_verifier"
MIRROR_EXCLUDED_NAMES = frozenset({".git", ".home", ".lake", ".work"})


def _required_string(package: object, key: str) -> str:
    if not isinstance(package, dict):
        raise TypeError("package entries must be objects")
    value = package.get(key)
    if not isinstance(value, str) or not value:
        raise TypeError(f"package {key} must be a non-empty string")
    return value


def _safe_filename(value: str, field: str) -> str:
    if Path(value).name != value or value in {".", ".."}:
        raise ValueError(f"package {field} must be a filename")
    return value


def _safe_package_name(value: str) -> str:
    if (
        value in {".", ".."}
        or not value[0].isalnum()
        or not all(character.isalnum() or character in "_-" for character in value)
    ):
        raise ValueError(f"unsafe Lake package name: {value!r}")
    return value


def _package_source(project_root: Path, package: object) -> PackageSource:
    kind = _required_string(package, "type")
    if kind not in {"git", "path"}:
        raise ValueError(f"unsupported Lake package type: {kind!r}")
    name = _safe_package_name(_required_string(package, "name"))
    if name == ROOT_PACKAGE_NAME:
        raise ValueError(f"dependency collides with root package: {name}")
    directory = _required_string(package, "dir") if kind == "path" else name
    if Path(directory).is_absolute():
        raise ValueError(f"package path must be relative: {directory!r}")
    source = (
        project_root / directory
        if kind == "path"
        else project_root / ".lake" / "packages" / directory
    ).resolve()
    trusted_root = project_root.resolve()
    if not source.is_relative_to(trusted_root):
        raise ValueError(f"package path escapes the trusted project: {directory!r}")
    scope = package.get("scope", "") if isinstance(package, dict) else ""
    if not isinstance(scope, str):
        raise TypeError("package scope must be a string")
    return (
        name,
        source,
        _safe_filename(_required_string(package, "configFile"), "configFile"),
        _safe_filename(_required_string(package, "manifestFile"), "manifestFile"),
        scope,
    )


def _path_package_entry(package: PackageSource, directory: Path, *, inherited: bool = True) -> dict[str, object]:
    name, _source, config_file, manifest_file, scope = package
    return {
        "type": "path",
        "scope": scope,
        "name": name,
        "manifestFile": manifest_file,
        "inherited": inherited,
        "dir": str(directory.resolve()),
        "configFile": config_file,
    }


def _local_package_sources(project_root: Path) -> tuple[PackageSource, ...]:
    try:
        manifest = json.loads((project_root / "lake-manifest.json").read_text(encoding="utf-8"))
        packages = manifest["packages"]
        if not isinstance(packages, list):
            raise TypeError("packages must be an array")
        dependencies = tuple(_package_source(project_root, package) for package in packages)
        names = tuple(package[0] for package in dependencies)
        if len(names) != len(set(names)):
            raise ValueError("Lake package names must be unique")
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise VerifierError(ReasonCode.WORKSPACE_ERROR, f"cannot load local Lake package graph: {exc}") from exc
    sources: tuple[PackageSource, ...] = (
        (
            ROOT_PACKAGE_NAME,
            project_root.resolve(),
            "lakefile.toml",
            "lake-manifest.json",
            "",
        ),
        *dependencies,
    )
    missing = tuple(
        str(source)
        for _name, source, config, manifest_file, _scope in sources
        if not source.is_dir()
        or not (source / config).is_file()
        or not (source / manifest_file).is_file()
    )
    if missing:
        raise VerifierError(
            ReasonCode.WORKSPACE_ERROR,
            "local Lake package checkout is missing: " + ", ".join(missing),
        )
    return sources


def _workspace_manifest(root_entry: dict[str, object]) -> str:
    return json.dumps(
        {
            "version": "1.1.0",
            "packagesDir": ".lake/packages",
            "packages": [root_entry],
            "name": "fc_verification_workspace",
            "lakeDir": ".lake",
        },
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ) + "\n"


def _package_overrides(entries: tuple[dict[str, object], ...]) -> str:
    return json.dumps(
        {
            "schemaVersion": "1.1.0",
            "packages": list(entries),
        },
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    ) + "\n"


def _symlink_directory_contents(source: Path, destination: Path, *, excluded: frozenset[str]) -> None:
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    for child in sorted(source.iterdir(), key=lambda path: path.name):
        if child.name not in excluded:
            (destination / child.name).symlink_to(child.resolve(), target_is_directory=child.is_dir())


def _materialize_package_mirror(source: Path, destination: Path) -> None:
    _symlink_directory_contents(
        source,
        destination,
        excluded=MIRROR_EXCLUDED_NAMES,
    )
    trusted_lake = source / ".lake"
    mirror_lake = destination / ".lake"
    mirror_lake.mkdir(mode=0o700)
    if trusted_lake.is_dir():
        _symlink_directory_contents(
            trusted_lake,
            mirror_lake,
            excluded=frozenset({"config"}),
        )
        trusted_config = trusted_lake / "config"
        if trusted_config.is_dir():
            shutil.copytree(trusted_config, mirror_lake / "config", symlinks=True)


def _materialize_package_graph(
    sources: tuple[PackageSource, ...], packages_dir: Path
) -> tuple[dict[str, object], ...]:
    entries = []
    for index, package in enumerate(sources):
        name, source, _config, _manifest, _scope = package
        destination = packages_dir / name
        _materialize_package_mirror(source.resolve(), destination)
        entries.append(_path_package_entry(package, destination, inherited=index != 0))
    return tuple(entries)


def create_workspace(
    *,
    task_files: Mapping[str, bytes],
    project_root: Path,
    workspace_parent: Path | None = None,
    retain: bool = False,
) -> WorkspacePaths:
    parent = workspace_parent or project_root / ".work"
    parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="verify-", dir=parent))
    try:
        for name in ("Challenge.lean", "comparator-config.json"):
            content = task_files.get(name)
            if content is None:
                raise VerifierError(ReasonCode.WORKSPACE_ERROR, f"trusted task file is missing: {name}")
            destination = root / name
            destination.write_bytes(content)
            destination.chmod(0o600)
        lake_dir = root / ".lake"
        lake_dir.mkdir(mode=0o700)
        entries = _materialize_package_graph(
            _local_package_sources(project_root), lake_dir / "packages"
        )
        (root / "lakefile.toml").write_text(
            _lakefile(Path(str(entries[0]["dir"]))), encoding="utf-8"
        )
        (root / "lake-manifest.json").write_text(
            _workspace_manifest(entries[0]), encoding="utf-8"
        )
        (lake_dir / "package-overrides.json").write_text(
            _package_overrides(entries), encoding="utf-8"
        )
        (root / ".home" / ".tmp").mkdir(parents=True, mode=0o700)
        return WorkspacePaths(
            root=root,
            challenge=root / "Challenge.lean",
            solution=root / "Solution.lean",
            config=root / "comparator-config.json",
            retained=retain,
        )
    except Exception:
        shutil.rmtree(root, ignore_errors=True)
        raise


def package_solution(
    paths: WorkspacePaths, task_files: Mapping[str, bytes], submission: Submission
) -> None:
    try:
        header = task_files["SolutionHeader.lean.txt"].decode("utf-8", errors="strict")
        footer = task_files["SolutionFooter.lean.txt"].decode("utf-8", errors="strict")
        paths.solution.write_text(header + submission.text + footer, encoding="utf-8")
        paths.solution.chmod(0o600)
    except (KeyError, OSError, UnicodeDecodeError) as exc:
        raise VerifierError(ReasonCode.WORKSPACE_ERROR, f"cannot package solution: {exc}") from exc


def cleanup_workspace(paths: WorkspacePaths) -> None:
    if not paths.retained:
        shutil.rmtree(paths.root, ignore_errors=True)


def build_challenge(
    paths: WorkspacePaths,
    lake: Path,
    env: Mapping[str, str],
    timeout_seconds: int,
) -> ProcessResult:
    return run_process(
        (str(lake), "build", "Challenge"),
        cwd=paths.root,
        timeout_seconds=timeout_seconds,
        env=env,
    )


def inspect_generated_target(
    *,
    paths: WorkspacePaths,
    lake: Path,
    project_root: Path,
    source_module: str,
    source_theorem: str,
    classification: str,
    mode: str,
    env: Mapping[str, str],
    timeout_seconds: int,
    target_theorem: str = "Bounty.target",
) -> dict[str, object]:
    inspector = project_root / ".lake" / "build" / "bin" / "task_inspector"
    args = (
        str(lake),
        "env",
        str(inspector),
        "Challenge",
        target_theorem,
        source_module,
        source_theorem,
        classification,
        mode,
    )
    result = run_process(args, cwd=paths.root, timeout_seconds=timeout_seconds, env=env)
    if result.timed_out:
        raise VerifierError(ReasonCode.TIMEOUT, "task inspection timed out")
    if result.exit_code != 0:
        raise VerifierError(ReasonCode.CHALLENGE_BUILD_FAILED, result.stderr[-4000:] or result.stdout[-4000:])
    try:
        candidate = next(
            line.strip()
            for line in reversed(result.stdout.splitlines())
            if line.lstrip().startswith("{")
        )
        payload = json.loads(candidate)
        axioms = payload["source_transitive_axioms"]
        if not isinstance(axioms, list) or not all(isinstance(item, str) for item in axioms):
            raise TypeError("source_transitive_axioms must be a string array")
        return {
            "source_hash": sha256_text(str(payload["source_type_canonical"])),
            "target_hash": sha256_text(str(payload["target_type_canonical"])),
            "matches": bool(payload["matches_intended_target"]),
            "target_contains_sorry": bool(payload["target_contains_sorry"]),
            "source_axioms": tuple(sorted(axioms)),
            "source_depends_on_sorry": bool(payload["source_depends_on_sorry"]),
            "source_category": payload.get("source_category"),
            "source_has_formal_proof": bool(payload["source_has_formal_proof"]),
            "source_declaration_kind": str(payload["source_declaration_kind"]),
        }
    except (StopIteration, ValueError, KeyError, TypeError) as exc:
        raise VerifierError(ReasonCode.CHALLENGE_BUILD_FAILED, f"invalid task inspector output: {exc}") from exc


def target_validator(
    project_root: Path,
) -> Callable[[Path, CatalogDeclaration, object, str], str]:
    lake = tool_path(project_root, "lake")

    def validate(
        task_dir: Path,
        declaration: CatalogDeclaration,
        _generated: object,
        mode: str,
        target_theorem: str = "Bounty.target",
    ) -> str:
        task_files = {
            name: (task_dir / name).read_bytes()
            for name in ("Challenge.lean", "comparator-config.json")
        }
        paths = create_workspace(task_files=task_files, project_root=project_root)
        try:
            effective_env = trusted_environment(project_root, paths.root / ".home")
            build = build_challenge(paths, lake, effective_env, 1800)
            if build.timed_out:
                raise VerifierError(ReasonCode.TIMEOUT, "generated challenge build timed out")
            if build.exit_code != 0:
                raise VerifierError(
                    ReasonCode.CHALLENGE_BUILD_FAILED, build.stderr[-4000:] or build.stdout[-4000:]
                )
            inspection = inspect_generated_target(
                paths=paths,
                lake=lake,
                project_root=project_root,
                source_module=declaration.module,
                source_theorem=declaration.theorem,
                classification=declaration.classification.value,
                mode=mode,
                env=effective_env,
                timeout_seconds=600,
                target_theorem=target_theorem,
            )
            if inspection["source_hash"] != declaration.type_hash:
                raise VerifierError(ReasonCode.SOURCE_TYPE_CHANGED, "source type differs during task generation")
            if not inspection["matches"]:
                raise VerifierError(ReasonCode.STATEMENT_MISMATCH, "generated challenge is not the intended target")
            if is_production_task_mode(mode) and (
                inspection["source_category"] != "research open"
                or inspection["source_declaration_kind"] != "theorem"
                or not inspection["source_depends_on_sorry"]
                or inspection["source_has_formal_proof"]
                or inspection["target_contains_sorry"]
                or inspection["source_axioms"] != tuple(sorted(declaration.transitive_axioms))
            ):
                raise VerifierError(
                    ReasonCode.INELIGIBLE_TASK,
                    "source production policy differs from the compiled Lean environment",
                )
            if (
                mode == COUNTEREXAMPLE_TASK_MODE
                and inspection["target_hash"] == inspection["source_hash"]
            ):
                raise VerifierError(
                    ReasonCode.STATEMENT_MISMATCH,
                    "counterexample target unexpectedly matches the source theorem type",
                )
            return str(inspection["target_hash"])
        finally:
            cleanup_workspace(paths)

    return validate
