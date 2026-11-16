#!/usr/bin/env python3
"""Compile the Hilbert 16 open-case packet in the pinned verifier image and audit its axioms."""
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD = Path("/tmp/hilbert16-open-build")
EVIDENCE = ROOT / "evidence"
ALLOWED_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}
REVIEWED_HILBERT16 = "2a0330f6e2424f6a3bb069ac4fe3ce01df5101252f9694597872d489451c0b18"
MODULES = ["HilbertDrafts/Hilbert16.lean", "HilbertDrafts/Hilbert16Open.lean",
           "HilbertDrafts/Hilbert16OpenChecks.lean"]
ATTACKS = ["ReviewA1.lean", "ReviewA2.lean", "ReviewA3.lean"]
TARGETS = ["HilbertDrafts.Hilbert16Open.QuadraticFourCycles",
           "HilbertDrafts.Hilbert16Open.QuinticSurfaceComponents"]
DECLARATION = re.compile(r"^(?:noncomputable )?(?:def|abbrev|structure|theorem|lemma|instance) ([\w.]+)")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compile_file(relative):
    command = ["lake", "env", "lean", "-DwarningAsError=true", f"--root={BUILD}",
               "-o", str((BUILD / relative).with_suffix(".olean")), str(BUILD / relative)]
    result = subprocess.run(command, cwd="/opt/fc-verifier", text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return {"module": relative, "exit_code": result.returncode, "output": result.stdout}


def declared(relative):
    namespace, names = "", []
    for line in (BUILD / relative).read_text().splitlines():
        if match := re.fullmatch(r"namespace (\S+)", line):
            namespace = match[1]
        elif match := DECLARATION.match(line):
            names.append(f"{namespace}.{match[1]}")
    return names


shutil.rmtree(BUILD, ignore_errors=True)
(BUILD / "HilbertDrafts").mkdir(parents=True)
for relative in MODULES:
    shutil.copyfile(ROOT / "draft" / relative, BUILD / relative)
for name in ATTACKS:
    shutil.copyfile(ROOT / "reviews/attacks" / name, BUILD / "HilbertDrafts" / name)
os.environ["LEAN_PATH"] = f"{BUILD}:" + os.environ.get("LEAN_PATH", "")
assert digest(ROOT / "draft" / MODULES[0]) == REVIEWED_HILBERT16, "Hilbert16.lean differs from the reviewed module"
for relative in MODULES[1:]:
    text = (ROOT / "draft" / relative).read_text()
    assert not re.search(r"\b(sorry|admit|native_decide|axiom|unsafe|implemented_by)\b", text), relative

results = []
for relative in MODULES + [f"HilbertDrafts/{name}" for name in ATTACKS]:
    results.append(compile_file(relative))
    if results[-1]["exit_code"]:
        break
names = [name for relative in MODULES[1:] for name in declared(relative)]
names += [name for attack in ATTACKS for name in declared(f"HilbertDrafts/{attack}")]
audit = ["import HilbertDrafts.Hilbert16OpenChecks", "import HilbertDrafts.ReviewA2",
         "import HilbertDrafts.ReviewA3", ""]
audit += [f"#print axioms {name}" for name in names]
audit += [f"example : Prop := {target}" for target in TARGETS]
audit += [f"#print {target}" for target in TARGETS]
(BUILD / "Audit.lean").write_text("\n".join(audit) + "\n")
axioms, audit_ok = set(), False
if all(row["exit_code"] == 0 for row in results):
    results.append(compile_file("Audit.lean"))
    log = results[-1]["output"]
    found = re.findall(r"depends on axioms:\s*\[([^\]]*)\]", log)
    for entry in found:
        axioms.update(item.strip() for item in entry.split(",") if item.strip())
    completed = len(found) + log.count("does not depend on any axioms")
    audit_ok = (results[-1]["exit_code"] == 0 and completed == len(names)
                and not axioms - ALLOWED_AXIOMS)
    (EVIDENCE / "Audit.log").write_text(log)
record = {
    "lean": "4.35.0-rc2",
    "mathlib": "065356127b1dc0016f66b7283ce0ce2c4055aa55",
    "image_id": "sha256:4da29ed9e4448435a979baeda82e934b091abde3754bd9976d8b1df93708fa90",
    "warnings_as_errors": True,
    "source_sha256": {relative: digest(ROOT / "draft" / relative) for relative in MODULES},
    "reviewer_attack_sha256": {name: digest(ROOT / "reviews/attacks" / name) for name in ATTACKS},
    "compilation": [{"module": row["module"], "exit_code": row["exit_code"]} for row in results],
    "audited_declarations": len(names),
    "observed_axioms": sorted(axioms),
    "axiom_audit_passed": audit_ok,
    "closed_targets": TARGETS,
    "passed": all(row["exit_code"] == 0 for row in results) and audit_ok,
    "passed_meaning": "Compilation and axiom audit only. It does not establish either conjecture.",
}
EVIDENCE.mkdir(exist_ok=True)
(EVIDENCE / "validation.json").write_text(json.dumps(record, indent=2) + "\n")
for row in results:
    if row["exit_code"]:
        print(row["output"])
print(json.dumps(record, indent=2))
raise SystemExit(0 if record["passed"] else 1)
