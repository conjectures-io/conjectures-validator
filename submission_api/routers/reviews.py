"""Audited operator review endpoint.

This endpoint is deliberately separate from miner authentication. Reviewers use
a deployment secret and their configured operator identity is written into the
append-only decision and event rows. A review cannot override a failed Lean run.
"""

from __future__ import annotations

import hmac
import uuid

from fastapi import APIRouter, Depends, Header, Path
from sqlalchemy.ext.asyncio import AsyncSession

from conjectures_subnet.db import submissions as store
from conjectures_subnet.db.models import ReviewOutcome
from submission_api import schemas
from submission_api.dependencies import get_services, get_session
from submission_api.errors import Forbidden, ServiceUnavailable, Unauthorized
from submission_api.routers.submissions import _status


router = APIRouter(prefix="/v1/reviews", tags=["operator-review"])


def _authorize(authorization: str | None, services) -> None:  # type: ignore[no-untyped-def]
    expected = services.settings.review_api_token
    if not expected:
        raise ServiceUnavailable(
            "manual review endpoint is not configured",
            reason_code="REVIEW_NOT_CONFIGURED",
        )
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not supplied:
        raise Unauthorized("review bearer token is required", reason_code="REVIEW_UNAUTHORIZED")
    if not hmac.compare_digest(supplied, expected):
        raise Forbidden("review bearer token is invalid", reason_code="REVIEW_FORBIDDEN")


@router.post(
    "/{submission_id}",
    response_model=schemas.SubmissionStatus,
    summary="Record one binding human review decision",
)
async def record_review(
    body: schemas.ReviewRequest,
    submission_id: uuid.UUID = Path(),
    authorization: str | None = Header(default=None, alias="Authorization"),
    services=Depends(get_services),  # type: ignore[no-untyped-def]
    session: AsyncSession = Depends(get_session),
) -> schemas.SubmissionStatus:
    _authorize(authorization, services)
    view = await store.record_human_review(
        session,
        submission_id,
        decision=ReviewOutcome(body.decision),
        reviewer=services.settings.reviewer_identity,
        reason_code=body.reason_code,
        notes=body.notes,
    )
    await session.commit()
    return _status(view)
