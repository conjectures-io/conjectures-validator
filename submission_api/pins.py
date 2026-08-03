"""The active pin set, as the public API reports it.

`pins.lock.json` is the operator-facing record: it pins Elan release archives per platform,
the Formal Conjectures checkout, its Mathlib revision, Lean, Comparator, `lean4export`,
Landrun, and Nanoda. README.md's "Pins, cache, and reproducibility" is the policy around it —
production runs exactly one active pin set and never follows a floating branch.

What the website needs from it is narrower: which revision of which component produced a
verdict, so a reader can reproduce it. So this loads the file once at startup and projects it
onto a flat, ordered list of components. Two deliberate omissions:

* **The Elan asset table is dropped.** Per-platform archive digests are how an operator
  verifies a download; they say nothing about a proof and would triple the size of every
  `/v1/catalog/meta` response.
* **Unknown keys are dropped rather than passed through.** A future pin entry is not published
  by accident — it appears here only once someone adds it to `COMPONENT_ORDER`. That is the
  same allowlist-not-denylist rule the public verification report follows.

Loaded once and frozen, like the task catalog: a running API serves one pin set, and a pin
rotation is a restart.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from verifier.hashing import is_sha256

MAX_PINS_BYTES = 256 * 1024

# The order the components are reported in, and the allowlist of what is reported at all.
# `formal_conjectures` and `mathlib` come first because they are the two a reader checking a
# statement actually wants.
COMPONENT_ORDER = (
    "formal_conjectures",
    "mathlib",
    "lean",
    "comparator",
    "lean4export",
    "landrun",
    "nanoda",
    "elan",
)

# Per component, the scalar fields worth publishing. Everything else in the entry is dropped.
COMPONENT_FIELDS = ("repository", "commit", "toolchain", "version", "enabled")


class PinsError(RuntimeError):
    """The pin lock is missing or unreadable, so the process must not start."""


@dataclass(frozen=True)
class Pin:
    """One pinned component. `commit` is absent only for a component pinned by toolchain."""

    component: str
    repository: str | None
    commit: str | None
    toolchain: str | None
    version: str | None
    enabled: bool | None


@dataclass(frozen=True)
class PinSet:
    schema_version: int
    pins: tuple[Pin, ...]
    # The digest of the exact file bytes, so a reader can tell two responses apart without
    # comparing pin lists, and so the ETag on /v1/catalog/meta has something honest behind it.
    lock_sha256: str

    @classmethod
    def load(cls, path: Path) -> PinSet:
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise PinsError(f"cannot read the pin lock at {path}: {exc}") from exc
        if len(raw) > MAX_PINS_BYTES:
            raise PinsError(f"the pin lock at {path} is implausibly large")
        return cls.from_bytes(raw)

    @classmethod
    def from_bytes(cls, raw: bytes) -> PinSet:
        from verifier.hashing import sha256_bytes

        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PinsError(f"the pin lock is not valid UTF-8 JSON: {exc}") from exc
        if not isinstance(document, dict):
            raise PinsError("the pin lock must contain a JSON object")
        try:
            schema_version = int(document["schema_version"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PinsError(f"the pin lock has no usable schema_version: {exc}") from exc

        pins = tuple(
            _pin(name, document[name])
            for name in COMPONENT_ORDER
            if isinstance(document.get(name), dict)
        )
        if not pins:
            raise PinsError(
                "the pin lock names none of the known components: "
                + ", ".join(COMPONENT_ORDER)
            )
        return cls(
            schema_version=schema_version,
            pins=pins,
            lock_sha256=sha256_bytes(raw),
        )

    def get(self, component: str) -> Pin | None:
        for pin in self.pins:
            if pin.component == component:
                return pin
        return None


def _pin(component: str, entry: Mapping[str, Any]) -> Pin:
    def scalar(field: str) -> str | None:
        value = entry.get(field)
        # Only strings are published. A nested object under one of these names is a schema
        # change, and dropping it is the safe reading.
        return value if isinstance(value, str) and value else None

    enabled = entry.get("enabled")
    return Pin(
        component=component,
        repository=scalar("repository"),
        commit=scalar("commit"),
        toolchain=scalar("toolchain"),
        version=scalar("version"),
        enabled=enabled if isinstance(enabled, bool) else None,
    )


def repository_commit(pins: PinSet) -> str | None:
    """The Formal Conjectures commit the task pool was generated from, if it is pinned.

    Cross-checked against the allowlist's own `repository_commit` at startup rather than
    trusted: two files claiming different source revisions is a misconfiguration that must not
    reach a miner as a task whose statement does not match its pin.
    """
    pin = pins.get("formal_conjectures")
    return None if pin is None else pin.commit


def assert_agrees_with_catalog(pins: PinSet, catalog_repository_commit: str) -> None:
    """Refuse to start when the pin lock and the audited allowlist disagree on the source."""
    pinned = repository_commit(pins)
    if pinned is None:
        raise PinsError("the pin lock does not pin a formal_conjectures commit")
    if not is_sha256(f"sha256:{pinned}") and len(pinned) != 40:
        raise PinsError(f"the pinned formal_conjectures commit is malformed: {pinned!r}")
    if pinned != catalog_repository_commit:
        raise PinsError(
            "the pin lock and the task allowlist disagree on the source revision: "
            f"pins.lock.json has {pinned}, the allowlist has {catalog_repository_commit}"
        )


__all__ = [
    "COMPONENT_FIELDS",
    "COMPONENT_ORDER",
    "Pin",
    "PinSet",
    "PinsError",
    "assert_agrees_with_catalog",
    "repository_commit",
]
