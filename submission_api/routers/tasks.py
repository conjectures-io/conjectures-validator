"""Task discovery.

The task pool is public and its digests are published, so these reads need no
authentication. A miner uses them to learn the exact `task_id` and `task_bundle_sha256` to
commit to in a bundle.
"""

from __future__ import annotations

from verifier.models import TaskTrack

from typing import Annotated

from fastapi import APIRouter, Path, Query

from submission_api import schemas
from submission_api.dependencies import ServicesDep, SessionDep
from submission_api.errors import NotFound
from submission_api.taskpool import TaskEntry
from verifier.bundle import BUNDLE_FORMAT
from verifier.task_registry import TaskNotAllowed
from verifier.task_policy import review_policy_for_track

router = APIRouter(prefix="/v1/tasks", tags=["tasks"])


def _summary(entry: TaskEntry, open_review_policy: str) -> schemas.TaskSummary:
    return schemas.TaskSummary(
        submission_terms_url=f"/v1/catalog/submission-terms?track={entry.manifest.track}",
        track=entry.manifest.track,
        policy_version=entry.manifest.policy_version,
        review_policy_version=review_policy_for_track(entry.manifest.track, open_review_policy),
        resolution_reference=dict(entry.manifest.resolution_reference),
        task_id=entry.task_id,
        task_bundle_sha256=entry.task_bundle_sha256,
        target_type_sha256s=entry.target_type_sha256s,
    )


@router.get("", response_model=schemas.TaskList, summary="List submittable tasks")
async def list_tasks(
    services: ServicesDep, session: SessionDep,
    track: Annotated[TaskTrack | None, Query()] = None,
) -> schemas.TaskList:
    catalog = services.catalog
    settings = services.settings
    entries = tuple(entry for entry in catalog.summaries() if track is None or entry.manifest.track == track)
    snapshot = await services.pricing.quote_many(
        session,
        reward_target_ids=tuple(entry.reward_target_id for entry in entries),
    )
    await session.commit()
    return schemas.TaskList(
        repository_commit=catalog.repository_commit,
        bundle_format=BUNDLE_FORMAT,
        max_bundle_bytes=settings.max_bundle_bytes,
        submission_price_rao=settings.payment_amount_rao,
        payment_recipient=settings.payment_recipient,
        tasks=tuple(
            _summary(entry, settings.review_policy_version)
            for entry in entries
            if snapshot.quotes[entry.reward_target_id].available
        ),
    )


@router.get(
    "/{task_id}",
    response_model=schemas.TaskSummary,
    summary="Read one task's published commitment",
)
async def read_task(
    task_id: Annotated[str, Path(max_length=255)],
    services: ServicesDep,
    session: SessionDep,
) -> schemas.TaskSummary:
    try:
        entry = services.catalog.get(task_id)
    except TaskNotAllowed as exc:
        raise NotFound(str(exc)) from exc
    quote = await services.pricing.quote(
        session, reward_target_id=entry.reward_target_id
    )
    await session.commit()
    if not quote.available:
        raise NotFound("this bounty has already been solved", reason_code="BOUNTY_CLOSED")
    return _summary(entry, services.settings.review_policy_version)
