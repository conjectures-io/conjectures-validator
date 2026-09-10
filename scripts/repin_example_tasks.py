#!/usr/bin/env python3
"""Repin the checked-in verifier example bundles to the active source revision."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from verifier.hashing import hash_named_files, pretty_json
from verifier.task_generator import TRUSTED_NAMES, task_id


EXAMPLES = ROOT / "examples"
# Read from the pin file rather than restated here: a second copy of the commit is a second thing
# to forget on a repin, and this one would silently stamp example bundles with the previous pin.
REPOSITORY_COMMIT = json.loads(
    (ROOT / "pins.lock.json").read_text(encoding="utf-8")
)["formal_conjectures"]["commit"]


def main() -> int:
    for manifest_path in sorted(EXAMPLES.glob("**/manifest.json")):
        task_dir = manifest_path.parent
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        source_path = task_dir / "source-metadata.json"
        source = json.loads(source_path.read_text(encoding="utf-8"))

        manifest["repository_commit"] = REPOSITORY_COMMIT
        manifest["task_id"] = task_id(
            REPOSITORY_COMMIT,
            manifest["source_theorem"],
            manifest["task_mode"],
            manifest["adapter_version"],
        )
        source["repository_commit"] = REPOSITORY_COMMIT
        source_path.write_text(pretty_json(source), encoding="utf-8")

        manifest["trusted_file_hashes"] = hash_named_files(task_dir, TRUSTED_NAMES)
        manifest_path.write_text(pretty_json(manifest), encoding="utf-8")
        (task_dir / "trusted-hashes.json").write_text(
            pretty_json(manifest["trusted_file_hashes"]),
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
