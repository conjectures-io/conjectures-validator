"""Build a one-problem task pool whose counterexample task is actually provable.

Development only. The audited pool is 74 open theorem targets in two modes each, and by design none
of them can be proved — so it cannot demonstrate that the pipeline reaches `accepted=true`. This builds
a separate pool, with its own allowlist, that can:

    python3 scripts/build_test_pool.py
    # then point the worker at it
    TASK_POOL_ROOT=<out>/pool TASK_ALLOWLIST_PATH=<out>/allowlist.json

The source is `VerifierCounterexampleFixtures.falseUniversal`, the repository's own fixture:

    @[category research open]
    theorem falseUniversal : ∀ n : ℕ, n + 1 = n := by sorry

Unproved in Lean and annotated `research open`, so it satisfies the production policy honestly —
`examples/counterexample/task-counterexample` is already `production_eligible`. And it is false, so
its *counterexample* task is provable in three lines while its *formalized* task is not, which is
the same shape as a real problem pair.

Nothing here writes to `tasks/allowlist.json` or `tasks/pool/`. The audited artifacts are untouched;
this pool exists beside them and is selected by environment variable.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from verifier.hashing import hash_named_files, pretty_json
from verifier.task_generator import TRUSTED_NAMES, problem_id, task_id
from verifier.task_loader import load_task_bundle
from verifier.task_registry import TaskPoolRegistry

SOURCE_EXAMPLE = PROJECT_ROOT / "examples/counterexample/task-counterexample"
TIER = "tier-1"

# The fixture's Challenge imports this module, and `lake build TestFixtures` does not cover it:
# the lean_lib has no globs, so only the root module and its imports are built, and
# TestFixtures.lean does not import Counterexample. Without the olean the challenge fails to
# compile, which the worker reports as CHALLENGE_BUILD_FAILED — its own failure, so it retries to
# the attempt cap and parks the row.
REQUIRED_MODULE = "TestFixtures.Counterexample"
REQUIRED_OLEAN = PROJECT_ROOT / ".lake/build/lib/lean/TestFixtures/Counterexample.olean"

# The allowlist requires every source path to sit under `FormalConjectures/`, because the audited
# pool is drawn from that repository. This fixture lives in the validator's own `lean/` tree, so the
# allowlist records where such a source *would* live. Nothing depends on the two agreeing:
# `TaskPoolRegistry.assert_bundle` compares task id, repository commit, bundle digest, mode, source
# theorems and target digests — never the source path.
ALLOWLIST_SOURCE_PATH = "FormalConjectures/TestFixtures/Counterexample.lean"


def build_formalized(counterexample: Path, destination: Path) -> None:
    """The paired proof task, derived from the counterexample bundle.

    The allowlist refuses a problem that lacks a complete outcome pair, so the pool needs both modes
    even though only the counterexample one is meant to be submitted against. The formalized target
    is the source statement itself — which is false, so this task is unprovable, exactly as its
    counterpart in the audited pool would be.
    """
    shutil.copytree(counterexample, destination)
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))

    # Drop the negation: `¬ (fcTypeOfName% "X")` becomes `fcTypeOfName% "X"`.
    challenge = (destination / "Challenge.lean").read_text(encoding="utf-8")
    negated = f'¬ (fcTypeOfName% "{manifest["source_theorem"]}")'
    plain = f'fcTypeOfName% "{manifest["source_theorem"]}"'
    if negated not in challenge:
        raise SystemExit(f"cannot find the negated target in {destination / 'Challenge.lean'}")
    (destination / "Challenge.lean").write_text(
        challenge.replace(negated, plain), encoding="utf-8"
    )

    manifest["task_mode"] = "formalized"
    # A formalized task's target is definitionally the source type, so their digests coincide. The
    # allowlist and `_validate_manifest` both check that relation for this mode.
    manifest["generated_target_type_hash"] = manifest["source_type_hash"]
    manifest["task_id"] = task_id(
        manifest["repository_commit"],
        manifest["source_theorem"],
        "formalized",
        manifest["adapter_version"],
    )
    # Challenge.lean changed, so its digest has to be recomputed — in both places that record it.
    # The manifest carries its own copy and `load_task_bundle` refuses a bundle where the two
    # disagree. manifest.json is not itself a trusted file, so hashing before writing it is safe.
    hashes = hash_named_files(destination, TRUSTED_NAMES)
    manifest["trusted_file_hashes"] = hashes
    (destination / "manifest.json").write_text(pretty_json(manifest), encoding="utf-8")
    (destination / "trusted-hashes.json").write_text(pretty_json(hashes), encoding="utf-8")


def tier_policy(pool_size: int, source_count: int) -> dict:
    """The declared policy for this tier. Every field is checked by `_valid_tier_policy`.

    The four `*_sha256` fields commit to the selection paperwork behind a real audited tier. This
    pool has none, so they are the digest of the empty JSON document — a stable, honest placeholder
    rather than a digest copied from the audited allowlist, which would imply this pool was selected
    by that audit.
    """
    from verifier.hashing import sha256_bytes

    empty = sha256_bytes(b"{}\n")
    return {
        "classification": "DIRECT_PROP",
        "compiled_target_validation": True,
        "excluded_source_prefixes": [],
        "grouping": "one-problem-per-source",
        "minimum_erdos_tasks": 0,
        "modes": ["formalized", "counterexample"],
        "multi_target_tasks": 0,
        "one_reward_per_problem": True,
        "one_reward_per_reward_family": True,
        "outcomes_per_problem": 2,
        "pool_size": pool_size,
        "reward_family_count": source_count,
        "reward_family_policy": "stable-erdos-number-v1",
        "retired_source_theorems_sha256": empty,
        "selection": "development-fixture",
        "selection_audit_sha256": empty,
        "source_category": "research open",
        "source_theorem_count": source_count,
        "task_groups_sha256": empty,
        "task_scope": "whole_problem",
        "target_relations": {
            "counterexample": "logical-negation",
            "formalized": "definitionally-equal",
        },
        "task_targets_sha256": empty,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_test_pool.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "tasks-test",
        help="directory to create, holding pool/ and allowlist.json",
    )
    parser.add_argument("--audit-date", default="2026-08-03")
    parser.add_argument(
        "--force", action="store_true", help="replace an existing output directory"
    )
    args = parser.parse_args(argv)

    if not REQUIRED_OLEAN.is_file():
        raise SystemExit(
            f"{REQUIRED_MODULE} is not compiled ({REQUIRED_OLEAN} is missing), so the task's "
            "challenge cannot build. Compile it first:\n\n"
            '  ELAN_HOME="$PWD/.elan" PATH="$PWD/.elan/bin:$PATH" '
            f"lake build {REQUIRED_MODULE}\n"
        )

    output = args.output
    if output.exists():
        if not args.force:
            raise SystemExit(f"{output} already exists; pass --force to replace it")
        shutil.rmtree(output)

    tier_root = output / "pool" / TIER
    tier_root.mkdir(parents=True)

    counterexample = tier_root / "false-universal-counterexample"
    shutil.copytree(SOURCE_EXAMPLE, counterexample)
    formalized = tier_root / "false-universal-formalized"
    build_formalized(counterexample, formalized)

    rows = []
    sources = []
    for index, directory in enumerate((formalized, counterexample)):
        bundle = load_task_bundle(directory)
        manifest = bundle.manifest
        if index == 0:
            sources.append(
                {
                    "index": 0,
                    "source_path": ALLOWLIST_SOURCE_PATH,
                    "source_type_sha256": manifest.source_type_hash,
                    "theorem": manifest.source_theorem,
                    "tier": TIER,
                }
            )
        identity = problem_id(
            manifest.repository_commit, (manifest.source_theorem,)
        )
        rows.append(
            {
                "completion_policy": "all_of",
                "mode": manifest.task_mode,
                "problem_id": identity,
                "reward_family_id": identity,
                "source_indices": [0],
                "source_path": ALLOWLIST_SOURCE_PATH,
                "target_type_sha256s": [
                    manifest.source_type_hash
                    if manifest.task_mode == "formalized"
                    else manifest.generated_target_type_hash
                ],
                "task_bundle_sha256": bundle.sha256,
                "task_id": manifest.task_id,
                "theorems": [manifest.source_theorem],
                "tier": TIER,
            }
        )

    allowlist = {
        "allowed_source_theorems": sources,
        "allowed_task_bundles": rows,
        "audit_date_utc": args.audit_date,
        "default": "DENY",
        "repository_commit": rows[0] and load_task_bundle(formalized).manifest.repository_commit,
        "schema_version": 7,
        "tier_order": [TIER],
        "tier_policies": {TIER: tier_policy(len(rows), len(sources))},
    }
    allowlist_path = output / "allowlist.json"
    allowlist_path.write_text(pretty_json(allowlist), encoding="utf-8")

    # Load it back through the real validator, so this script cannot emit an allowlist the worker
    # would refuse at startup.
    registry = TaskPoolRegistry.load(allowlist_path)
    for directory in (formalized, counterexample):
        registry.assert_bundle(load_task_bundle(directory))

    print(f"pool      {output / 'pool'}")
    print(f"allowlist {allowlist_path}")
    for task in sorted(registry.tasks.values(), key=lambda item: item.mode):
        print(f"  {task.mode:<14} {task.task_id}")
    print(f"  problem        {rows[0]['problem_id']}")
    print()
    print("point the worker at it with:")
    print(f"  TASK_POOL_ROOT={output / 'pool'}")
    print(f"  TASK_ALLOWLIST_PATH={allowlist_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
