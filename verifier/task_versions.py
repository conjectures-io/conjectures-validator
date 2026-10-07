"""Version-2 task identities: what an immutable task version is bound to, derived from trusted inputs.

A version-1 ("legacy") task ID embeds the Formal Conjectures commit, so every repin rotates every
task even when nothing it asks changed. A version-2 task ID instead commits to the following,
each derived here from the actual compiled environment and never accepted from a caller.

Immutable task provenance (bound into the task ID and the bundle bytes):

* the **environment identity**: Lean toolchain and commit, Mathlib and every Lake package pin,
  Comparator, lean4export, the validator's `TaskSupport`, the verification workspace template,
  the generator version and the verification policy. A change to any of these changes every task
  ID, and the new versions must be built and validated again; there is no identity-only upgrade.
* the **dependency identity** of the source statement, transitive over both definitions and
  imports:
  - the kernel content of every constant defined in a local module (the task source package or
    the validator's Lean) that the statement's type reaches through definition, opaque and
    recursor bodies (a theorem contributes its statement, never its proof). This is
    declaration-level, so editing one problem in a file, or one target's definition in a shared
    library module, rotates only targets whose statements reach the edited declaration, and a
    body change to a named definition is a change even when the printed statement is
    byte-identical;
  - the **import graph** of the source module: every local module in its transitive import
    closure with its tree and direct imports, and every external module those import with the
    pinned package it comes from. Adding, removing or re-homing an import changes the identity.
* the exact bundle bytes, through the hashes of every trusted file.

Together with a submission's `problem_id`, which commits to the source snapshot that accepted
it, the task ID fixes the submission's original verification environment. Nothing mutable —
the current registry, a later release, a cache — can move it elsewhere.

Current build/cache provenance (recorded per snapshot, never part of the ID):

* the **build provenance** of a snapshot: the full text of every local module in the source
  module's import closure plus the source build configuration. It is recorded for each snapshot
  that publishes a version, append-only, and the verifier recomputes it in its container to
  catch an environment whose checkout or cache differs from the one it claims to be. It is never
  used to route work from one snapshot to another.

Every parser here is strict and fails closed. Nothing trusts the closure a bundle claims.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from verifier.errors import ReasonCode, VerifierError
from verifier.hashing import canonical_json_bytes, is_sha256, sha256_bytes, sha256_text


LEGACY_PROVENANCE = "legacy-source-commit"
V2_PROVENANCE = "dependency-identity-v2"
V2_MANIFEST_SCHEMA_VERSION = 3
TASK_VERSION_NAME = "task-version.json"
TASK_VERSION_SCHEMA_VERSION = 1
ENVIRONMENT_SCHEMA_VERSION = 1
DEPENDENCY_SCHEMA_VERSION = 1
IMPORT_SCHEMA_VERSION = 1
CLOSURE_SCHEMA_VERSION = 1
# Bump when the generator changes what a version means in a way its payload bytes do not show.
TASK_GENERATOR_VERSION = "fc-task-generator-v2.0"
# Bump when the acceptance policy changes in a way the hashed policy lists below do not show.
VERIFICATION_POLICY_VERSION = "fc-verification-policy-v1"
V2_TASK_ID_PREFIX = "fc-v2-"
V2_TASK_ID = re.compile(r"^fc-v2-[a-z0-9-]{1,80}-[0-9a-f]{24}-[a-z]+$")
MAX_CLOSURE_BYTES = 128 * 1024 * 1024
MAX_LOCAL_SOURCE_BYTES = 8 * 1024 * 1024
MAX_OLEAN_BYTES = 2 * 1024 * 1024 * 1024
CONSTANT_KINDS = frozenset(
    {"theorem", "axiom", "quotient", "definition", "opaque", "inductive", "constructor", "recursor"}
)
NODE_CHILDREN = {"A": (1, 2), "L": (3, 4), "P": (3, 4), "T": (2, 3, 4), "M": (2,), "J": (3,)}
NODE_ARITY = {"B": 2, "S": 2, "C": 3, "A": 3, "L": 5, "P": 5, "T": 6, "N": 2, "Z": 2, "M": 3, "J": 4}
LEAN_IDENTIFIER_ROOT = re.compile(r"^[A-Za-z_][A-Za-z0-9_']*$")


def canonical_sha256(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def _mismatch(message: str) -> VerifierError:
    return VerifierError(ReasonCode.ENVIRONMENT_MISMATCH, message)


def _dependency_mismatch(message: str) -> VerifierError:
    return VerifierError(ReasonCode.DEPENDENCY_IDENTITY_MISMATCH, message)


def strict_json(content: bytes, label: str) -> Any:
    """Strict UTF-8 JSON: no duplicate keys and no non-finite numbers."""

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=unique,
            parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise _dependency_mismatch(f"{label} is not strict JSON: {exc}") from exc


# --- Lean names -------------------------------------------------------------------------------


def lean_name_parts(value: str) -> tuple[str, ...]:
    """Components of a Lean name as written (`FormalConjectures.ErdosProblems.«10»`)."""
    rooted = value.removeprefix("_root_.")
    parts = tuple(
        part.removeprefix("«").removesuffix("»") for part in re.findall(r"«[^»]*»|[^.]+", rooted)
    )
    if not parts or any(not part for part in parts):
        raise _dependency_mismatch(f"invalid Lean name: {value!r}")
    return parts


def name_from_json(value: Any) -> tuple[Any, ...]:
    """The extractor's unambiguous name encoding: strings, or ["#", n] for numeric components."""
    if not isinstance(value, list) or not value:
        raise _dependency_mismatch("extractor name is not a non-empty component list")
    result: list[Any] = []
    for component in value:
        if isinstance(component, str) and component:
            result.append(component)
        elif (
            isinstance(component, list)
            and len(component) == 2
            and component[0] == "#"
            and type(component[1]) is int
            and component[1] >= 0
        ):
            result.append(("#", component[1]))
        else:
            raise _dependency_mismatch("extractor name component is invalid")
    return tuple(result)


def name_text(name: tuple[Any, ...]) -> str:
    """A readable rendering, escaped like Lean's for non-identifier components."""

    def render(component: Any) -> str:
        if isinstance(component, tuple):
            return str(component[1])
        return component if LEAN_IDENTIFIER_ROOT.fullmatch(component) else f"«{component}»"

    return ".".join(render(component) for component in name)


def name_json(name: tuple[Any, ...]) -> list[Any]:
    return [list(component) if isinstance(component, tuple) else component for component in name]


def module_parts(name: tuple[Any, ...]) -> tuple[str, ...]:
    if any(not isinstance(component, str) for component in name):
        raise _dependency_mismatch(f"module name has a numeric component: {name_text(name)}")
    for component in name:
        if component in {".", ".."} or "/" in component or "\0" in component:
            raise _dependency_mismatch(f"module name is not a safe path: {name_text(name)}")
    return name  # type: ignore[return-value]


# --- Safe reads -------------------------------------------------------------------------------


def read_tree_file(root: Path, relative: Iterable[str], max_bytes: int) -> bytes:
    """Read `root/relative` without following any symlink below `root`.

    Every directory component is opened with O_NOFOLLOW relative to its parent, so neither a
    symlinked directory nor a symlinked file inside the trusted tree can redirect the read.
    """
    parts = tuple(relative)
    if not parts or any(part in {"", ".", ".."} or "/" in part or "\0" in part for part in parts):
        raise _mismatch(f"unsafe trusted path: {'/'.join(parts)!r}")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(root, directory_flags)
    except OSError as exc:
        raise _mismatch(f"trusted tree is unavailable: {root}: {exc}") from exc
    try:
        for part in parts[:-1]:
            child = os.open(part, directory_flags | nofollow, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        handle = os.open(
            parts[-1],
            os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0),
            dir_fd=descriptor,
        )
    except OSError as exc:
        os.close(descriptor)
        raise _mismatch(f"cannot safely open {root}/{'/'.join(parts)}: {exc}") from exc
    os.close(descriptor)
    try:
        if not stat.S_ISREG(os.fstat(handle).st_mode):
            raise _mismatch(f"trusted file is not regular: {'/'.join(parts)}")
        with os.fdopen(handle, "rb", closefd=False) as stream:
            content = stream.read(max_bytes + 1)
    finally:
        os.close(handle)
    if len(content) > max_bytes:
        raise _mismatch(f"trusted file exceeds {max_bytes} bytes: {'/'.join(parts)}")
    return content


# --- Environment identity ---------------------------------------------------------------------


@dataclass(frozen=True)
class EnvironmentIdentity:
    lean_toolchain: str
    lean_commit: str
    mathlib_commit: str
    lake_packages: tuple[tuple[str, str], ...]
    comparator_commit: str
    lean4export_commit: str
    task_support_sha256: str
    workspace_template_sha256: str
    generator_version: str
    verification_policy: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ENVIRONMENT_SCHEMA_VERSION,
            "comparator_commit": self.comparator_commit,
            "generator_version": self.generator_version,
            "lake_packages": [list(item) for item in self.lake_packages],
            "lean4export_commit": self.lean4export_commit,
            "lean_commit": self.lean_commit,
            "lean_toolchain": self.lean_toolchain,
            "mathlib_commit": self.mathlib_commit,
            "task_support_sha256": self.task_support_sha256,
            "verification_policy": dict(self.verification_policy),
            "workspace_template_sha256": self.workspace_template_sha256,
        }

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_dict())

    @classmethod
    def from_dict(cls, value: object) -> "EnvironmentIdentity":
        fields = {
            "schema_version", "comparator_commit", "generator_version", "lake_packages",
            "lean4export_commit", "lean_commit", "lean_toolchain", "mathlib_commit",
            "task_support_sha256", "verification_policy", "workspace_template_sha256",
        }
        if not isinstance(value, dict) or set(value) != fields:
            raise _mismatch("environment identity field set is invalid")
        if value["schema_version"] != ENVIRONMENT_SCHEMA_VERSION:
            raise _mismatch("environment identity schema is unsupported")
        packages = value["lake_packages"]
        if (
            not isinstance(packages, list)
            or not all(
                isinstance(item, list) and len(item) == 2 and all(isinstance(x, str) and x for x in item)
                for item in packages
            )
            or packages != sorted(packages)
            or len({item[0] for item in packages}) != len(packages)
        ):
            raise _mismatch("environment identity Lake packages are invalid")
        for key in ("comparator_commit", "lean4export_commit", "lean_commit", "mathlib_commit"):
            if not _is_commit(value[key]):
                raise _mismatch(f"environment identity {key} is not a commit")
        for key in ("task_support_sha256", "workspace_template_sha256"):
            if not is_sha256(value[key]):
                raise _mismatch(f"environment identity {key} is not a digest")
        if not isinstance(value["verification_policy"], dict) or not all(
            isinstance(value[key], str) and value[key] for key in ("generator_version", "lean_toolchain")
        ):
            raise _mismatch("environment identity policy or versions are invalid")
        return cls(
            lean_toolchain=value["lean_toolchain"],
            lean_commit=value["lean_commit"],
            mathlib_commit=value["mathlib_commit"],
            lake_packages=tuple((item[0], item[1]) for item in packages),
            comparator_commit=value["comparator_commit"],
            lean4export_commit=value["lean4export_commit"],
            task_support_sha256=value["task_support_sha256"],
            workspace_template_sha256=value["workspace_template_sha256"],
            generator_version=value["generator_version"],
            verification_policy=dict(value["verification_policy"]),
        )


def _is_commit(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(character in "0123456789abcdef" for character in value)
    )


def verification_policy() -> dict[str, Any]:
    """The acceptance rules a v2 task is bound to, as data, plus an explicit version."""
    from verifier import static_checks
    from verifier.task_generator import PERMITTED_AXIOMS

    static_policy = {
        "anywhere_prohibited": sorted(static_checks.ANYWHERE_PROHIBITED),
        "max_delimiter_depth": static_checks.MAX_DELIMITER_DEPTH,
        "max_line_length": static_checks.MAX_LINE_LENGTH,
        "max_literal_digits": static_checks.MAX_LITERAL_DIGITS,
        "max_tokens": static_checks.MAX_TOKENS,
        "top_level_prohibited": sorted(static_checks.TOP_LEVEL_PROHIBITED),
    }
    return {
        "permitted_axioms": list(PERMITTED_AXIOMS),
        "static_policy_sha256": canonical_sha256(static_policy),
        "version": VERIFICATION_POLICY_VERSION,
    }


def workspace_template_sha256() -> str:
    """The verification workspace's Lake configuration, with the project path abstracted."""
    from verifier.workspace import _lakefile

    return sha256_text(_lakefile(Path("/fc-verifier-project")))


def _manifest_git_packages(source_root: Path) -> tuple[tuple[str, str], ...]:
    manifest = strict_json(read_tree_file(source_root, ("lake-manifest.json",), 1024 * 1024), "lake-manifest.json")
    packages = manifest.get("packages") if isinstance(manifest, dict) else None
    if not isinstance(packages, list):
        raise _mismatch("source Lake manifest has no package list")
    rows = []
    for package in packages:
        if not isinstance(package, dict) or not isinstance(package.get("name"), str):
            raise _mismatch("source Lake manifest package is invalid")
        if package.get("type") == "git":
            if not _is_commit(package.get("rev")):
                raise _mismatch(f"Lake package {package['name']} is not pinned to a commit")
            rows.append((package["name"], package["rev"]))
    names = [name for name, _rev in rows]
    if len(names) != len(set(names)):
        raise _mismatch("source Lake manifest names a package twice")
    return tuple(sorted(rows))


def _lean_githash(project_root: Path) -> str:
    from verifier.environment import tool_path, trusted_environment

    home = project_root / ".work" / "identity-home"
    (home / ".tmp").mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            (str(tool_path(project_root, "lean")), "--githash"),
            capture_output=True,
            text=True,
            check=True,
            env=dict(trusted_environment(project_root, home)),
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise _mismatch(f"cannot ask the pinned Lean for its commit: {exc}") from exc
    return result.stdout.strip()


def derive_environment_identity(
    project_root: Path,
    *,
    lean_githash: Callable[[Path], str] = _lean_githash,
    repository_commit: Callable[[Path], str] | None = None,
) -> EnvironmentIdentity:
    """Read the environment identity from the installed tools and checkouts, against the pins.

    Every value comes from the actual environment (the Lean binary, the vendored checkouts, the
    validator's Lean). Each is also required to equal `pins.lock.json`, so an environment whose
    tools drifted from its own pins never produces an identity at all.
    """
    from verifier.repository import lean_toolchain, load_pins, mathlib_pin
    from verifier.repository import repository_commit as git_commit

    commit_of = repository_commit or git_commit
    pins = load_pins(project_root)
    source_root = project_root / "vendor" / "formal-conjectures"
    try:
        expected = {
            "lean_toolchain": str(pins["lean"]["toolchain"]),
            "lean_commit": str(pins["lean"]["commit"]),
            "mathlib_commit": str(pins["mathlib"]["commit"]),
            "comparator_commit": str(pins["comparator"]["commit"]),
            "lean4export_commit": str(pins["lean4export"]["commit"]),
        }
    except (KeyError, TypeError) as exc:
        raise _mismatch(f"pins.lock.json lacks an environment pin: {exc}") from exc
    try:
        actual = {
            "lean_toolchain": lean_toolchain(source_root),
            "lean_commit": lean_githash(project_root),
            "mathlib_commit": mathlib_pin(source_root),
            "comparator_commit": commit_of(project_root / "vendor" / "comparator"),
            "lean4export_commit": commit_of(project_root / "vendor" / "lean4export"),
        }
    except VerifierError as exc:
        raise _mismatch(f"environment identity input is unavailable: {exc}") from exc
    project_toolchain = read_tree_file(project_root, ("lean-toolchain",), 4096).decode().strip()
    drift = sorted(key for key in expected if expected[key] != actual[key])
    if drift or project_toolchain != expected["lean_toolchain"]:
        raise _mismatch(
            "environment differs from its pins: "
            + ", ".join(f"{key}: pinned {expected[key]} actual {actual[key]}" for key in drift)
            + ("" if project_toolchain == expected["lean_toolchain"] else "; validator lean-toolchain")
        )
    return EnvironmentIdentity(
        lean_toolchain=actual["lean_toolchain"],
        lean_commit=actual["lean_commit"],
        mathlib_commit=actual["mathlib_commit"],
        lake_packages=_manifest_git_packages(source_root),
        comparator_commit=actual["comparator_commit"],
        lean4export_commit=actual["lean4export_commit"],
        task_support_sha256=sha256_bytes(
            read_tree_file(project_root, ("lean", "TaskSupport.lean"), MAX_LOCAL_SOURCE_BYTES)
        ),
        workspace_template_sha256=workspace_template_sha256(),
        generator_version=TASK_GENERATOR_VERSION,
        verification_policy=verification_policy(),
    )


# --- Trusted trees and the extractor's report -------------------------------------------------


@dataclass(frozen=True)
class SourceTrees:
    """Where every module of a verification environment may legitimately come from."""

    source_root: Path
    project_root: Path
    toolchain_lib: Path
    packages: Mapping[str, Path]

    @classmethod
    def for_project(cls, project_root: Path) -> "SourceTrees":
        from verifier.environment import target_toolchain_bin

        source_root = project_root / "vendor" / "formal-conjectures"
        toolchain_lib = target_toolchain_bin(project_root).parent / "lib" / "lean"
        packages = {
            name: source_root / ".lake" / "packages" / name
            for name, _rev in _manifest_git_packages(source_root)
        }
        return cls(source_root, project_root, toolchain_lib, packages)

    @property
    def local_trees(self) -> tuple[tuple[str, Path, Path], ...]:
        """(origin, source directory, build library directory) for each local package."""
        return (
            ("source", self.source_root, self.source_root / ".lake" / "build" / "lib" / "lean"),
            ("validator", self.project_root / "lean", self.project_root / ".lake" / "build" / "lib" / "lean"),
        )

    def local_roots(self) -> tuple[str, ...]:
        """Top-level module roots that local Lean files can define.

        Over- or under-approximation fails closed: every module's origin is cross-checked
        against the `.olean` Lean actually loaded.
        """
        roots: set[str] = set()
        for _origin, directory, _build in self.local_trees:
            try:
                entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
            except OSError as exc:
                raise _mismatch(f"local Lean tree is unavailable: {directory}: {exc}") from exc
            for entry in entries:
                if entry.name.startswith(".") or entry.is_symlink():
                    continue
                if entry.is_file() and entry.name.endswith(".lean"):
                    stem = entry.name.removesuffix(".lean")
                elif entry.is_dir() and any(Path(entry.path).rglob("*.lean")):
                    stem = entry.name
                else:
                    continue
                if LEAN_IDENTIFIER_ROOT.fullmatch(stem):
                    roots.add(stem)
        if not roots:
            raise _mismatch("no local Lean module roots found")
        return tuple(sorted(roots))


@dataclass(frozen=True)
class ModuleRow:
    name: tuple[Any, ...]
    local: bool
    olean: str
    imports: tuple[tuple[Any, ...], ...]


@dataclass(frozen=True)
class TargetClosure:
    theorem: tuple[Any, ...]
    module: tuple[Any, ...]
    local_constants: tuple[tuple[tuple[Any, ...], tuple[Any, ...], Mapping[str, Any]], ...]
    external_modules: tuple[tuple[Any, ...], ...]
    statement_canonical: str | None


@dataclass(frozen=True)
class ClosureReport:
    lean_githash: str
    local_roots: tuple[str, ...]
    modules: Mapping[tuple[Any, ...], ModuleRow]
    targets: tuple[TargetClosure, ...]
    sha256: str
    # Per-report memo of module origins and artifact digests: one report is classified against
    # one set of trees, and every target in it shares most of its import closure.
    memo: dict[Any, Any] = field(default_factory=dict, compare=False, repr=False)


def _validate_content(content: object, label: str) -> Mapping[str, Any]:
    if not isinstance(content, dict) or content.get("kind") not in CONSTANT_KINDS:
        raise _dependency_mismatch(f"{label}: constant content has no valid kind")
    nodes = content.get("nodes")
    if not isinstance(nodes, list):
        raise _dependency_mismatch(f"{label}: constant nodes are not a list")
    for position, node in enumerate(nodes):
        if not isinstance(node, list) or not node or node[0] not in NODE_ARITY:
            raise _dependency_mismatch(f"{label}: expression node {position} is invalid")
        if len(node) != NODE_ARITY[node[0]]:
            raise _dependency_mismatch(f"{label}: expression node {position} has the wrong arity")
        for child in NODE_CHILDREN.get(node[0], ()):
            if type(node[child]) is not int or not 0 <= node[child] < position:
                raise _dependency_mismatch(f"{label}: expression node {position} is not post-order")
    for key in ("type", "value"):
        index = content.get(key)
        if index is None and key == "value":
            continue
        if type(index) is not int or not 0 <= index < len(nodes):
            raise _dependency_mismatch(f"{label}: constant {key} index is invalid")
    return content


def parse_closure_report(
    content: bytes,
    *,
    expected_roots: tuple[str, ...],
    expected_lean_commit: str,
    expected_targets: Iterable[tuple[str, str]],
) -> ClosureReport:
    """Validate the extractor's JSON. The caller's expected targets are the only ones accepted."""
    if len(content) > MAX_CLOSURE_BYTES:
        raise _dependency_mismatch("dependency closure report is too large")
    value = strict_json(content, "dependency closure report")
    if not isinstance(value, dict) or set(value) != {
        "lean_githash", "local_roots", "modules", "schema_version", "targets",
    }:
        raise _dependency_mismatch("dependency closure report field set is invalid")
    if value["schema_version"] != CLOSURE_SCHEMA_VERSION:
        raise _dependency_mismatch("dependency closure report schema is unsupported")
    if value["lean_githash"] != expected_lean_commit:
        raise _mismatch(
            f"closure was extracted by Lean {value['lean_githash']!r}, not {expected_lean_commit}"
        )
    roots = value["local_roots"]
    if (
        not isinstance(roots, list)
        or sorted(name_text(name_from_json(root)) for root in roots) != sorted(expected_roots)
    ):
        raise _dependency_mismatch("dependency closure used different local roots")
    modules: dict[tuple[Any, ...], ModuleRow] = {}
    if not isinstance(value["modules"], list):
        raise _dependency_mismatch("dependency closure module table is invalid")
    for row in value["modules"]:
        if not isinstance(row, dict) or set(row) != {"imports", "local", "module", "olean"}:
            raise _dependency_mismatch("dependency closure module row is invalid")
        name = name_from_json(row["module"])
        if (
            name in modules
            or type(row["local"]) is not bool
            or not isinstance(row["olean"], str)
            or not row["olean"].startswith("/")
            or not isinstance(row["imports"], list)
        ):
            raise _dependency_mismatch(f"dependency closure module row is invalid: {name_text(name)}")
        modules[name] = ModuleRow(
            name=name,
            local=row["local"],
            olean=row["olean"],
            imports=tuple(name_from_json(item) for item in row["imports"]),
        )
    if not isinstance(value["targets"], list):
        raise _dependency_mismatch("dependency closure targets are invalid")
    expected = [(lean_name_parts(module), lean_name_parts(theorem)) for module, theorem in expected_targets]
    targets: list[TargetClosure] = []
    for row in value["targets"]:
        allowed = {"external_constant_count", "external_modules", "local_constants", "module", "theorem"}
        if not isinstance(row, dict) or set(row) - {"statement_canonical"} != allowed:
            raise _dependency_mismatch("dependency closure target row is invalid")
        theorem = name_from_json(row["theorem"])
        module = name_from_json(row["module"])
        if not isinstance(row["local_constants"], list) or not isinstance(row["external_modules"], list):
            raise _dependency_mismatch("dependency closure target lists are invalid")
        constants = []
        seen: set[tuple[Any, ...]] = set()
        for item in row["local_constants"]:
            if not isinstance(item, dict) or set(item) != {"content", "module", "name"}:
                raise _dependency_mismatch("dependency closure constant row is invalid")
            constant = name_from_json(item["name"])
            if constant in seen:
                raise _dependency_mismatch(f"constant listed twice: {name_text(constant)}")
            seen.add(constant)
            constants.append(
                (constant, name_from_json(item["module"]), _validate_content(item["content"], name_text(constant)))
            )
        canonical = row.get("statement_canonical")
        if canonical is not None and not isinstance(canonical, str):
            raise _dependency_mismatch("statement canonical form is not a string")
        targets.append(
            TargetClosure(
                theorem=theorem,
                module=module,
                local_constants=tuple(sorted(constants, key=lambda item: canonical_json_bytes(name_json(item[0])))),
                external_modules=tuple(
                    sorted({name_from_json(item) for item in row["external_modules"]}, key=lambda n: canonical_json_bytes(name_json(n)))
                ),
                statement_canonical=canonical,
            )
        )
    if [(target.module, target.theorem) for target in targets] != expected:
        raise _dependency_mismatch("dependency closure does not report exactly the requested targets")
    return ClosureReport(
        lean_githash=value["lean_githash"],
        local_roots=tuple(sorted(expected_roots)),
        modules=modules,
        targets=tuple(targets),
        sha256=sha256_bytes(content),
    )


def _module_closure(report: ClosureReport, module: tuple[Any, ...]) -> tuple[tuple[Any, ...], ...]:
    key = ("closure", module)
    if key not in report.memo:
        report.memo[key] = _compute_module_closure(report, module)
    return report.memo[key]


def _compute_module_closure(report: ClosureReport, module: tuple[Any, ...]) -> tuple[tuple[Any, ...], ...]:
    pending = [module]
    seen: set[tuple[Any, ...]] = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        row = report.modules.get(current)
        if row is None:
            raise _dependency_mismatch(f"import closure names an unloaded module: {name_text(current)}")
        seen.add(current)
        pending.extend(item for item in row.imports if item not in seen)
    return tuple(sorted(seen, key=lambda item: canonical_json_bytes(name_json(item))))


@dataclass(frozen=True)
class ModuleOrigin:
    origin: str  # "source", "validator", "lean" or "package:<name>"
    source_sha256: str | None  # local modules only
    olean_path: Path | None = None  # local modules only: the compiled artifact Lean loaded


def classify_module(report: ClosureReport, trees: SourceTrees, row: ModuleRow) -> ModuleOrigin:
    """Where a loaded module really came from, checked against its `.olean`; fails closed."""
    key = ("origin", id(trees), row.name)
    if key not in report.memo:
        report.memo[key] = _classify_module(report, trees, row)
    return report.memo[key]


def _memo_realpath(report: ClosureReport, path: str) -> str:
    # Resolved once per report: the trees do not move while one report is classified.
    key = ("real", path)
    if key not in report.memo:
        report.memo[key] = os.path.realpath(path)
    return report.memo[key]


def _resolved_artifact(report: ClosureReport, path: str) -> str:
    """`realpath(path)`, resolving each directory once; a symlinked file is still followed."""
    directory, name = os.path.split(path)
    resolved = os.path.join(_memo_realpath(report, directory), name)
    return os.path.realpath(resolved) if os.path.islink(resolved) else resolved


def _under(path: str, root: str) -> str | None:
    prefix = root.rstrip(os.sep) + os.sep
    return path[len(prefix):] if path.startswith(prefix) else None


def _resolved_roots(report: ClosureReport, trees: SourceTrees) -> dict[str, Any]:
    key = ("roots", id(trees))
    if key not in report.memo:
        report.memo[key] = {
            "local": [
                (origin, source_dir, _memo_realpath(report, str(build_dir)))
                for origin, source_dir, build_dir in trees.local_trees
            ],
            "toolchain": _memo_realpath(report, str(trees.toolchain_lib)),
            "packages": [
                (package, _memo_realpath(report, str(root / ".lake" / "build" / "lib" / "lean")))
                for package, root in sorted(trees.packages.items())
            ],
        }
    return report.memo[key]


def _classify_module(report: ClosureReport, trees: SourceTrees, row: ModuleRow) -> ModuleOrigin:
    parts = module_parts(row.name)
    olean = _resolved_artifact(report, row.olean)
    roots = _resolved_roots(report, trees)
    root_is_local = parts[0] in report.local_roots
    if root_is_local != row.local:
        raise _dependency_mismatch(f"module locality disagrees with its root: {name_text(row.name)}")
    if row.local:
        matches = []
        for origin, source_dir, build_dir in roots["local"]:
            relative = _under(olean, build_dir)
            if relative is not None:
                matches.append((origin, source_dir, relative))
        if len(matches) != 1:
            raise _dependency_mismatch(
                f"local module was not loaded from exactly one local build: {name_text(row.name)}"
            )
        origin, source_dir, relative = matches[0]
        if relative != "/".join((*parts[:-1], parts[-1] + ".olean")):
            raise _dependency_mismatch(f"local module .olean path is unexpected: {name_text(row.name)}")
        for other, other_source, _build in trees.local_trees:
            if other == origin:
                continue
            candidate = os.path.join(other_source, *parts[:-1], parts[-1] + ".lean")
            if os.path.lexists(candidate):
                raise _dependency_mismatch(f"local module is ambiguous between trees: {name_text(row.name)}")
        content = read_tree_file(source_dir, (*parts[:-1], parts[-1] + ".lean"), MAX_LOCAL_SOURCE_BYTES)
        return ModuleOrigin(origin, sha256_bytes(content), Path(olean))
    if _under(olean, roots["toolchain"]) is not None:
        return ModuleOrigin("lean", None)
    for package, build in roots["packages"]:
        if _under(olean, build) is not None:
            return ModuleOrigin(f"package:{package}", None)
    raise _dependency_mismatch(
        f"external module did not come from the toolchain or a pinned package: {name_text(row.name)}"
    )


@dataclass(frozen=True)
class DependencyIdentity:
    """Declaration-level identity of one source statement. Bound into the v2 task ID."""

    document: Mapping[str, Any]

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.document)

    @property
    def theorem(self) -> str:
        return name_text(tuple(self.document["theorem"]))

    @classmethod
    def from_dict(cls, value: object) -> "DependencyIdentity":
        fields = {
            "schema_version", "theorem", "module", "statement_type_sha256", "local_constants",
            "external_modules", "imports",
        }
        if not isinstance(value, dict) or set(value) != fields or value["schema_version"] != DEPENDENCY_SCHEMA_VERSION:
            raise _dependency_mismatch("dependency identity field set is invalid")
        name_from_json(value["theorem"])
        name_from_json(value["module"])
        if not is_sha256(value["statement_type_sha256"]):
            raise _dependency_mismatch("dependency identity statement digest is invalid")
        constants = value["local_constants"]
        if not isinstance(constants, list) or not all(
            isinstance(item, dict)
            and set(item) == {"kind", "module", "name", "sha256"}
            and item["kind"] in CONSTANT_KINDS
            and is_sha256(item["sha256"])
            for item in constants
        ):
            raise _dependency_mismatch("dependency identity constants are invalid")
        keys = [canonical_json_bytes(item["name"]) for item in constants]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise _dependency_mismatch("dependency identity constants are not sorted and unique")
        if not isinstance(value["external_modules"], list):
            raise _dependency_mismatch("dependency identity external modules are invalid")
        imports = value["imports"]
        if (
            not isinstance(imports, dict)
            or set(imports) != {"external_imports", "local_modules", "module"}
            or imports["module"] != value["module"]
            or not isinstance(imports["local_modules"], list)
            or not isinstance(imports["external_imports"], list)
            or not imports["local_modules"]
            or not all(
                isinstance(item, list) and len(item) == 3 and item[1] in {"source", "validator"}
                for item in imports["local_modules"]
            )
        ):
            raise _dependency_mismatch("dependency identity import graph is invalid")
        return cls(value)


@dataclass(frozen=True)
class BuildProvenance:
    """Full text identity of a snapshot's local import closure. Current build provenance only."""

    document: Mapping[str, Any]

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.document)


def import_graph(report: ClosureReport, trees: SourceTrees, module: tuple[Any, ...]) -> dict[str, Any]:
    key = ("graph", id(trees), module)
    if key not in report.memo:
        report.memo[key] = _import_graph(report, trees, module)
    return report.memo[key]


def _import_graph(report: ClosureReport, trees: SourceTrees, module: tuple[Any, ...]) -> dict[str, Any]:
    """The transitive import graph of `module`, structurally: immutable task provenance.

    Local modules appear with their tree and direct imports; the external modules they import
    appear with the pinned package (or the toolchain) that provides them. External modules'
    own imports are fixed by those pins, which the environment identity already binds.
    """
    local = []
    external: dict[tuple[Any, ...], str] = {}
    for name in _module_closure(report, module):
        row = report.modules[name]
        origin = classify_module(report, trees, row)
        if origin.source_sha256 is None:
            continue
        local.append(
            [
                name_json(name),
                origin.origin,
                sorted((name_json(item) for item in set(row.imports)), key=canonical_json_bytes),
            ]
        )
        for imported in row.imports:
            imported_row = report.modules.get(imported)
            if imported_row is None:
                raise _dependency_mismatch(f"import of an unloaded module: {name_text(imported)}")
            if not imported_row.local:
                external[imported] = classify_module(report, trees, imported_row).origin
    return {
        "external_imports": sorted(
            ([name_json(name), origin] for name, origin in external.items()),
            key=lambda item: canonical_json_bytes(item[0]),
        ),
        "local_modules": local,
        "module": name_json(module),
    }


def dependency_identity(
    report: ClosureReport, trees: SourceTrees, target: TargetClosure, *, statement_type_sha256: str
) -> DependencyIdentity:
    """The statement's transitive definition and import dependencies.

    Each local constant is hashed over its complete kernel content; the import graph is
    recorded structurally. Both are re-derived wherever the version is admitted or verified.
    """
    closure = set(_module_closure(report, target.module))
    rows = []
    theorem_seen = False
    for constant, module, content in target.local_constants:
        row = report.modules.get(module)
        if row is None or not row.local or module not in closure:
            raise _dependency_mismatch(
                f"constant {name_text(constant)} is not defined in the statement's local import closure"
            )
        classify_module(report, trees, row)
        if constant == target.theorem:
            theorem_seen = True
            if content["kind"] != "theorem" or content["value"] is not None:
                raise _dependency_mismatch("the source statement is not a theorem reported by its type")
        rows.append(
            {
                "kind": content["kind"],
                "module": name_json(module),
                "name": name_json(constant),
                "sha256": canonical_sha256(content),
            }
        )
    if not theorem_seen:
        raise _dependency_mismatch(f"the closure does not contain {name_text(target.theorem)} itself")
    if target.statement_canonical is not None and sha256_text(target.statement_canonical) != statement_type_sha256:
        raise _dependency_mismatch("compiled statement type differs from the cataloged type hash")
    return DependencyIdentity(
        {
            "schema_version": DEPENDENCY_SCHEMA_VERSION,
            "external_modules": [name_json(item) for item in target.external_modules],
            "imports": import_graph(report, trees, target.module),
            "local_constants": rows,
            "module": name_json(target.module),
            "statement_type_sha256": statement_type_sha256,
            "theorem": name_json(target.theorem),
        }
    )


def olean_sha256(path: Path) -> str:
    """Digest of one compiled local artifact, read without following a symlinked file."""
    return sha256_bytes(read_tree_file(path.parent, (path.name,), MAX_OLEAN_BYTES))


def build_provenance(report: ClosureReport, trees: SourceTrees, module: tuple[Any, ...]) -> BuildProvenance:
    """Source text and compiled `.olean` of every local module the statement's module imports.

    Recorded per instance when it publishes a version, and recomputed by the verifier: a stale
    or tampered artifact, or a checkout that differs from the published one, cannot match.
    """
    local = []
    external: set[str] = set()
    for name in _module_closure(report, module):
        origin = classify_module(report, trees, report.modules[name])
        if origin.source_sha256 is None:
            external.add(origin.origin)
        else:
            assert origin.olean_path is not None
            key = ("olean", origin.olean_path)
            if key not in report.memo:
                report.memo[key] = olean_sha256(origin.olean_path)
            local.append([name_json(name), origin.origin, origin.source_sha256, report.memo[key]])
    config = sha256_bytes(read_tree_file(trees.source_root, ("lakefile.toml",), 1024 * 1024))
    return BuildProvenance(
        {
            "schema_version": IMPORT_SCHEMA_VERSION,
            "external_origins": sorted(external),
            "local_modules": local,
            "module": name_json(module),
            "source_build_config_sha256": config,
        }
    )


# --- Batch derivation for task generation -------------------------------------------------------


@dataclass(frozen=True)
class DependencyIndex:
    """Identities for one source snapshot, derived by running the pinned extractor here."""

    repository_commit: str
    environment: EnvironmentIdentity
    dependencies: Mapping[str, DependencyIdentity]
    # Current build provenance of each theorem's source module in this snapshot.
    provenance: Mapping[str, BuildProvenance]
    report_sha256: str


def closure_request(trees: SourceTrees, targets: Iterable[tuple[str, str]]) -> bytes:
    return canonical_json_bytes(
        {
            "local_roots": list(trees.local_roots()),
            "targets": [{"module": module, "theorem": theorem} for module, theorem in targets],
        }
    )


def index_from_report(
    report_bytes: bytes,
    *,
    trees: SourceTrees,
    environment: EnvironmentIdentity,
    repository_commit: str,
    declarations: Iterable[Any],
) -> DependencyIndex:
    items = tuple(declarations)
    targets = [(item.module, item.theorem) for item in items]
    report = parse_closure_report(
        report_bytes,
        expected_roots=trees.local_roots(),
        expected_lean_commit=environment.lean_commit,
        expected_targets=targets,
    )
    dependencies: dict[str, DependencyIdentity] = {}
    provenance: dict[str, BuildProvenance] = {}
    by_module: dict[tuple[Any, ...], BuildProvenance] = {}
    for declaration, target in zip(items, report.targets, strict=True):
        if target.statement_canonical is None:
            raise _dependency_mismatch("batch closure must report the canonical statement")
        dependencies[declaration.theorem] = dependency_identity(
            report, trees, target, statement_type_sha256=declaration.type_hash
        )
        if target.module not in by_module:
            by_module[target.module] = build_provenance(report, trees, target.module)
        provenance[declaration.theorem] = by_module[target.module]
    return DependencyIndex(
        repository_commit=repository_commit,
        environment=environment,
        dependencies=dependencies,
        provenance=provenance,
        report_sha256=report.sha256,
    )


def local_tree_snapshot(trees: SourceTrees) -> dict[str, tuple[int, ...]]:
    """Metadata of every local `.lean` source and `.olean` artifact, without reading them.

    Size, inode, and modification and change times: any write between two snapshots shows.
    """
    snapshot: dict[str, tuple[int, ...]] = {}
    for origin, source_dir, build_dir in trees.local_trees:
        for label, root, suffix in (("source", source_dir, ".lean"), ("olean", build_dir, ".olean")):
            if not root.is_dir():
                continue
            for directory, subdirectories, files in os.walk(root):
                subdirectories[:] = [item for item in subdirectories if not item.startswith(".")]
                for file_name in files:
                    if not file_name.endswith(suffix):
                        continue
                    path = Path(directory, file_name)
                    metadata = path.lstat()
                    snapshot[f"{origin}:{label}:{path.relative_to(root)}"] = (
                        metadata.st_mode, metadata.st_size, metadata.st_ino, metadata.st_mtime_ns,
                        metadata.st_ctime_ns,
                    )
    return snapshot


def assert_coherent_build(
    *,
    project_root: Path,
    modules: Iterable[str],
    command_runner: Callable[..., Any],
    env: Mapping[str, str],
) -> None:
    """Lake itself must report every module's compiled artifacts up to date with its sources.

    `lake build --no-build` runs Lake's own trace check over the modules and their whole import
    graph and fails if anything would be rebuilt. A source edited after compiling therefore stops
    derivation instead of pairing an old compiled closure with new source text.
    """
    from verifier.environment import tool_path

    result = command_runner(
        (str(tool_path(project_root, "lake")), "build", "--no-build", *sorted(set(modules))),
        cwd=project_root,
        timeout_seconds=3600,
        env=env,
    )
    if result.timed_out or result.exit_code != 0:
        raise _mismatch(
            "compiled artifacts are not up to date with their sources; rebuild before deriving "
            f"identities: {(result.stderr or result.stdout)[-2000:]}"
        )


def derive_dependency_index(
    *,
    project_root: Path,
    declarations: Iterable[Any],
    environment: EnvironmentIdentity | None = None,
    command_runner: Callable[..., Any] | None = None,
    timeout_seconds: int = 7200,
    repository_commit: Callable[[Path], str] | None = None,
    check_pins: bool = True,
) -> DependencyIndex:
    """Run the pinned `dependency_extractor` over this environment and validate its report.

    Coherence is established around the extraction, not assumed: every local source and
    artifact is snapshotted, Lake must report the build up to date, the closure is extracted,
    identities and build provenance are derived (reading the same sources and artifacts), and
    the local trees must be unchanged at the end. Any difference fails closed.
    """
    from verifier.environment import tool_path, trusted_environment
    from verifier.process import run_process
    from verifier.repository import assert_dependency_pins
    from verifier.repository import repository_commit as git_commit

    items = tuple(declarations)
    if not items:
        raise VerifierError(ReasonCode.INVALID_ARGUMENT, "no declarations to index")
    if check_pins:
        assert_dependency_pins(project_root)
    identity = environment or derive_environment_identity(project_root)
    trees = SourceTrees.for_project(project_root)
    commit = (repository_commit or git_commit)(trees.source_root)
    runner = command_runner or run_process
    work = project_root / ".work"
    work.mkdir(parents=True, exist_ok=True)
    import tempfile

    before = local_tree_snapshot(trees)
    with tempfile.TemporaryDirectory(prefix="dependency-index-", dir=work) as temporary:
        directory = Path(temporary)
        (directory / ".home" / ".tmp").mkdir(parents=True)
        env = trusted_environment(project_root, directory / ".home")
        assert_coherent_build(
            project_root=project_root, modules=(item.module for item in items), command_runner=runner, env=env
        )
        request = directory / "request.json"
        output = directory / "closure.json"
        request.write_bytes(closure_request(trees, ((item.module, item.theorem) for item in items)))
        extractor = project_root / ".lake" / "build" / "bin" / "dependency_extractor"
        result = runner(
            (str(tool_path(project_root, "lake")), "env", str(extractor), str(request), str(output)),
            cwd=project_root,
            timeout_seconds=timeout_seconds,
            env=env,
        )
        if result.timed_out or result.exit_code != 0:
            raise VerifierError(
                ReasonCode.INTERNAL_ERROR,
                f"dependency extraction failed: {(result.stderr or result.stdout)[-4000:]}",
            )
        content = read_tree_file(directory, ("closure.json",), MAX_CLOSURE_BYTES)
    index = index_from_report(
        content, trees=trees, environment=identity, repository_commit=commit, declarations=items
    )
    after = local_tree_snapshot(trees)
    if after != before:
        changed = sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))
        raise _mismatch(f"local sources or artifacts changed during derivation: {changed[:5]}")
    return index


@dataclass(frozen=True)
class InspectedClosure:
    dependency: DependencyIdentity
    build_provenance: BuildProvenance


def check_inspected_closure(
    content: bytes | None,
    *,
    trees: SourceTrees,
    environment: EnvironmentIdentity,
    declaration: Any,
    expected_dependency_sha256: str,
    expected_build_provenance_sha256: str | None,
) -> InspectedClosure:
    """Re-derive a statement's identities from a compiled challenge and require the expected ones.

    The dependency identity is the version's immutable commitment: a mismatch means the
    compiled statement is not the one the task ID names (DEPENDENCY_IDENTITY_MISMATCH). The
    build provenance is this snapshot's recorded environment: a mismatch means this checkout or
    cache is not the environment it claims to be (ENVIRONMENT_MISMATCH). Both fail closed.
    """
    if content is None:
        raise _dependency_mismatch("the inspector did not report a dependency closure")
    report = parse_closure_report(
        content,
        expected_roots=trees.local_roots(),
        expected_lean_commit=environment.lean_commit,
        expected_targets=((declaration.module, declaration.theorem),),
    )
    target = report.targets[0]
    dependency = dependency_identity(report, trees, target, statement_type_sha256=declaration.type_hash)
    if dependency.sha256 != expected_dependency_sha256:
        raise _dependency_mismatch(
            f"compiled {declaration.theorem} has dependency identity {dependency.sha256}, "
            f"the task commits to {expected_dependency_sha256}"
        )
    provenance = build_provenance(report, trees, target.module)
    if expected_build_provenance_sha256 is not None and provenance.sha256 != expected_build_provenance_sha256:
        raise _mismatch(
            f"this environment's build provenance is {provenance.sha256}, "
            f"its snapshot records {expected_build_provenance_sha256}"
        )
    return InspectedClosure(dependency, provenance)


# --- The trusted file and the task ID ---------------------------------------------------------


def task_version_document(
    *,
    environment: EnvironmentIdentity,
    dependency: DependencyIdentity,
    nanoda_commit: str | None,
) -> dict[str, Any]:
    return {
        "dependency": dict(dependency.document),
        "dependency_identity_sha256": dependency.sha256,
        "environment": environment.to_dict(),
        "environment_identity_sha256": environment.sha256,
        "nanoda_commit": nanoda_commit,
        "provenance": V2_PROVENANCE,
        "schema_version": TASK_VERSION_SCHEMA_VERSION,
    }


@dataclass(frozen=True)
class TaskVersionDocument:
    environment: EnvironmentIdentity
    dependency: DependencyIdentity
    nanoda_commit: str | None

    def to_dict(self) -> dict[str, Any]:
        return task_version_document(
            environment=self.environment, dependency=self.dependency, nanoda_commit=self.nanoda_commit
        )


def parse_task_version_document(content: bytes) -> TaskVersionDocument:
    value = strict_json(content, TASK_VERSION_NAME)
    fields = {
        "dependency", "dependency_identity_sha256", "environment", "environment_identity_sha256",
        "nanoda_commit", "provenance", "schema_version",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise _dependency_mismatch("task-version.json field set is invalid")
    if value["schema_version"] != TASK_VERSION_SCHEMA_VERSION or value["provenance"] != V2_PROVENANCE:
        raise _dependency_mismatch("task-version.json schema or provenance is invalid")
    environment = EnvironmentIdentity.from_dict(value["environment"])
    dependency = DependencyIdentity.from_dict(value["dependency"])
    if environment.sha256 != value["environment_identity_sha256"]:
        raise VerifierError(ReasonCode.TRUSTED_FILE_MODIFIED, "environment identity digest is inconsistent")
    if dependency.sha256 != value["dependency_identity_sha256"]:
        raise VerifierError(ReasonCode.TRUSTED_FILE_MODIFIED, "dependency identity digest is inconsistent")
    nanoda = value["nanoda_commit"]
    if nanoda is not None and not _is_commit(nanoda):
        raise _dependency_mismatch("task-version.json Nanoda commit is invalid")
    document = TaskVersionDocument(environment, dependency, nanoda)
    if canonical_json_bytes(document.to_dict()) != canonical_json_bytes(value):
        raise VerifierError(ReasonCode.TRUSTED_FILE_MODIFIED, "task-version.json is not canonical")
    return document


def v2_task_id(*, slug: str, mode: str, descriptor: Mapping[str, Any]) -> str:
    """The v2 task ID: a commitment to every input that determines the bundle.

    The descriptor holds the statement, the dependency and environment identities, the policy
    and every trusted file hash. Outputs computed by Lean (the generated target hash, collision
    findings) are deterministic functions of those inputs and are pinned by the published
    bundle digest instead.
    """
    digest = canonical_sha256({"schema": "fc-task-version-id-v2", **descriptor})[7:31]
    task_id = f"{V2_TASK_ID_PREFIX}{slug}-{digest}-{mode}"
    if V2_TASK_ID.fullmatch(task_id) is None:
        raise VerifierError(ReasonCode.INVALID_MANIFEST, f"v2 task ID is malformed: {task_id}")
    return task_id


def is_v2_task_id(task_id: str) -> bool:
    return V2_TASK_ID.fullmatch(task_id) is not None


__all__ = [
    "LEGACY_PROVENANCE",
    "TASK_VERSION_NAME",
    "V2_MANIFEST_SCHEMA_VERSION",
    "V2_PROVENANCE",
    "ClosureReport",
    "DependencyIdentity",
    "DependencyIndex",
    "BuildProvenance",
    "EnvironmentIdentity",
    "SourceTrees",
    "TaskVersionDocument",
    "derive_dependency_index",
    "derive_environment_identity",
    "InspectedClosure",
    "build_provenance",
    "assert_coherent_build",
    "check_inspected_closure",
    "local_tree_snapshot",
    "dependency_identity",
    "import_graph",
    "index_from_report",
    "is_v2_task_id",
    "parse_closure_report",
    "parse_task_version_document",
    "read_tree_file",
    "task_version_document",
    "v2_task_id",
]
