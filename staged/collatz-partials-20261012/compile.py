"""Compile independent question snippets in the previously pinned verifier image."""

from pathlib import Path
import hashlib
import json
import subprocess
import sys

root = Path("/opt/fc-verifier")
sys.path.insert(0, str(root))
from verifier.environment import trusted_environment, tool_path  # noqa: E402

src = Path("/inputs")
out = Path("/outputs")
home = root / ".work/home"
(home / ".tmp").mkdir(parents=True)
build = root / ".work/build"
build.mkdir()
env = trusted_environment(root, home)
env["LEAN_PATH"] = str(build)
rows = []
for file in ["PartialCollatz.lean", "C12.lean", "C13.lean", "C14.lean", "Fixtures.lean"]:
    p = src / file
    if not p.is_file():
        raise FileNotFoundError(f"Required independent source missing: {p}")
    command = [
        str(tool_path(root, "lake")),
        "env",
        "lean",
        "--trust=0",
        "--root=" + str(src),
        "-o",
        str(build / (p.stem + ".olean")),
        str(p),
    ]
    result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
    (out / (p.stem + ".log")).write_text(result.stdout + result.stderr)
    row = {
        "file": file,
        "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        "exit_code": result.returncode,
    }
    rows.append(row)
    print(json.dumps(row), flush=True)
    if result.returncode:
        print(result.stdout + result.stderr, flush=True)
        break
(out / "lean-compile.json").write_text(
    json.dumps(
        {
            "checks": rows,
            "all_passed": all(r["exit_code"] == 0 for r in rows),
            "research_problems_proved": False,
        },
        indent=2,
    )
    + "\n"
)
raise SystemExit(any(r["exit_code"] != 0 for r in rows))
