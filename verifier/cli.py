from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

from verifier.bundle import bundle_verdict, read_bundle_file
from verifier.catalog import build_catalog, find_declaration, load_catalog
from verifier.classification import catalog_statistics
from verifier.doctor import doctor_report
from verifier.errors import ReasonCode, VerifierError, exit_code_for
from verifier.hashing import pretty_json
from verifier.models import CATEGORY_ORDER
from verifier.preflight import verify_proof_bundle_file
from verifier.repository import formal_conjectures_pin
from verifier.task_generator import MAX_SUBMISSION_BYTES, generate_all, generate_task
from verifier.task_loader import load_task_bundle
from verifier.verification import verify
from verifier.workspace import target_validator


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m verifier")
    subcommands = parser.add_subparsers(dest="command", required=True)
    doctor = subcommands.add_parser("doctor")
    doctor.add_argument("--allow-insecure-development", action="store_true")

    catalog = subcommands.add_parser("catalog")
    catalog_commands = catalog.add_subparsers(dest="catalog_command", required=True)
    catalog_build = catalog_commands.add_parser("build")
    catalog_build.add_argument("--repo-dir", type=Path, required=True)
    catalog_build.add_argument("--output", type=Path, required=True)
    catalog_stats = catalog_commands.add_parser("stats")
    catalog_stats.add_argument("--catalog", type=Path, required=True)

    task = subcommands.add_parser("task")
    task_commands = task.add_subparsers(dest="task_command", required=True)
    task_generate = task_commands.add_parser("generate")
    task_generate.add_argument("--catalog", type=Path, required=True)
    task_generate.add_argument("--theorem", required=True)
    task_generate.add_argument("--mode", required=True)
    task_generate.add_argument("--output", type=Path, required=True)
    task_generate.add_argument("--enable-nanoda", action="store_true")
    task_generate.add_argument("--allow-non-open", action="store_true")

    task_all = task_commands.add_parser("generate-all")
    task_all.add_argument("--catalog", type=Path, required=True)
    task_all.add_argument("--category", default="research open")
    task_all.add_argument("--modes", default="formalized,counterexample")
    task_all.add_argument("--output", type=Path, required=True)
    task_all.add_argument("--enable-nanoda", action="store_true")
    task_all.add_argument("--allow-non-open", action="store_true")

    bundle = subcommands.add_parser("bundle")
    bundle_commands = bundle.add_subparsers(dest="bundle_command", required=True)
    bundle_scan = bundle_commands.add_parser("scan")
    bundle_scan.add_argument("--bundle", type=Path, required=True)
    bundle_scan.add_argument("--max-proof-bytes", type=int, default=MAX_SUBMISSION_BYTES)
    bundle_verify = bundle_commands.add_parser("verify")
    bundle_verify.add_argument("--bundle", type=Path, required=True)
    bundle_verify.add_argument("--task", type=Path, required=True)
    bundle_verify.add_argument("--allow-insecure-development", action="store_true")

    verification = subcommands.add_parser("verify")
    verification.add_argument("--task", type=Path, required=True)
    verification.add_argument("--submission", type=Path, required=True)
    verification.add_argument("--retain-workspace", action="store_true")
    verification.add_argument("--expected-task-sha256")
    verification.add_argument("--allow-uncommitted-task", action="store_true")
    verification.add_argument("--allow-insecure-development", action="store_true")
    verification.add_argument("--allow-test-task", action="store_true")
    return parser


def _absolute_without_resolving(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _print(value: object) -> None:
    sys.stdout.write(pretty_json(value))


def _run(args: argparse.Namespace) -> int:
    if args.command == "doctor":
        report = doctor_report(
            PROJECT_ROOT, insecure_development=args.allow_insecure_development
        )
        _print(report)
        return 0 if report["ready"] else 2
    if args.command == "catalog" and args.catalog_command == "build":
        result = build_catalog(
            repo_dir=args.repo_dir.resolve(),
            output=args.output.resolve(),
            project_root=PROJECT_ROOT,
            expected_commit=formal_conjectures_pin(PROJECT_ROOT),
        )
        _print(
            {
                "catalog": str(args.output),
                "repository_commit": result.repository_commit,
                **catalog_statistics(result.declarations),
            }
        )
        return 0
    if args.command == "catalog" and args.catalog_command == "stats":
        _print(catalog_statistics(load_catalog(args.catalog).declarations))
        return 0
    if args.command == "task" and args.task_command == "generate":
        catalog = load_catalog(args.catalog)
        declaration = find_declaration(catalog, args.theorem)
        manifest = generate_task(
            catalog=catalog,
            declaration=declaration,
            mode=args.mode,
            output=args.output.resolve(),
            enable_nanoda=args.enable_nanoda,
            allow_non_open=args.allow_non_open,
            validate_target=target_validator(
                PROJECT_ROOT,
                allow_non_open=args.allow_non_open,
            ),
        )
        _print({**manifest.to_dict(), "task_bundle_sha256": load_task_bundle(args.output.resolve()).sha256})
        return 0
    if args.command == "task" and args.task_command == "generate-all":
        if args.category not in CATEGORY_ORDER:
            raise VerifierError(
                ReasonCode.INVALID_ARGUMENT,
                f"unknown category {args.category!r}; expected one of {CATEGORY_ORDER}",
            )
        catalog = load_catalog(args.catalog)
        declarations = tuple(item for item in catalog.declarations if item.category == args.category)
        result = generate_all(
            catalog=catalog,
            declarations=declarations,
            modes=tuple(mode.strip() for mode in args.modes.split(",") if mode.strip()),
            output=args.output.resolve(),
            enable_nanoda=args.enable_nanoda,
            allow_non_open=args.allow_non_open,
            validate_target=target_validator(
                PROJECT_ROOT,
                allow_non_open=args.allow_non_open,
            ),
        )
        result = {
            **result,
            "tasks": [
                {
                    **item,
                    "task_bundle_sha256": load_task_bundle(Path(item["path"])).sha256,
                }
                for item in result["tasks"]
            ],
        }
        summary_path = args.output.resolve() / "generation-summary.json"
        summary_path.write_text(pretty_json(result), encoding="utf-8")
        _print(result)
        return 0 if result["failed"] == 0 else 2
    if args.command == "bundle" and args.bundle_command == "scan":
        verdict = bundle_verdict(
            read_bundle_file(_absolute_without_resolving(args.bundle)),
            max_proof_bytes=args.max_proof_bytes,
        )
        _print(dict(verdict))
        return 0 if verdict["admitted"] else 1
    if args.command == "bundle" and args.bundle_command == "verify":
        result = verify_proof_bundle_file(
            bundle_path=_absolute_without_resolving(args.bundle),
            task_dir=_absolute_without_resolving(args.task),
            project_root=PROJECT_ROOT,
            allow_insecure_development=args.allow_insecure_development,
        )
        _print(result.report.to_dict())
        return exit_code_for(result.report.reason_code, result.report.accepted)
    if args.command == "verify":
        report = verify(
            task_dir=_absolute_without_resolving(args.task),
            submission_path=_absolute_without_resolving(args.submission),
            project_root=PROJECT_ROOT,
            retain_workspace=args.retain_workspace,
            expected_task_sha256=args.expected_task_sha256,
            allow_uncommitted_task=args.allow_uncommitted_task,
            allow_insecure_development=args.allow_insecure_development,
            allow_test_task=args.allow_test_task,
        )
        _print(report.to_dict())
        return exit_code_for(report.reason_code, report.accepted)
    raise VerifierError(ReasonCode.INVALID_ARGUMENT, "unhandled command")


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return _run(_parser().parse_args(argv))
    except VerifierError as exc:
        _print({"accepted": False, "reason_code": exc.reason.value, "error": str(exc)})
        return exit_code_for(exc.reason)
    except KeyboardInterrupt:
        _print({"accepted": False, "reason_code": ReasonCode.INTERNAL_ERROR.value, "error": "interrupted"})
        return 2
    except Exception as exc:
        _print({"accepted": False, "reason_code": ReasonCode.INTERNAL_ERROR.value, "error": str(exc)})
        return 2
