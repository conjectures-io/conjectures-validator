"""The source and bundle links the catalog publishes, built from the pins and the tasks checkout."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from submission_api.links import (
    AUDIT_PATCH_PATH,
    CatalogLinks,
    github_web_base,
    patched_paths,
)
from submission_api.pins import PinSet

BASE = "7d1a8c9912747679d0093f6d1216420c33ee5ffa"
DERIVED = "4b69a7dca3e731dd1f28cb523b9ebd2ea32527f4"
TASKS = "5fe7720f8aeb4423c0329a51601be720575c991f"

PATCH = b"""diff --git a/FormalConjectures/ErdosProblems/107.lean b/FormalConjectures/ErdosProblems/107.lean
index 1111111..2222222 100644
--- a/FormalConjectures/ErdosProblems/107.lean
+++ b/FormalConjectures/ErdosProblems/107.lean
@@ -1 +1 @@
-old
+new
diff --git a/Math15/Collatz.lean b/Math15/Collatz.lean
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/Math15/Collatz.lean
@@ -0,0 +1 @@
+theorem collatz : True := trivial
"""


def pins(patch: bytes = PATCH) -> PinSet:
    return PinSet.from_bytes(
        json.dumps(
            {
                "schema_version": 1,
                "formal_conjectures": {
                    "repository": "https://github.com/google-deepmind/formal-conjectures.git",
                    "base_commit": BASE,
                    "commit": DERIVED,
                    "patch_sha256": hashlib.sha256(patch).hexdigest(),
                },
                "tasks": {
                    "repository": "https://github.com/conjectures-io/conjectures-tasks.git",
                    "commit": TASKS,
                },
            }
        ).encode("utf-8")
    )


def tasks_checkout(tmp_path: Path, patch: bytes = PATCH) -> Path:
    path = tmp_path / AUDIT_PATCH_PATH
    path.parent.mkdir(parents=True)
    path.write_bytes(patch)
    return tmp_path


def test_a_github_clone_url_becomes_its_web_base_and_nothing_else_does():
    assert github_web_base("https://github.com/owner/name.git") == "https://github.com/owner/name"
    assert github_web_base("https://github.com/owner/name") == "https://github.com/owner/name"
    assert github_web_base("https://gitlab.com/owner/name.git") is None
    assert github_web_base("git@github.com:owner/name.git") is None
    assert github_web_base(None) is None


def test_the_patch_parser_finds_touched_and_created_files():
    files = patched_paths(PATCH)
    assert files.touched == {"FormalConjectures/ErdosProblems/107.lean", "Math15/Collatz.lean"}
    assert files.created == {"Math15/Collatz.lean"}


def test_the_source_link_names_the_upstream_base_commit_not_the_derived_one(tmp_path):
    links = CatalogLinks.build(pins(), tasks_root=tasks_checkout(tmp_path))
    url = links.source_url("FormalConjectures/ErdosProblems/107.lean")
    # The derived commit exists only locally; a link to it would 404.
    assert url == (
        "https://github.com/google-deepmind/formal-conjectures/blob/"
        f"{BASE}/FormalConjectures/ErdosProblems/107.lean"
    )
    assert DERIVED not in url
    assert links.source_patched("FormalConjectures/ErdosProblems/107.lean") is True
    assert links.source_patched("FormalConjectures/ErdosProblems/1.lean") is False
    # A file the patch creates has no upstream page at all.
    assert links.source_url("Math15/Collatz.lean") is None
    assert links.source_patched("Math15/Collatz.lean") is True


def test_an_unverifiable_patch_makes_the_answer_unknown_rather_than_false(tmp_path):
    tampered = CatalogLinks.build(
        pins(), tasks_root=tasks_checkout(tmp_path, patch=PATCH + b"\n")
    )
    assert tampered.source_patched("FormalConjectures/ErdosProblems/107.lean") is None
    # Unknown which files the patch created, so no source link is offered either.
    assert tampered.source_url("FormalConjectures/ErdosProblems/1.lean") is None
    missing = CatalogLinks.build(pins(), tasks_root=tmp_path / "absent")
    assert missing.source_patched("FormalConjectures/ErdosProblems/1.lean") is None


def test_the_bundle_link_is_the_loaded_directory_at_the_pinned_tasks_commit(tmp_path):
    root = tasks_checkout(tmp_path)
    links = CatalogLinks.build(pins(), tasks_root=root)
    assert links.bundle_url(root / "pool" / "tier-2" / "erdos-567-formalized") == (
        f"https://github.com/conjectures-io/conjectures-tasks/tree/{TASKS}/"
        "pool/tier-2/erdos-567-formalized"
    )
    assert links.bundle_url(root / "versions" / "fc-v2-task") == (
        f"https://github.com/conjectures-io/conjectures-tasks/tree/{TASKS}/versions/fc-v2-task"
    )
    # A directory outside the checkout is not something the tasks repository holds.
    assert links.bundle_url(tmp_path.parent / "elsewhere") is None


def test_the_tasks_pin_and_the_patch_inputs_are_published():
    published = {pin.component: pin for pin in pins().pins}
    assert published["tasks"].commit == TASKS
    assert published["formal_conjectures"].base_commit == BASE
    assert published["formal_conjectures"].patch_sha256 is not None
