#!/usr/bin/env python3
"""Compile the review packet inside the pinned verifier image; audit dependencies."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "draft"
EVIDENCE = ROOT / "evidence"
EVIDENCE.mkdir(exist_ok=True)
os.environ["LEAN_PATH"] = str(SOURCE) + ":" + os.environ.get("LEAN_PATH", "")
ALLOWED_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}

# Scope is a separate reviewed record, not a conclusion of the Lean compiler.
coverage_path = ROOT / "coverage.json"
coverage = json.loads(coverage_path.read_text())
status = json.loads((ROOT / "status.json").read_text())
assert {row["id"] for row in coverage["problems"]} == {"6", "12", "13_algebraic", "16"}
missing_scope = [row["id"] for row in coverage["problems"]
                 if not row["full_scope_statement_present"] or row["gaps"]]
assert not coverage["full_request_complete"] or not missing_scope
assert status["full_request_complete"] == coverage["full_request_complete"]


def source_without_comments_or_strings(source):
    """Mask nested Lean comments and quoted strings, preserving line breaks."""
    out = []
    i = depth = 0
    quoted = escaped = False
    while i < len(source):
        ch = source[i]
        pair = source[i:i + 2]
        if depth:
            if pair == "/-":
                depth += 1
                out.extend("  ")
                i += 2
                continue
            if pair == "-/":
                depth -= 1
                out.extend("  ")
                i += 2
                continue
            out.append("\n" if ch == "\n" else " ")
        elif quoted:
            out.append("\n" if ch == "\n" else " ")
            if escaped:
                escaped = False
            elif ch == chr(92):
                escaped = True
            elif ch == '"':
                quoted = False
        elif pair == "/-":
            depth = 1
            out.extend("  ")
            i += 2
            continue
        elif pair == "--":
            end = source.find("\n", i)
            if end < 0:
                end = len(source)
            out.extend(" " * (end - i))
            i = end
            continue
        elif ch == '"':
            quoted = True
            out.append(" ")
        else:
            out.append(ch)
        i += 1
    assert not depth and not quoted, "Unterminated comment or string in inventory source"
    return "".join(out)


def declared_names(path):
    """Inventory top-level declarations, accounting for namespace/section ends."""
    scopes = []
    names = []
    for line in source_without_comments_or_strings(path.read_text()).splitlines():
        if match := re.fullmatch(r"namespace (\S+)", line):
            scopes.append(("namespace", match[1]))
        elif re.fullmatch(r"(?:noncomputable )?section(?: \S+)?", line):
            scopes.append(("section", ""))
        elif re.fullmatch(r"end(?: \S+)?", line):
            scopes.pop()
        elif match := re.match(r"(?:noncomputable )?(?:def|abbrev|structure|inductive|theorem|lemma|axiom|instance) ([\w.]+)", line):
            prefix = ".".join(name for kind, name in scopes if kind == "namespace")
            names.append(f"{prefix}.{match[1]}")
    assert not scopes, path
    return names


def compile_file(path):
    command = ["lake", "env", "lean", "-DwarningAsError=true", f"--root={SOURCE}",
               "-o", str(path.with_suffix(".olean")), str(path)]
    result = subprocess.run(command, cwd="/opt/fc-verifier", text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (EVIDENCE / f"{path.stem}.log").write_text(result.stdout)
    return {"module": str(path.relative_to(SOURCE)), "exit_code": result.returncode,
            "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


paths = sorted((SOURCE / "HilbertDrafts").glob("Hilbert*.lean"))
assert len(paths) == 4
names = [name for path in paths for name in declared_names(path)]
ledger_path = ROOT / "primary-source-coverage.json"
ledger = json.loads(ledger_path.read_text())
assert {row["problem"] for row in ledger["clauses"]} == {"6", "12"}
assert len({row["id"] for row in ledger["clauses"]}) == len(ledger["clauses"])
for row in ledger["clauses"]:
    assert set(row["declarations"]) <= set(names), row["id"]
unaccepted_clauses = [row["id"] for row in ledger["clauses"] if not row["review_accepted"]]
assert not ledger["full_source_scope_accepted"] or not unaccepted_clauses
assert not coverage["full_request_complete"] or ledger["full_source_scope_accepted"]
targets = ["HilbertDrafts.Hilbert6Rigid.ClassicalLimitsProgram",
           "HilbertDrafts.Hilbert6Rigid.ContinuumRigidLimit",
           "HilbertDrafts.Hilbert6Rigid.ElasticityToStiff",
           "HilbertDrafts.Hilbert6Rigid.PolynomialMaterialWitness",
           "HilbertDrafts.Hilbert6Fluid.ClassicalGasProgram",
           "HilbertDrafts.Hilbert6Kinetic.NewtonBoltzmannLimit",
           "HilbertDrafts.Hilbert12Universal.RationalBenchmark",
           "HilbertDrafts.Hilbert12CM.ImaginaryQuadraticBenchmark",
           "HilbertDrafts.Hilbert12Universal.RationalAllArguments",
           "HilbertDrafts.Hilbert12ClassField.ClassFieldClassification",
           "HilbertDrafts.Hilbert12ClassField.ConductorDiscriminant",
           "HilbertDrafts.Hilbert12ClassField.CyclotomicReciprocity",
           "HilbertDrafts.Hilbert12Stark.RankOneAbelianStark",
           "HilbertDrafts.Hilbert13.AlgebraicHilbert13",
           "HilbertDrafts.Hilbert16.UniformBound",
           "HilbertDrafts.Hilbert16Classification.HarnackSharpBound",
           "HilbertDrafts.Hilbert16Classification.QuarticSurfaceMaximum",
           "HilbertDrafts.Hilbert16Classification.EffectiveHilbert16"]
audit = ["import HilbertDrafts", "", "-- Generated inventory: every explicit declaration."]
audit += [f"#print axioms {name}" for name in names]
audit += [""] + [f"example : Prop := {target}" for target in targets]
audit += [f"#print {target}" for target in targets]
audit += ["#print HilbertDrafts.Hilbert13.ParameterStep",
          "example (R : HilbertDrafts.Hilbert12Universal.Recipe) : Prop := HilbertDrafts.Hilbert12Universal.Hilbert12For R",
          "#print HilbertDrafts.Hilbert12Universal.Hilbert12For",
          "#print HilbertDrafts.Hilbert12Universal.GenerationContract",
          "#print HilbertDrafts.Hilbert6Fluid.ParticleFluidLimit"]
audit += ["#print HilbertDrafts.Hilbert16Classification.AlgebraicClassification",
          "#print HilbertDrafts.Hilbert16Classification.CycleClassification",
          "#check HilbertDrafts.Hilbert16Classification.ClassificationOutput",
          "#check HilbertDrafts.Hilbert12ClassField.ArithmeticOutput",
          "#print HilbertDrafts.Hilbert12Universal.FieldHilbert12For"]
(SOURCE / "Audit.lean").write_text("\n".join(audit) + "\n")
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    results = list(pool.map(compile_file, paths))
if all(row["exit_code"] == 0 for row in results):
    results.append(compile_file(SOURCE / "HilbertDrafts.lean"))
if all(row["exit_code"] == 0 for row in results):
    results.append(compile_file(SOURCE / "Audit.lean"))

axioms = set()
audit_ok = False
audit_log = EVIDENCE / "Audit.log"
if all(row["exit_code"] == 0 for row in results) and audit_log.exists():
    dependencies = re.findall(r"depends on axioms:\s*\[([^\]]*)\]", audit_log.read_text())
    for entry in dependencies:
        axioms.update(x.strip() for x in entry.split(",") if x.strip())
    requested = (SOURCE / "Audit.lean").read_text().count("#print axioms ")
    completed = len(dependencies) + audit_log.read_text().count("does not depend on any axioms")
    audit_ok = completed == requested and not (axioms - ALLOWED_AXIOMS)

record = {
    "lean": "4.35.0-rc2",
    "mathlib": "065356127b1dc0016f66b7283ce0ce2c4055aa55",
    "image_id": "sha256:4da29ed9e4448435a979baeda82e934b091abde3754bd9976d8b1df93708fa90",
    "warnings_as_errors": True,
    "audited_declarations": len(names),
    "closed_targets": targets,
    "recipe_dependent_schema": "HilbertDrafts.Hilbert12Universal.Hilbert12For",
    "compilation": results,
    "observed_axioms": sorted(axioms),
    "axiom_audit_passed": audit_ok,
    "passed": all(row["exit_code"] == 0 for row in results) and audit_ok,
    "passed_meaning": "Compilation and axiom audit only; not mathematical fidelity or full-request completion.",
    "full_request_complete": coverage["full_request_complete"],
    "scope_coverage": {
        "source": "coverage.json",
        "source_sha256": hashlib.sha256(coverage_path.read_bytes()).hexdigest(),
        "missing_full_scope": missing_scope,
        "review": coverage["full_scope_review"],
    },
    "primary_source_review": {
        "source": ledger_path.name,
        "source_sha256": hashlib.sha256(ledger_path.read_bytes()).hexdigest(),
        "declaration_references_valid": True,
        "unaccepted_clauses": unaccepted_clauses,
        "full_source_scope_accepted": ledger["full_source_scope_accepted"],
    },
    "scope": "Statement drafts and proved sanity lemmas; no proof of an open Hilbert target.",
}
(EVIDENCE / "validation.json").write_text(json.dumps(record, indent=2) + "\n")
print(json.dumps(record, indent=2))
raise SystemExit(0 if record["passed"] else 1)
