"""Which Git the verifier will run, and which it refuses to.

This is consulted on the verification path, not only during setup: `assert_dependency_pins` shells
out to it for every proof. A host that resolves nothing here builds for an hour and then fails its
first verification with REPOSITORY_NOT_FOUND.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from verifier.errors import ReasonCode, VerifierError
from verifier.repository import GIT_EXECUTABLE_ENV, git_executable


def _executable(path: Path, *, mode: int = 0o755) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(mode)
    return path


def test_an_absolute_override_is_used_verbatim(tmp_path, monkeypatch):
    fake = _executable(tmp_path / "git")
    monkeypatch.setenv(GIT_EXECUTABLE_ENV, str(fake))

    assert git_executable() == fake


def test_an_override_is_never_silently_replaced_by_a_search(tmp_path, monkeypatch):
    """An operator who names a path meant that path.

    Falling back to `PATH` here would run a different Git than the one asked for and report
    success, which is the failure mode the absolute path existed to prevent in the first place.
    """
    monkeypatch.setenv(GIT_EXECUTABLE_ENV, str(tmp_path / "absent"))

    with pytest.raises(VerifierError) as raised:
        git_executable()
    assert raised.value.reason is ReasonCode.REPOSITORY_NOT_FOUND


def test_a_relative_override_is_refused(tmp_path, monkeypatch):
    """Relative paths resolve against the working directory, which the caller does not control."""
    _executable(tmp_path / "git")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(GIT_EXECUTABLE_ENV, "git")

    with pytest.raises(VerifierError):
        git_executable()


def test_a_world_writable_executable_is_refused(tmp_path, monkeypatch):
    """Git decides which commit a checkout is at, and that decides whether a verdict may exist."""
    monkeypatch.setenv(GIT_EXECUTABLE_ENV, str(_executable(tmp_path / "git", mode=0o757)))

    with pytest.raises(VerifierError):
        git_executable()


def test_an_executable_in_a_world_writable_directory_is_refused(tmp_path, monkeypatch):
    """It could be swapped between the check and the call, so the check would prove nothing."""
    directory = tmp_path / "bin"
    directory.mkdir()
    fake = _executable(directory / "git")
    directory.chmod(directory.stat().st_mode | stat.S_IWOTH)
    monkeypatch.setenv(GIT_EXECUTABLE_ENV, str(fake))

    with pytest.raises(VerifierError):
        git_executable()


@pytest.mark.skipif(not os.access("/usr/bin/git", os.X_OK), reason="no /usr/bin/git on this host")
def test_the_image_still_resolves_exactly_what_it_always_did(monkeypatch):
    """`/usr/bin/git` stays first, so nothing about the container's behaviour changes."""
    monkeypatch.delenv(GIT_EXECUTABLE_ENV, raising=False)

    assert git_executable() == Path("/usr/bin/git")
