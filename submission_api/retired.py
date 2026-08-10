"""Conjectures that have left the pool: readable forever, admissible never.

Retiring a target deletes its bundles and drops it from `allowlist.json`, which is what closes
it to submission. But everything the public catalog renders — statement, docstring, references,
AMS subjects, `Challenge.lean` — lived *inside* those bundles, so the same edit also erased the
problem from the website, along with the results and attribution already earned against it. A
solver who proved something got their page 404'd as a reward.

The task repository closes that gap with `tiers/<tier>/retired-conjectures.json`, recovered from
the commit that deleted each bundle. This module loads it into an index the catalog router
consults *after* the live one misses.

Two properties matter, and both are structural rather than a matter of care:

* **Nothing here can widen admission.** `TaskCatalog` is built from `allowlist.json` alone and
  never consults this file; the submission path resolves against `TaskCatalog`, not against the
  index below. A retired conjecture has no `MachineContract` — there is no bundle to compile
  against and no digest a miner could commit to — so there is not even a shape in which a
  submission for one could be assembled.
* **What is served is the audited bytes.** The file is checked against
  `retired_conjectures_sha256` in the tier policy, which is itself inside the allowlist the
  registry already validates. Each `Challenge.lean` was checked against its own manifest's
  digest when the file was generated. So the statement shown for a target whose bundle no longer
  exists is still provably the one that was audited.

Absent is fine; wrong is not. A tier policy that publishes no digest simply has no retired
conjectures. One that publishes a digest with no file behind it, or bytes that do not match, is
a startup failure — that combination means the pinned checkout is not the release the allowlist
describes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from submission_api.slugs import slug_for
from verifier.models import CatalogDeclaration
from verifier.task_policy import PRODUCTION_TASK_MODES

RETIRED_FILE_NAME = "retired-conjectures.json"
RETIRED_SCHEMA_VERSION = 1
TIERS_DIR_NAME = "tiers"
# The tier-policy field naming the digest of the file below.
DIGEST_FIELD = "retired_conjectures_sha256"
# Generous against the largest plausible retired set: the payload is one `Challenge.lean` plus a
# source declaration per task, and the whole 272-bundle pool is smaller than this.
MAX_RETIRED_BYTES = 16 * 1024 * 1024


class RetiredPoolError(RuntimeError):
    """The retired set does not match the allowlist it is published beside.

    Always raised during startup. The retired index is immutable once built, so a mismatch that
    does not stop the process here cannot appear later in a request.
    """


@dataclass(frozen=True)
class RetiredTask:
    """One task that was issued against a retired target, as it stood before deletion.

    Deliberately *not* a `TaskEntry`. A `TaskEntry` carries the `TaskManifest` a verifier runs
    against — permitted axioms, forbidden dependencies, timeouts, trusted file hashes — and none
    of that survives in the display payload, nor should it be reconstructed from anything weaker.
    Publishing a fabricated contract for an unbuildable task would be worse than publishing none.
    """

    task_id: str
    task_mode: str
    task_bundle_sha256: str
    target_type_sha256: str
    # `Challenge.lean` exactly as it was hashed into the bundle digest above.
    challenge_lean: str


@dataclass(frozen=True)
class RetiredConjecture:
    """One retired target, shaped to answer the same reads a live `Conjecture` answers."""

    slug: str
    problem_id: str
    reward_target_id: str
    tier: str
    retired_on: str
    reason_code: str
    reason: str
    # Only a retirement decided under the manual reward-review policy has a published rationale.
    # A dependency or audit retirement has none, and null is the honest answer.
    decision_url: str | None
    recovered_from_commit: str
    source: CatalogDeclaration
    tasks: tuple[RetiredTask, ...]

    @property
    def classification(self) -> str:
        """Read from the source declaration rather than a manifest, which no longer exists.

        The two agree by construction: the manifest's classification is copied from the source
        declaration when the bundle is generated.
        """
        return self.source.classification.value

    @property
    def task_modes(self) -> tuple[str, ...]:
        return tuple(task.task_mode for task in self.tasks)

    @property
    def task_ids(self) -> tuple[str, ...]:
        return tuple(task.task_id for task in self.tasks)


@dataclass(frozen=True)
class RetiredIndex:
    """Slug-addressable retired conjectures, built once at startup."""

    by_slug: Mapping[str, RetiredConjecture]
    # Task ids from every retired bundle, so a URL minted from one still resolves to a page
    # instead of 404ing once the bundle it named is gone.
    slug_by_task_id: Mapping[str, str]

    @classmethod
    def empty(cls) -> RetiredIndex:
        return cls(by_slug={}, slug_by_task_id={})

    @classmethod
    def load(cls, *, allowlist_path: Path) -> RetiredIndex:
        """Load every tier's retired set, verified against the digest its tier policy publishes.

        The allowlist is read again here rather than threaded through `TaskPoolRegistry`, which
        keeps the tier policies out of the registry's surface: nothing on the submission path
        needs them, and this is the only reader.
        """
        policies = _tier_policies(allowlist_path)
        tiers_root = allowlist_path.parent / TIERS_DIR_NAME

        by_slug: dict[str, RetiredConjecture] = {}
        for tier in sorted(policies):
            digest = policies[tier].get(DIGEST_FIELD)
            if digest is None:
                # This tier retires nothing, or predates the field. Either way there is no file
                # to look for and no claim to check.
                continue
            path = tiers_root / tier / RETIRED_FILE_NAME
            payload = _verified_payload(path, tier=tier, digest=digest)
            for item in _conjectures(payload, tier=tier, path=path):
                if item.slug in by_slug:
                    raise RetiredPoolError(
                        f"retired targets {by_slug[item.slug].reward_target_id!r} and "
                        f"{item.reward_target_id!r} both produce the slug {item.slug!r}"
                    )
                by_slug[item.slug] = item

        slug_by_task_id = {
            task_id: item.slug
            for item in by_slug.values()
            for task_id in item.task_ids
        }
        return cls(by_slug=by_slug, slug_by_task_id=slug_by_task_id)

    def get(self, slug: str) -> RetiredConjecture | None:
        return self.by_slug.get(slug)

    def all(self) -> tuple[RetiredConjecture, ...]:
        return tuple(sorted(self.by_slug.values(), key=lambda item: item.slug))


def _tier_policies(allowlist_path: Path) -> Mapping[str, Mapping[str, object]]:
    try:
        value = json.loads(allowlist_path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RetiredPoolError(f"cannot read {allowlist_path}: {exc}") from exc
    policies = value.get("tier_policies")
    if not isinstance(policies, dict):
        raise RetiredPoolError(f"{allowlist_path} publishes no tier policies")
    return {
        tier: policy
        for tier, policy in policies.items()
        if isinstance(policy, dict)
    }


def _verified_payload(path: Path, *, tier: str, digest: object) -> Mapping[str, object]:
    """Read the file and require its bytes to hash to what the tier policy published."""
    if not isinstance(digest, str):
        raise RetiredPoolError(f"tier {tier} publishes a non-string {DIGEST_FIELD}")
    if not path.is_file():
        raise RetiredPoolError(
            f"tier {tier} publishes {DIGEST_FIELD} but {path} is missing; the pinned task "
            "checkout is not the release this allowlist describes"
        )
    raw = path.read_bytes()
    if len(raw) > MAX_RETIRED_BYTES:
        raise RetiredPoolError(f"{path} is larger than {MAX_RETIRED_BYTES} bytes")
    actual = "sha256:" + hashlib.sha256(raw).hexdigest()
    if actual != digest:
        raise RetiredPoolError(
            f"{path} hashes to {actual} but tier {tier} published {digest}"
        )
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RetiredPoolError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RetiredPoolError(f"{path} is not a JSON object")
    if payload.get("schema_version") != RETIRED_SCHEMA_VERSION:
        raise RetiredPoolError(
            f"{path} has schema version {payload.get('schema_version')!r}, "
            f"expected {RETIRED_SCHEMA_VERSION}"
        )
    return payload


def _conjectures(
    payload: Mapping[str, object], *, tier: str, path: Path
) -> tuple[RetiredConjecture, ...]:
    rows = payload.get("retired")
    if not isinstance(rows, list):
        raise RetiredPoolError(f"{path} publishes no retired list")

    items: list[RetiredConjecture] = []
    for row in rows:
        if not isinstance(row, dict):
            raise RetiredPoolError(f"{path} contains a non-object retired entry")
        reward_target_id = row.get("reward_target_id")
        if not isinstance(reward_target_id, str) or not reward_target_id:
            raise RetiredPoolError(f"{path} contains an entry with no reward target id")

        raw_tasks = row.get("tasks")
        if not isinstance(raw_tasks, list) or not raw_tasks:
            raise RetiredPoolError(f"{reward_target_id} lists no retired tasks")

        # `problem_id` is published once per conjecture, so its member tasks have to agree on
        # it — the same rule `conjectures._grouped` applies to a live target.
        problem_ids = {task.get("problem_id") for task in raw_tasks}
        if len(problem_ids) != 1:
            raise RetiredPoolError(
                f"{reward_target_id} spans problem ids {sorted(map(str, problem_ids))}; "
                "a conjecture publishes one"
            )
        problem_id = problem_ids.pop()
        if not isinstance(problem_id, str) or not problem_id:
            raise RetiredPoolError(f"{reward_target_id} has no problem id")

        entry_tier = row.get("tier", tier)
        if entry_tier != tier:
            raise RetiredPoolError(
                f"{reward_target_id} is published for {entry_tier!r} but stored under {tier!r}"
            )

        try:
            source = CatalogDeclaration.from_dict(row["source"])
        except (KeyError, TypeError) as exc:
            raise RetiredPoolError(f"{reward_target_id} has no source declaration") from exc

        items.append(
            RetiredConjecture(
                slug=slug_for(reward_target_id),
                problem_id=problem_id,
                reward_target_id=reward_target_id,
                tier=tier,
                retired_on=str(row.get("retired_on", "")),
                reason_code=str(row.get("reason_code", "")),
                reason=str(row.get("reason", "")),
                decision_url=row.get("decision_url") or None,
                recovered_from_commit=str(row.get("recovered_from_commit", "")),
                source=source,
                tasks=tuple(
                    sorted(
                        (_task(task, reward_target_id) for task in raw_tasks),
                        key=_mode_order,
                    )
                ),
            )
        )
    return tuple(items)


def _mode_order(task: RetiredTask) -> tuple[int, str]:
    """Order tasks the way `conjectures._mode_order` orders a live conjecture's.

    The generator writes them sorted by mode name, which puts `counterexample` first. A reader
    should not see the two attack directions swap places depending on whether the target is still
    open, so the production order is restored here rather than relied on from the file.
    """
    if task.task_mode in PRODUCTION_TASK_MODES:
        return (PRODUCTION_TASK_MODES.index(task.task_mode), task.task_mode)
    return (len(PRODUCTION_TASK_MODES), task.task_mode)


def _task(value: object, reward_target_id: str) -> RetiredTask:
    if not isinstance(value, dict):
        raise RetiredPoolError(f"{reward_target_id} contains a non-object task")
    try:
        return RetiredTask(
            task_id=str(value["task_id"]),
            task_mode=str(value["task_mode"]),
            task_bundle_sha256=str(value["task_bundle_sha256"]),
            target_type_sha256=str(value["target_type_sha256"]),
            challenge_lean=str(value["challenge_lean"]),
        )
    except KeyError as exc:
        raise RetiredPoolError(f"{reward_target_id} has an incomplete task: {exc}") from exc


__all__ = [
    "DIGEST_FIELD",
    "RETIRED_FILE_NAME",
    "RETIRED_SCHEMA_VERSION",
    "RetiredConjecture",
    "RetiredIndex",
    "RetiredPoolError",
    "RetiredTask",
]
