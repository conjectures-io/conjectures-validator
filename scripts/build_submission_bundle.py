#!/usr/bin/env python3
"""Build a `conjectures-submission/v1` bundle from a Lean proof.

Reference implementation of the miner side of the format. Stdlib only, so a miner can copy
this file without taking on dependencies.

    python3 scripts/build_submission_bundle.py \
      --proof Main.lean \
      --task-id fc-379fc029-erdos89-erdos-89-c956ed476a-formalized-v1 \
      --task-sha256 sha256:<64 hex> \
      --hotkey 5F... \
      --output submission.zip

The archive is written with exactly the two admitted entries, in the required order, with no
extra fields, no comments, and no directory entries. Scan its shape, then run the real verifier
before submitting:

    python3 -m verifier bundle scan --bundle submission.zip
    python3 -m verifier bundle verify --bundle submission.zip --task /path/to/task
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path


BUNDLE_FORMAT = "conjectures-submission/v1"
SCHEMA_VERSION = 1
MANIFEST_NAME = "submission.json"
PROOF_NAME = "Main.lean"
MAX_PROOF_BYTES = 1_000_000


def digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def build_manifest(
    *,
    task_id: str,
    task_sha256: str,
    hotkey: str,
    proof: bytes,
    solver_name: str | None,
    solver_version: str | None,
) -> bytes:
    manifest: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "format": BUNDLE_FORMAT,
        "task_id": task_id,
        "task_bundle_sha256": task_sha256,
        "proof_path": PROOF_NAME,
        "proof_sha256": digest(proof),
        "proof_bytes": len(proof),
        "miner_hotkey": hotkey,
    }
    if solver_name is not None:
        if solver_version is None:
            raise SystemExit("--solver-version is required when --solver-name is given")
        manifest["solver"] = {"name": solver_name, "version": solver_version}
    return json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")


def write_bundle(output: Path, manifest: bytes, proof: bytes) -> None:
    if output.exists():
        raise SystemExit(f"refusing to overwrite {output}")
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as handle:
        # Order matters: submission.json first, then Main.lean.
        for name, data in ((MANIFEST_NAME, manifest), (PROOF_NAME, proof)):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            info.create_system = 3
            handle.writestr(info, data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proof", type=Path, required=True, help="the candidate Main.lean")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--task-sha256", required=True, help="sha256:<64 hex> from GET /v1/tasks")
    parser.add_argument("--hotkey", required=True, help="the submitting miner's SS58 address")
    parser.add_argument("--output", type=Path, default=Path("submission.zip"))
    parser.add_argument("--solver-name")
    parser.add_argument("--solver-version")
    args = parser.parse_args(argv)

    proof = args.proof.read_bytes()
    if not proof:
        raise SystemExit("proof is empty")
    if len(proof) > MAX_PROOF_BYTES:
        raise SystemExit(f"proof is {len(proof)} bytes; the maximum is {MAX_PROOF_BYTES}")
    try:
        proof.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise SystemExit(f"proof is not valid UTF-8: {exc}") from exc
    if b"\x00" in proof:
        raise SystemExit("proof contains a NUL byte")

    manifest = build_manifest(
        task_id=args.task_id,
        task_sha256=args.task_sha256,
        hotkey=args.hotkey,
        proof=proof,
        solver_name=args.solver_name,
        solver_version=args.solver_version,
    )
    write_bundle(args.output, manifest, proof)
    raw = args.output.read_bytes()
    json.dump(
        {
            "bundle": str(args.output),
            "bundle_sha256": digest(raw),
            "bundle_bytes": len(raw),
            "proof_sha256": digest(proof),
            "proof_bytes": len(proof),
        },
        sys.stdout,
        indent=2,
        sort_keys=True,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
