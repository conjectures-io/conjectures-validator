from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from verifier.catalog import build_catalog, find_declaration, load_catalog
from verifier.classification import catalog_statistics
from verifier.doctor import doctor_report
from verifier.errors import ReasonCode, VerifierError, exit_code_for
from verifier.hashing import pretty_json
from verifier.models import CATEGORY_ORDER
from verifier.repository import formal_conjectures_pin
from verifier.task_generator import generate_all, generate_task
from verifier.verification import verify
from verifier.workspace import target_validator


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m verifier")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("doctor")

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

    task_all = task_commands.add_parser("generate-all")
    task_all.add_argument("--catalog", type=Path, required=True)
    task_all.add_argument("--category", default="research open")
    task_all.add_argument("--modes", default="positive,negative")
    task_all.add_argument("--output", type=Path, required=True)
    task_all.add_argument("--enable-nanoda", action="store_true")

    verification = subcommands.add_parser("verify")
    verification.add_argument("--task", type=Path, required=True)
    verification.add_argument("--submission", type=Path, required=True)
    verification.add_argument("--retain-workspace", action="store_true")
    return parser


def _print(value: object) -> None:
    sys.stdout.write(pretty_json(value))


def _run(args: argparse.Namespace) -> int:
    if args.command == "doctor":
        report = doctor_report(PROJECT_ROOT)
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
            validate_target=target_validator(PROJECT_ROOT),
        )
        _print(manifest.to_dict())
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
            validate_target=target_validator(PROJECT_ROOT),
        )
        summary_path = args.output.resolve() / "generation-summary.json"
        summary_path.write_text(pretty_json(result), encoding="utf-8")
        _print(result)
        return 0 if result["failed"] == 0 else 2
    if args.command == "verify":
        report = verify(
            task_dir=args.task.resolve(),
            submission_path=args.submission.resolve(),
            project_root=PROJECT_ROOT,
            retain_workspace=args.retain_workspace,
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
