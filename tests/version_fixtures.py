"""A miniature verification environment for version-2 task tests, without Lean.

Lays out exactly what `verifier.task_versions` inspects — a validator project with pins, a
vendored Formal Conjectures checkout with its Lake manifest, local `.lean` sources, an `.olean`
tree per package and a toolchain — and writes dependency-closure reports in the extractor's
exact JSON format. Tests then edit sources, definitions or imports and re-derive identities the
same way production does.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from verifier.models import CatalogDeclaration, Classification
from verifier.task_versions import (
    EnvironmentIdentity,
    SourceTrees,
    TASK_GENERATOR_VERSION,
    index_from_report,
    verification_policy,
    workspace_template_sha256,
)

COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
LEAN_COMMIT = "11acb17ec6b07a8f9e9173e6845197929540936b"
MATHLIB_COMMIT = "065356127b1dc0016f66b7283ce0ce2c4055aa55"
TOOLCHAIN = "leanprover/lean4:v4.35.0-rc2"
LOCAL_IMPORTS = {
    "FormalConjecturesUtil": ["Mathlib"],
    "FormalConjectures.Shared": ["FormalConjecturesUtil"],
    "FormalConjectures.Problems.Pair": ["FormalConjecturesUtil", "FormalConjectures.Shared"],
    "FormalConjectures.Problems.Solo": ["FormalConjecturesUtil"],
}


def name(value: str) -> list[str]:
    return value.split(".")


def node_type(text: str) -> dict[str, Any]:
    """A tiny but well-formed serialized constant: its type is a constant named `text`."""
    return {"kind": "theorem", "levels": [], "nodes": [["C", name(text), []]], "type": 0, "value": None}


def definition(body: str) -> dict[str, Any]:
    return {
        "all": [["x"]],
        "hints": ["regular", 1],
        "kind": "definition",
        "levels": [],
        "nodes": [["S", "z"], ["C", name(body), []]],
        "safety": "safe",
        "type": 0,
        "value": 1,
    }


@dataclass
class MiniEnvironment:
    root: Path
    sources: dict[str, str] = field(default_factory=dict)
    imports: dict[str, list[str]] = field(default_factory=lambda: {k: list(v) for k, v in LOCAL_IMPORTS.items()})
    # theorem -> (module, local constants it reaches: name -> content)
    statements: dict[str, tuple[str, dict[str, dict[str, Any]]]] = field(default_factory=dict)
    lean_commit: str = LEAN_COMMIT
    toolchain: str = TOOLCHAIN

    @property
    def project(self) -> Path:
        return self.root / "validator"

    @property
    def source(self) -> Path:
        return self.project / "vendor" / "formal-conjectures"

    def build(self) -> "MiniEnvironment":
        project, source = self.project, self.source
        (project / "lean").mkdir(parents=True, exist_ok=True)
        (project / "lean" / "TaskSupport.lean").write_text("-- task support\n")
        (project / "lean-toolchain").write_text(self.toolchain + "\n")
        (project / "pins.lock.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "formal_conjectures": {"commit": COMMIT_A},
                    "lean": {"toolchain": self.toolchain, "commit": self.lean_commit},
                    "mathlib": {"commit": MATHLIB_COMMIT},
                    "comparator": {"commit": "c" * 40},
                    "lean4export": {"commit": "e" * 40},
                }
            )
        )
        toolchain_dir = project / ".elan" / "toolchains" / self.toolchain.replace("/", "--").replace(":", "---")
        (toolchain_dir / "bin").mkdir(parents=True, exist_ok=True)
        for tool in ("lake", "lean"):
            # Never executed: tests inject a command runner. `tool_path` only requires them.
            (toolchain_dir / "bin" / tool).write_text("#!/bin/false\n")
            (toolchain_dir / "bin" / tool).chmod(0o755)
        (toolchain_dir / "lib" / "lean" / "Init").mkdir(parents=True, exist_ok=True)
        (toolchain_dir / "lib" / "lean" / "Init" / "Prelude.olean").write_bytes(b"")
        (project / ".lake" / "build" / "lib" / "lean").mkdir(parents=True, exist_ok=True)
        source.mkdir(parents=True, exist_ok=True)
        (source / "lean-toolchain").write_text(self.toolchain + "\n")
        (source / "lakefile.toml").write_text('name = "formal_conjectures"\n')
        (source / "lake-manifest.json").write_text(
            json.dumps({"packages": [{"name": "mathlib", "type": "git", "rev": MATHLIB_COMMIT}]})
        )
        mathlib_lib = source / ".lake" / "packages" / "mathlib" / ".lake" / "build" / "lib" / "lean"
        mathlib_lib.mkdir(parents=True, exist_ok=True)
        (mathlib_lib / "Mathlib.olean").write_bytes(b"")
        for module in self.imports:
            self.write_module(module, self.sources.get(module, f"-- {module}\n"))
        return self

    def write_module(self, module: str, text: str) -> None:
        self.sources[module] = text
        parts = module.split(".")
        path = self.source.joinpath(*parts[:-1], parts[-1] + ".lean")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        olean = self.source.joinpath(".lake", "build", "lib", "lean", *parts[:-1], parts[-1] + ".olean")
        olean.parent.mkdir(parents=True, exist_ok=True)
        olean.write_bytes(b"")

    def olean(self, module: str) -> str:
        if module == "Mathlib":
            return str(self.source / ".lake/packages/mathlib/.lake/build/lib/lean/Mathlib.olean")
        if module == "Init.Prelude":
            return str(next((self.project / ".elan").rglob("Prelude.olean")))
        parts = module.split(".")
        return str(self.source.joinpath(".lake", "build", "lib", "lean", *parts[:-1], parts[-1] + ".olean"))

    @property
    def trees(self) -> SourceTrees:
        toolchain_lib = next((self.project / ".elan").rglob("lib")) / "lean"
        return SourceTrees(
            source_root=self.source,
            project_root=self.project,
            toolchain_lib=toolchain_lib,
            packages={"mathlib": self.source / ".lake" / "packages" / "mathlib"},
        )

    def environment(self, **overrides: Any) -> EnvironmentIdentity:
        values = {
            "lean_toolchain": self.toolchain,
            "lean_commit": self.lean_commit,
            "mathlib_commit": MATHLIB_COMMIT,
            "lake_packages": (("mathlib", MATHLIB_COMMIT),),
            "comparator_commit": "c" * 40,
            "lean4export_commit": "e" * 40,
            "task_support_sha256": "sha256:" + "1" * 64,
            "workspace_template_sha256": workspace_template_sha256(),
            "generator_version": TASK_GENERATOR_VERSION,
            "verification_policy": verification_policy(),
        }
        values.update(overrides)
        return EnvironmentIdentity(**values)

    def report(self, targets: list[tuple[str, str]], *, canonical: bool = True) -> bytes:
        rows = [
            {"imports": [name(item) for item in imports], "local": True, "module": name(module), "olean": self.olean(module)}
            for module, imports in self.imports.items()
        ]
        rows.append({"imports": [name("Init.Prelude")], "local": False, "module": name("Mathlib"), "olean": self.olean("Mathlib")})
        rows.append({"imports": [], "local": False, "module": name("Init.Prelude"), "olean": self.olean("Init.Prelude")})
        target_rows = []
        for module, theorem in targets:
            owner, constants = self.statements[theorem]
            assert owner == module
            row: dict[str, Any] = {
                "external_constant_count": 1,
                "external_modules": [name("Mathlib")],
                "local_constants": [
                    {"content": content, "module": name(self.owner_of(constant, module)), "name": name(constant)}
                    for constant, content in constants.items()
                ],
                "module": name(module),
                "theorem": name(theorem),
            }
            if canonical:
                row["statement_canonical"] = f"canonical {theorem}"
            target_rows.append(row)
        return json.dumps(
            {
                "lean_githash": self.lean_commit,
                "local_roots": [[root] for root in self.trees.local_roots()],
                "modules": rows,
                "schema_version": 1,
                "targets": target_rows,
            }
        ).encode()

    def owner_of(self, constant: str, default: str) -> str:
        return "FormalConjectures.Shared" if constant.startswith("Shared.") else default


def declaration(theorem: str, module: str, *, docstring: str = "fixture") -> CatalogDeclaration:
    from verifier.hashing import sha256_text

    parts = module.split(".")
    return CatalogDeclaration(
        theorem=theorem,
        module=module,
        source_path="/".join(parts) + ".lean",
        category="research open",
        ams_subjects=(5,),
        formal_proof_kind=None,
        formal_proof_link=None,
        declaration_kind="theorem",
        type_pretty="P",
        type_hash=sha256_text(f"canonical {theorem}"),
        contains_answer_annotation=False,
        answer_occurrences=(),
        contains_sorry_in_type=False,
        contains_sorry_in_value=True,
        depends_on_sorry=True,
        transitive_axioms=("propext", "sorryAx"),
        has_parameters=False,
        is_prop=True,
        docstring=docstring,
        supported_modes=("formalized", "counterexample"),
        classification=Classification.DIRECT_PROP,
    )


def standard(root: Path) -> MiniEnvironment:
    """Two targets in one problem module sharing one definition, a third elsewhere."""
    env = MiniEnvironment(root)
    env.statements = {
        "Pair.first": (
            "FormalConjectures.Problems.Pair",
            {"Pair.first": node_type("Pair.firstType"), "Shared.helper": definition("helper.v1"), "Pair.onlyFirst": definition("only.v1")},
        ),
        "Pair.second": (
            "FormalConjectures.Problems.Pair",
            {"Pair.second": node_type("Pair.secondType"), "Shared.helper": definition("helper.v1")},
        ),
        "Solo.third": ("FormalConjectures.Problems.Solo", {"Solo.third": node_type("Solo.thirdType")}),
    }
    return env.build()


def index(env: MiniEnvironment, declarations: list[CatalogDeclaration], *, commit: str = COMMIT_A, environment=None):
    return index_from_report(
        env.report([(item.module, item.theorem) for item in declarations]),
        trees=env.trees,
        environment=environment or env.environment(),
        repository_commit=commit,
        declarations=declarations,
    )


# --- releases -----------------------------------------------------------------------------


class CountingValidator:
    """Stands in for the Lean challenge build: records each build and returns its target hash."""

    def __init__(self):
        self.builds: list[tuple[str, str]] = []

    def __call__(self, task_dir, declaration, generated, mode):
        self.builds.append((declaration.theorem, mode))
        return declaration.type_hash if mode == "formalized" else "sha256:" + "9" * 64


def fake_allowlist(bundles) -> bytes:
    return json.dumps(sorted(bundle.sha256 for bundle in bundles)).encode()


def release(
    env, decls, *, store, previous, commit=COMMIT_A, environment=None, exit_states=None, validator=None, indexed=None
):
    from verifier.incremental import SelectedTarget, publish_release
    from verifier.models import Catalog
    from verifier.version_registry import Instance

    built = index(env, indexed or decls, commit=commit, environment=environment)
    catalog = Catalog(1, commit, env.toolchain, MATHLIB_COMMIT, "test", 0, tuple(indexed or decls))
    validator = validator or CountingValidator()
    result, allowlist = publish_release(
        catalog=catalog,
        targets=[SelectedTarget(item, "tier-1", ("formalized", "counterexample")) for item in decls],
        index=built,
        store=store,
        previous=previous,
        instance=Instance(commit, built.environment.sha256),
        allowlist_for=fake_allowlist,
        exit_states=exit_states or {},
        validate_target=validator,
    )
    return result, validator, built
