#!/usr/bin/env python3
"""Compile the Hilbert 6 and 12 open-statement packet in the pinned verifier image; audit axioms."""
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD = Path("/tmp/hilbert6-12-build")
EVIDENCE = ROOT / "evidence"
ALLOWED_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}
REVIEWED = {
    "HilbertDrafts/Hilbert6.lean": "b15cea7afccf3daefe69de272cae19970929ede0f93a6934e4326a7de21a25fd",
    "HilbertDrafts/Hilbert12.lean": "2a8006ae7579e5269cca0a88a3d8f1e5dc6898ec04c7d8228cb591e76fe8b19c",
}
NEW = ["HilbertDrafts/Hilbert6MeanField.lean", "HilbertDrafts/Hilbert6MeanFieldChecks.lean",
       "HilbertDrafts/Hilbert12Lambda.lean", "HilbertDrafts/Hilbert12LambdaChecks.lean"]
ATTACKS = {"reviews/attacks-hilbert6": "Rev", "reviews/attacks-hilbert12": "Review"}
TARGETS = ["HilbertDrafts.Hilbert6MeanField.VlasovPoissonMeanFieldLimit",
           "HilbertDrafts.Hilbert12Lambda.Hilbert12LambdaGeometric",
           "HilbertDrafts.Hilbert12Lambda.BorgerDeSmitQuestion4"]
DECLARATION = re.compile(r"^(?:noncomputable )?(?:def|abbrev|structure|theorem|lemma|instance) ([\w.₀-₉']+)")
FORBIDDEN = re.compile(r"\b(sorry|admit|native_decide|unsafe|implemented_by)\b|^axiom\b", re.M)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def module_name(relative):
    return relative[:-5].replace("/", ".")


def compile_file(relative):
    command = ["lake", "env", "lean", "-DwarningAsError=true", f"--root={BUILD}",
               "-o", str((BUILD / relative).with_suffix(".olean")), str(BUILD / relative)]
    result = subprocess.run(command, cwd="/opt/fc-verifier", text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return {"module": relative, "exit_code": result.returncode, "output": result.stdout}


def without_comments(source):
    """Blank out nested block comments and line comments, keeping line breaks."""
    out, depth, i = [], 0, 0
    while i < len(source):
        pair = source[i:i + 2]
        if pair == "/-":
            depth += 1
            i += 2
        elif pair == "-/" and depth:
            depth -= 1
            i += 2
        elif depth:
            out.append("\n" if source[i] == "\n" else " ")
            i += 1
        elif pair == "--":
            end = source.find("\n", i)
            i = len(source) if end < 0 else end
        else:
            out.append(source[i])
            i += 1
    assert depth == 0, "unterminated comment"
    return "".join(out)


def declared(relative):
    """Top-level declarations with their namespace, ignoring comments and docstrings."""
    namespace, names = [], []
    for line in without_comments((BUILD / relative).read_text()).splitlines():
        if match := re.fullmatch(r"namespace (\S+)", line):
            namespace.append(match[1])
        elif re.fullmatch(r"end (\S+)", line) and namespace and line == f"end {namespace[-1]}":
            namespace.pop()
        elif match := DECLARATION.match(line):
            names.append(".".join([*namespace, match[1]]))
    return names


shutil.rmtree(BUILD, ignore_errors=True)
(BUILD / "HilbertDrafts").mkdir(parents=True)
local = []
for relative in [*REVIEWED, *NEW]:
    shutil.copyfile(ROOT / "draft" / relative, BUILD / relative)
    local.append(relative)
for relative, expected in REVIEWED.items():
    assert digest(ROOT / "draft" / relative) == expected, f"{relative} differs from the reviewed module"
for relative in NEW:
    assert not FORBIDDEN.search((ROOT / "draft" / relative).read_text()), relative
attacks = []
for directory, package in ATTACKS.items():
    (BUILD / package).mkdir()
    for path in sorted((ROOT / directory).glob("*.lean")):
        shutil.copyfile(path, BUILD / package / path.name)
        attacks.append(f"{package}/{path.name}")
os.environ["LEAN_PATH"] = f"{BUILD}:" + os.environ.get("LEAN_PATH", "")

# Compile in dependency order, following imports among the local modules.
everything = local + attacks
names = {module_name(relative): relative for relative in everything}
imports = {relative: [names[m] for m in re.findall(r"^import (\S+)", (BUILD / relative).read_text(), re.M)
                      if m in names] for relative in everything}
order, seen = [], set()


def visit(relative):
    if relative in seen:
        return
    seen.add(relative)
    for dependency in imports[relative]:
        visit(dependency)
    order.append(relative)


for relative in everything:
    visit(relative)
results = []
for relative in order:
    results.append(compile_file(relative))
    if results[-1]["exit_code"]:
        break
audited = [name for relative in NEW for name in declared(relative)]
audit = [f"import {module_name(relative)}" for relative in NEW] + [""]
audit += [f"#print axioms {name}" for name in audited]
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
    audit_ok = (results[-1]["exit_code"] == 0 and completed == len(audited)
                and not axioms - ALLOWED_AXIOMS)
    EVIDENCE.mkdir(exist_ok=True)
    (EVIDENCE / "Audit.log").write_text(log)
record = {
    "lean": "4.35.0-rc2",
    "mathlib": "065356127b1dc0016f66b7283ce0ce2c4055aa55",
    "image_id": "sha256:4da29ed9e4448435a979baeda82e934b091abde3754bd9976d8b1df93708fa90",
    "warnings_as_errors": True,
    "source_sha256": {relative: digest(ROOT / "draft" / relative) for relative in [*REVIEWED, *NEW]},
    "reviewer_attack_files": len(attacks),
    "compilation": [{"module": row["module"], "exit_code": row["exit_code"]} for row in results],
    "audited_declarations": len(audited),
    "observed_axioms": sorted(axioms),
    "axiom_audit_passed": audit_ok,
    "closed_targets": TARGETS,
    "passed": all(row["exit_code"] == 0 for row in results) and audit_ok,
    "passed_meaning": "Compilation and axiom audit only. It does not establish any of the statements.",
}
EVIDENCE.mkdir(exist_ok=True)
(EVIDENCE / "validation.json").write_text(json.dumps(record, indent=2) + "\n")
for row in results:
    if row["exit_code"]:
        print(row["output"])
print(json.dumps(record, indent=2))
raise SystemExit(0 if record["passed"] else 1)
