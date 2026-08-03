"""Operational status for the public website.

Distinct from `/healthz` and `/readyz`, which exist for the orchestrator and answer "should this
replica receive traffic". This answers "can I submit right now, and if not, why" — the question a
solver has before spending a credit — so it is on `/v1`, rate-limited and CORS-scoped like the
rest of the public surface.

`submissions_open` is authoritative rather than descriptive: `SUBMISSIONS_PAUSED` is read by
`POST /v1/submissions` too, which refuses with `503 SUBMISSIONS_PAUSED` while it is set. A status
endpoint that reported a pause the intake path did not honour would be worse than no status
endpoint, because a solver would trust it.

The pin rotation window is the weekly drain-and-rotate from README.md: operators pause new
submissions, wait for every accepted submission to reach a terminal state, then update the pin set
atomically. It is configured, not inferred — only the operator knows when they take the system
down — but `drained` is computed, because whether the window may actually start is a fact about
the queues.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Response

from conjectures_subnet.db import public as public_store
from submission_api import schemas_public as public
from submission_api.dependencies import ServicesDep, SessionDep
from submission_api.settings import Settings

router = APIRouter(prefix="/v1/system", tags=["system"])

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_PAUSED = "paused"

DAYS_PER_WEEK = 7


@router.get(
    "/status",
    response_model=public.SystemStatus,
    summary="Whether submissions are open, the queues, and the next pin rotation",
)
async def read_status(
    response: Response, services: ServicesDep, session: SessionDep
) -> public.SystemStatus:
    settings: Settings = services.settings
    depths = await public_store.queue_depths(session)
    window = pin_rotation_window(settings, datetime.now(UTC), drained=depths.drained)

    # Never cached. This is the endpoint a client polls to find out whether the state it was
    # told about is still true, and a shared cache serving a minute-old "submissions open" is
    # exactly the failure it exists to prevent.
    response.headers["Cache-Control"] = "no-store"
    return public.SystemStatus(
        status=_status(settings, window),
        submissions_open=not settings.submissions_paused,
        repository_commit=services.catalog.repository_commit,
        queue_depths=public.QueueDepths(
            awaiting_verification=depths.awaiting_verification,
            awaiting_review=depths.awaiting_review,
            awaiting_reward=depths.awaiting_reward,
        ),
        pin_rotation=window,
        banner=settings.status_banner or None,
    )


def _status(settings: Settings, window: public.PinRotationWindow) -> str:
    if settings.submissions_paused:
        return STATUS_PAUSED
    if window.in_progress:
        # The documented policy is that submissions are paused for the duration of a rotation.
        # Open submissions inside the window means the two disagree, which an operator should
        # see rather than have smoothed over into `ok`.
        return STATUS_DEGRADED
    return STATUS_OK


def pin_rotation_window(
    settings: Settings, now: datetime, *, drained: bool
) -> public.PinRotationWindow:
    """The rotation window in progress, or the next one.

    Returns the current window while one is running — so `in_progress` is meaningful and
    `ends_at` says when it should be over — and rolls forward to next week once it has passed.
    Pure and takes `now` as an argument so the boundaries are testable without freezing a clock.
    """
    moment = now.astimezone(UTC)
    duration = timedelta(minutes=settings.pin_rotation_minutes)

    # Midnight UTC on the most recent configured weekday, at or before today.
    days_since = (moment.weekday() - settings.pin_rotation_weekday) % DAYS_PER_WEEK
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    starts_at = midnight - timedelta(days=days_since) + timedelta(
        minutes=settings.pin_rotation_start_minute
    )
    ends_at = starts_at + duration
    if moment >= ends_at:
        # This week's window is over; the next one is a week on. Recomputed rather than
        # incremented so a duration longer than a week cannot produce a window in the past.
        starts_at += timedelta(days=DAYS_PER_WEEK)
        ends_at = starts_at + duration

    return public.PinRotationWindow(
        weekday=settings.pin_rotation_weekday,
        starts_at=starts_at,
        ends_at=ends_at,
        in_progress=starts_at <= moment < ends_at,
        drained=drained,
    )


__all__ = ["STATUS_DEGRADED", "STATUS_OK", "STATUS_PAUSED", "pin_rotation_window", "router"]
