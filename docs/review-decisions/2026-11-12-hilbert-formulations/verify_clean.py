#!/usr/bin/env python3
"""Build the standalone Lake package from source in a fresh directory."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "draft"
EVIDENCE = ROOT / "evidence"
PINNED_PACKAGES = Path("/opt/fc-verifier/.lake/packages")
ALLOWED_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}
EVIDENCE.mkdir(exist_ok=True)

config = tomllib.loads((SOURCE / "lakefile.toml").read_text())
manifest = json.loads((SOURCE / "lake-manifest.json").read_text())
mathlib = next(p for p in manifest["packages"] if p["name"] == "mathlib")
assert mathlib["rev"] == next(p for p in config["require"] if p["name"] == "mathlib")["rev"]
assert set(config["defaultTargets"]) == {"HilbertDrafts", "Audit"}
assert "-DwarningAsError=true" in config["moreLeanArgs"]

lean_sources = sorted([*SOURCE.glob("*.lean"), *(SOURCE / "HilbertDrafts").rglob("*.lean")])
files = lean_sources + [SOURCE / n for n in
                        ("lakefile.toml", "lake-manifest.json", "lean-toolchain")]
source_hashes = {str(p.relative_to(SOURCE)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in files}
requested = re.findall(r"^#print axioms (\S+)$", (SOURCE / "Audit.lean").read_text(), re.M)
assert requested and len(requested) == len(set(requested))
env = os.environ.copy()
env.pop("LEAN_PATH", None)
env.pop("LEAN_SRC_PATH", None)

with tempfile.TemporaryDirectory(prefix="hilbert-clean-") as tmp:
    clean = Path(tmp)
    for source in files:
        target = clean / source.relative_to(SOURCE)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    assert not list(clean.rglob("*.olean"))
    (clean / ".lake").mkdir()
    (clean / ".lake/packages").symlink_to(PINNED_PACKAGES, target_is_directory=True)
    # Only the pinned dependencies are reused. Every project module, including
    # the aggregate and the audit, must be compiled in this fresh package.
    version = subprocess.run(["lake", "env", "lean", "--version"], cwd=clean,
                             env=env, text=True, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, check=True).stdout.strip()
    result = subprocess.run(["lake", "build"], cwd=clean, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (EVIDENCE / "clean-lake.log").write_text(result.stdout)
    artifacts = {str(p.relative_to(SOURCE)):
                 (clean / ".lake/build/lib/lean" / p.relative_to(SOURCE).with_suffix(".olean")).is_file()
                 for p in lean_sources}
    generated_manifest = json.loads((clean / "lake-manifest.json").read_text())
    assert generated_manifest == manifest, "Lake changed the pinned dependency manifest"

observed = {}
for name, axioms in re.findall(r"'(HilbertDrafts\.[^']+)' depends on axioms:\s*\[([^\]]*)\]",
                               result.stdout):
    assert name not in observed, f"Duplicate audit result: {name}"
    observed[name] = {a.strip() for a in axioms.split(",") if a.strip()}
for name in re.findall(r"'(HilbertDrafts\.[^']+)' does not depend on any axioms", result.stdout):
    assert name not in observed, f"Duplicate audit result: {name}"
    observed[name] = set()
all_axioms = set().union(*observed.values()) if observed else set()
audit_ok = set(observed) == set(requested) and not (all_axioms - ALLOWED_AXIOMS)
record = {
    "method": "Fresh source-only standalone package; lake build includes every module and Audit",
    "command": "lake build",
    "lean": version,
    "mathlib": mathlib["rev"],
    "network": "disabled by Docker invocation",
    "reused_project_artifacts": False,
    "reused_pinned_dependency_cache": True,
    "warnings_as_errors": True,
    "source_sha256": source_hashes,
    "exit_code": result.returncode,
    "compiled_artifacts": artifacts,
    "audited_declarations": len(observed),
    "observed_axioms": sorted(all_axioms),
    "axiom_audit_passed": audit_ok,
    "passed": result.returncode == 0 and all(artifacts.values()) and audit_ok,
    "full_request_complete": json.loads((ROOT / "coverage.json").read_text())["full_request_complete"],
    "scope": "Compilation of the supplied formulations; not a proof of full historical coverage.",
}
(EVIDENCE / "clean-build.json").write_text(json.dumps(record, indent=2) + "\n")
print(json.dumps(record, indent=2))
raise SystemExit(0 if record["passed"] else 1)
