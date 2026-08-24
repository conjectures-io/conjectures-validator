"""One build recipe, two callers.

The Dockerfile used to inline its own copy of the steps in `build_trusted_cache.sh`, and the two
drifted: the script grew `TestFixtures.Counterexample` and the image never got it, while the image
hardcoded a toolchain the pin file already recorded. A miner building the verifier from source is
only as trustworthy as the claim that they are running the same build the validator runs, so these
tests are about that claim rather than about Lean.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_trusted_cache.sh"

STUB = """#!/bin/sh
printf '%s %s %s\\n' "$(basename "$PWD")" "$(basename "$0")" "$*" >> "$BUILD_LOG"
"""


def _stub_tree(tmp_path: Path) -> Path:
    """A checkout shaped like the real one, with every build tool replaced by a logging stub."""
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / SCRIPT.name).write_text(SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    for package in ("mathlib", "aesop"):
        (root / "vendor/formal-conjectures/.lake/packages" / package).mkdir(parents=True)
    # Left behind by the Lake build the stub replaces, and where the answer-mode stamp lands.
    (root / "vendor/formal-conjectures/.lake/build").mkdir(parents=True)
    for checkout in ("lean4export", "comparator", "landrun"):
        (root / "vendor" / checkout).mkdir(parents=True)
    (root / "security").mkdir()
    (root / "security" / "seccomp-launcher.c").write_text("int main(void){return 0;}\n", encoding="utf-8")

    binaries = root / "stub-bin"
    binaries.mkdir()
    for tool in ("lake", "go", "cc"):
        stub = binaries / tool
        stub.write_text(STUB, encoding="utf-8")
        stub.chmod(0o755)
    return root


def _build(root: Path, *arguments: str) -> list[str]:
    log = root / "build.log"
    log.write_text("", encoding="utf-8")
    environment = {
        **os.environ,
        "PATH": f"{root / 'stub-bin'}{os.pathsep}/usr/bin{os.pathsep}/bin",
        "BUILD_LOG": str(log),
    }
    subprocess.run(
        ["bash", str(root / "scripts" / SCRIPT.name), *arguments],
        env=environment,
        check=True,
        capture_output=True,
    )
    logged = log.read_text(encoding="utf-8").splitlines()
    return [line.replace(str(root.resolve()), "<root>") for line in logged]


def test_an_unknown_stage_is_refused_rather_than_silently_skipped(tmp_path):
    """Both halves must run somewhere; a typo that quietly builds neither would ship an empty image."""
    root = _stub_tree(tmp_path)
    refused = subprocess.run(
        ["bash", str(root / "scripts" / SCRIPT.name), "--stage", "everything"],
        capture_output=True,
        check=False,
    )
    assert refused.returncode == 2


def test_the_two_stages_together_are_the_whole_build(tmp_path):
    """The Dockerfile runs them in separate layers, so nothing may fall between them."""
    root = _stub_tree(tmp_path)
    split = _build(root, "--stage", "vendor") + _build(root, "--stage", "root")
    whole = _build(_stub_tree(tmp_path / "second"), "--stage", "all")

    assert split == whole


def test_the_vendor_stage_builds_only_the_pinned_checkouts(tmp_path):
    """It runs in a layer that verifier-source edits must not invalidate."""
    root = _stub_tree(tmp_path)
    log = _build(root, "--stage", "vendor")

    assert "formal-conjectures lake build FormalConjectures" in log
    assert "lean4export lake build lean4export" in log
    assert "comparator lake build comparator" in log
    assert not any("VerifierLean" in line for line in log)


def test_the_root_stage_builds_the_test_fixture_the_image_used_to_miss(tmp_path):
    """`lake build TestFixtures` compiles the root module alone, which does not import this one."""
    root = _stub_tree(tmp_path)
    log = _build(root, "--stage", "root")

    built = next(line for line in log if "VerifierLean" in line)
    assert "TestFixtures.Counterexample" in built
    assert "catalog_extractor" in built and "task_inspector" in built


def test_the_root_stage_reuses_formal_conjectures_packages(tmp_path):
    """Mathlib alone is 6.5 GB, and both manifests already fix it at the same revision."""
    root = _stub_tree(tmp_path)
    _build(root, "--stage", "root")

    mathlib = root / ".lake/packages/mathlib"
    assert mathlib.is_symlink()
    assert mathlib.resolve() == (root / "vendor/formal-conjectures/.lake/packages/mathlib")


def test_an_existing_package_directory_is_left_alone(tmp_path):
    """A checkout that predates the symlinks holds gigabytes that must not be relinked away."""
    root = _stub_tree(tmp_path)
    existing = root / ".lake/packages/mathlib"
    existing.mkdir(parents=True)
    _build(root, "--stage", "root")

    assert not existing.is_symlink()


@pytest.mark.skipif(platform.system() != "Linux", reason="the sandbox tooling is Linux-only")
def test_a_miner_build_skips_the_tooling_that_protects_a_validator(tmp_path):
    """Landrun and the seccomp launcher defend against hostile proofs, not against one's own.

    Skipping them is what takes `golang-go` and `build-essential` off the list of packages a miner
    has to install before setup can start.
    """
    root = _stub_tree(tmp_path)
    log = _build(root, "--stage", "all", "--miner")

    assert not any(line.startswith("landrun go ") for line in log)
    assert not any("seccomp-launcher" in line for line in log)
    assert "comparator lake build comparator" in log


@pytest.mark.skipif(platform.system() != "Linux", reason="the sandbox tooling is Linux-only")
def test_a_validator_build_still_produces_both(tmp_path):
    root = _stub_tree(tmp_path)
    log = _build(root, "--stage", "all")

    assert any(line.startswith("landrun go build") for line in log)
    assert any("seccomp-launcher" in line for line in log)


def test_the_dockerfile_carries_no_second_copy_of_the_recipe():
    """The failure this whole miner-verification feature exists to prevent, caught at review time."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "build_trusted_cache.sh --stage vendor" in dockerfile
    assert "build_trusted_cache.sh --stage root" in dockerfile
    for inlined in ("lake exe cache get", "lake build", "go build", "seccomp-launcher.c"):
        assert inlined not in dockerfile


def test_the_image_normalizes_checkout_modes_before_dropping_privileges():
    """A release checkout's umask must not decide whether UID 10001 can import the verifier."""
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    copied = dockerfile.index("COPY . .")
    normalized = dockerfile.index("-exec chmod a+rX {} +")
    root_build = dockerfile.index("build_trusted_cache.sh --stage root")
    non_root = dockerfile.index("USER verifier")

    assert copied < normalized < root_build < non_root
    normalization = dockerfile[copied:root_build]
    for trusted_cache in ("vendor", ".elan", ".lake"):
        assert f"-path './{trusted_cache}'" in normalization


def test_the_elan_download_comes_from_the_pin_file(tmp_path):
    """The URL, the digest and the default toolchain, none of them written down twice.

    Driven through a stub curl so the assertion is on what the installer actually asks for rather
    than on the shape of the script: it fetched the pinned version, and refused an archive whose
    sha256 was not the pinned one.
    """
    root = tmp_path / "checkout"
    (root / "scripts").mkdir(parents=True)
    installer = ROOT / "scripts" / "install_elan.sh"
    (root / "scripts" / installer.name).write_text(installer.read_text(encoding="utf-8"), encoding="utf-8")
    (root / "pins.lock.json").write_text(
        (ROOT / "pins.lock.json").read_text(encoding="utf-8"), encoding="utf-8"
    )

    binaries = root / "stub-bin"
    binaries.mkdir()
    requested = root / "curl.log"
    curl = binaries / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> {requested}\n'
        'while [ $# -gt 0 ]; do\n'
        '  if [ "$1" = "-o" ]; then shift; printf substitute > "$1"; fi\n'
        "  shift\n"
        "done\n",
        encoding="utf-8",
    )
    curl.chmod(0o755)

    attempt = subprocess.run(
        ["bash", str(root / "scripts" / installer.name)],
        env={
            **os.environ,
            "PATH": f"{binaries}{os.pathsep}/usr/bin{os.pathsep}/bin",
            "ELAN_HOME": str(root / ".elan"),
        },
        capture_output=True,
        check=False,
    )

    pins = json.loads((ROOT / "pins.lock.json").read_text(encoding="utf-8"))
    assert f"/download/v{pins['elan']['version']}/" in requested.read_text(encoding="utf-8")
    assert b"hash mismatch" in attempt.stderr
    assert attempt.returncode != 0


def test_the_elan_release_is_named_in_one_place_only():
    """The version was hardcoded in bootstrap.sh and read from the pin file in the Dockerfile."""
    installer = (ROOT / "scripts" / "install_elan.sh").read_text(encoding="utf-8")
    assert "pins.lock.json" in installer

    for caller in ("Dockerfile", "scripts/bootstrap.sh"):
        text = (ROOT / caller).read_text(encoding="utf-8")
        assert "install_elan.sh" in text
        assert "elan/releases/download" not in text
        assert "leanprover/lean4:v" not in text
