#!/usr/bin/env python3
"""Generate the published package schema, the synthetic example package and pinned terms digests."""
import copy
import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from submission_api.trajectory_schema import FORMAT, digest, package_schema, parse_package


def example_document():
    """A made-up session in the one-object form tests mutate; `build_package` zips it."""
    proof = b"example : True := by\n  trivial\n"
    proof_sha = digest(proof)
    turns = [
        ("user", "message", "Prove True in Lean. This whole session is a synthetic example.", None, []),
        ("assistant", "message", "I'll try a constructor first; this synthetic attempt will fail.", None, []),
        ("assistant", "tool_call", "lean Solution.lean", "call-1", []),
        ("tool", "tool_result", "error: type mismatch (synthetic, no Lean ran)", "call-1", []),
        ("assistant", "message", "Wrong constructor. Rewriting the proof with `trivial`.", None, []),
        ("assistant", "tool_call", "write Solution.lean", "call-2", ["final-proof"]),
        ("tool", "tool_result", "written", "call-2", []),
        ("assistant", "tool_call", "lean Solution.lean", "call-3", []),
        ("tool", "tool_result", "exit code 0 (synthetic, not a verification claim)", "call-3", []),
        ("assistant", "message", "Solution.lean compiles. No real bounty or submission exists here.", None, []),
    ]
    events = [{
        "id": f"event-{i}", "source": f"synthetic:{i}", "thread_id": "thread-1", "agent_id": "main",
        "parent_agent_id": None, "turn_id": None, "tool_call_id": call, "role": role, "type": kind,
        "timestamp": f"2026-10-12T12:00:{i:02d}Z", "content": content, "tool_arguments": None,
        "tool_result": None, "status": None, "artifact_ids": artifacts, "fidelity": "verbatim",
    } for i, (role, kind, content, call, artifacts) in enumerate(turns, 1)]
    return {
        "manifest": {
            "format": FORMAT, "synthetic": True, "submission_id": None, "task_id": "synthetic-example",
            "task_bundle_sha256": "sha256:" + "0" * 64, "final_proof_sha256": proof_sha,
            "exported_at": "2026-10-12T12:30:00Z", "source_hashes": {}, "counts": {"events": len(events)},
            "models": [{"provider": "Synthetic", "model": "Synthetic model"}],
            "missing_evidence": ["Everything: this is a made-up example, never eligible."],
        },
        "events": events,
        "artifacts": [{"id": "final-proof", "path": "artifacts/Solution.lean", "kind": "final_proof",
                       "sha256": proof_sha, "included": True, "event_ids": ["event-6"]}],
        "files": {"artifacts/Solution.lean": proof},
        "episodes": [{"title": "Wrong constructor, then a fix", "summary": "Retrospective: the first attempt "
                      "failed to compile; the rewrite with `trivial` compiled.", "event_ids":
                      ["event-3", "event-4", "event-5", "event-6", "event-9"], "artifact_ids": ["final-proof"]}],
        "readme": "# Synthetic example package\n\nShows the shape of a chat-log package. Never eligible.\n",
        "sample_html": "<!doctype html><title>Sample</title><p>Synthetic example; see episodes.jsonl.</p>\n",
        "validation": {"checks": [{"name": "events.jsonl parses", "passed": True}]},
    }


def build_package(document, folder="chat-log-package/") -> bytes:
    """Zip the one-object form into the package layout the agent instructions describe."""
    def lines(rows):
        return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode()
    files = {
        "README.md": document["readme"].encode(), "events.jsonl": lines(document["events"]),
        "episodes.jsonl": lines(document["episodes"]), "sample.html": document["sample_html"].encode(),
        "artifact-index.json": json.dumps(document["artifacts"], indent=2).encode(),
        "manifest.json": json.dumps(document["manifest"], indent=2).encode(),
        "validation.json": json.dumps(document["validation"], indent=2).encode(),
        **document["files"],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(folder + name, date_time=(2026, 10, 12, 12, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    return buffer.getvalue()


def main():
    destination = ROOT / "docs/trajectories"
    destination.mkdir(parents=True, exist_ok=True)
    package = build_package(example_document())
    parse_package(package)  # The example passes every structural check (and is refused as synthetic).
    schema = copy.deepcopy(package_schema())
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://conjectures.io/trajectory/chat-log-package.schema.json"
    (destination / "chat-log-package.schema.json").write_text(json.dumps(schema, indent=2) + "\n")
    (destination / "example-package.zip").write_bytes(package)
    for stale in ("trajectory.schema.json", "template.json", "synthetic-example.json"):
        (destination / stale).unlink(missing_ok=True)
    pin_terms(destination)


def pin_terms(destination):
    """Changing the terms or assent text changes these digests and requires fresh assent."""
    def sha(name):
        return "sha256:" + hashlib.sha256((destination / name).read_bytes()).hexdigest()
    manifest = {"TERMS_VERSION": "chat-log-terms-v3", "TERMS_SHA256": sha("chat-log-terms-v3.md"),
                "ASSENT_STATEMENTS_SHA256": sha("assent-statements-v2.json")}
    (destination / "terms-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (ROOT / "submission_api/trajectory_terms.py").write_text(
        '"""Pinned chat-log terms and exact assent text. Changing either requires fresh assent."""\n'
        + "".join(f"{key} = {value!r}\n" for key, value in manifest.items()))


if __name__ == "__main__":
    main()
