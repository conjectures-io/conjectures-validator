"""Reuse a compiler workspace per thread while inspecting every staged bundle.

Only trusted, locally generated challenge text enters these workspaces. This is
not a submission worker and is never installed into production.
"""

from pathlib import Path
from threading import Lock, local


def compile_challenge(paths, lake, env, timeout):
    """Compile only the generated leaf; upstream dependencies were built already."""
    from verifier.process import run_process

    output = paths.root / ".lake/build/lib/lean/Challenge.olean"
    output.parent.mkdir(parents=True, exist_ok=True)
    return run_process(
        (str(lake), "env", "lean", "-Dwarn.sorry=false", "-o", str(output), "Challenge.lean"),
        cwd=paths.root,
        env=env,
        timeout_seconds=timeout,
    )


class StagedValidator:
    def __init__(self, project: Path, dependency_index=None):
        from verifier.environment import tool_path

        self.project = project
        self.dependency_index = dependency_index
        self.trees = None
        if dependency_index is not None:
            from verifier.task_versions import SourceTrees

            self.trees = SourceTrees.for_project(project)
        self.lake = tool_path(project, "lake")
        self.local = local()
        self.lock = Lock()
        self.workspaces = []

    def __call__(self, task_dir, declaration, _generated, mode):
        from verifier.environment import trusted_environment
        from verifier.workspace import (
            create_workspace,
            inspect_generated_target,
        )

        payloads = {
            name: (task_dir / name).read_bytes()
            for name in ("Challenge.lean", "comparator-config.json")
        }
        paths = getattr(self.local, "paths", None)
        if paths is None:
            paths = create_workspace(task_files=payloads, project_root=self.project)
            self.local.paths = paths
            with self.lock:
                self.workspaces.append(paths)
        else:
            for name, content in payloads.items():
                (paths.root / name).write_bytes(content)
            # Invalidate the local challenge after every change. Dependency
            # caches may be shared; a prior challenge's olean must never be used.
            for subdirectory in ("lib/lean", "ir"):
                for artifact in (paths.root / ".lake/build" / subdirectory).glob("Challenge.*"):
                    artifact.unlink()
        env = trusted_environment(self.project, paths.root / ".home")
        built = compile_challenge(paths, self.lake, env, 1800)
        if built.timed_out or built.exit_code != 0:
            raise ValueError("challenge build failed: " + (built.stderr or built.stdout)[-4000:])
        inspected = inspect_generated_target(
            paths=paths,
            lake=self.lake,
            project_root=self.project,
            source_module=declaration.module,
            source_theorem=declaration.theorem,
            classification=declaration.classification.value,
            mode=mode,
            env=env,
            timeout_seconds=600,
            closure_roots=self.trees.local_roots() if self.trees is not None else None,
        )
        if inspected["source_hash"] != declaration.type_hash or not inspected["matches"]:
            raise ValueError("compiled target or source type differs from catalog")
        if self.dependency_index is not None:
            from verifier.task_versions import check_inspected_closure

            check_inspected_closure(
                inspected["closure"],
                trees=self.trees,
                environment=self.dependency_index.environment,
                declaration=declaration,
                expected_dependency_sha256=self.dependency_index.dependencies[declaration.theorem].sha256,
                expected_build_provenance_sha256=self.dependency_index.provenance[declaration.theorem].sha256,
            )
        if (
            inspected["source_category"] != "research open"
            or inspected["source_declaration_kind"] != "theorem"
            or not inspected["source_depends_on_sorry"]
            or inspected["source_has_formal_proof"]
            or inspected["target_contains_sorry"]
            or inspected["source_axioms"] != tuple(sorted(declaration.transitive_axioms))
        ):
            raise ValueError("compiled source does not meet the direct-proposition policy")
        if mode == "counterexample" and inspected["target_hash"] == inspected["source_hash"]:
            raise ValueError("counterexample target was not negated")
        return inspected["target_hash"]

    def close(self):
        from verifier.workspace import cleanup_workspace

        for paths in self.workspaces:
            cleanup_workspace(paths)
