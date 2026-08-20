"""Response models.

The four submission statuses are independent axes, not one lifecycle, exactly as the schema
stores them: a submission always has a verification status AND a review status AND a reward
status. Collapsing them into one field would imply that payment acceptance, Lean validity,
manual approval and reward issuance are the same event, which docs/SUBNET.md forbids.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PaymentRecord(Model):
    """The confirmed transfer that funded this submission.

    Present unconditionally: intake is payment-gated, so a submission exists only for a
    transfer already confirmed on finalized chain state.
    """

    reference: str
    sender: str
    amount_rao: int = Field(description="Confirmed transfer in the chain's base unit")
    block: int = Field(description="Finalized block the transfer was observed in")


class BountyQuote(Model):
    """The submission's bounty quote and whether its pricing contract fixed it."""

    amount_rao: int | None = Field(
        description=(
            "Subnet Alpha amount; locked for V012+ submissions, live for grandfathered rows."
        )
    )
    amount_usd: str | None = Field(
        description=(
            "Current USD display value from TaoStats; null with amount_rao or when the "
            "external rate is unavailable."
        )
    )
    policy_version: str
    available: bool
    reason: str
    as_of: datetime
    locked: bool = Field(
        description="True when submission acceptance fixed the amount; false for legacy rows."
    )


class VerificationStatus(Model):
    status: str
    attempt: int | None = None
    accepted: bool | None = None
    reason_code: str | None = None
    stage: str | None = None
    sandbox_mode: str | None = None
    report_available: bool = False
    started_at: datetime | None = None
    finished_at: datetime | None = None


class PublicCredit(Model):
    """Opt-in authorship signed by the submitting hotkey and published with a result."""

    name: str
    url: str | None = None
    orcid: str | None = None


class SubmissionStatus(Model):
    submission_id: uuid.UUID
    hotkey: str
    public_credit: PublicCredit | None = None
    task_id: str
    task_bundle_sha256: str
    proof_sha256: str
    request_digest: str

    verification_status: str
    manual_review_status: str
    reward_status: str
    failure_reason: str | None = None

    manual_review_required: bool
    review_policy_version: str

    payment: PaymentRecord
    bounty: BountyQuote
    verification: VerificationStatus | None = None

    created_at: datetime
    updated_at: datetime


class TaskSummary(Model):
    task_id: str
    task_bundle_sha256: str
    target_type_sha256s: tuple[str, ...]


class TaskList(Model):
    repository_commit: str
    bundle_format: str
    max_bundle_bytes: int
    submission_price_rao: int
    payment_recipient: str
    tasks: tuple[TaskSummary, ...]


class VerificationReportResponse(Model):
    submission_id: uuid.UUID
    report_sha256: str
    report: dict[str, Any]


class Health(Model):
    status: str
    version: str
    app_mode: str


class Readiness(Model):
    status: str
    database: bool
    task_pool: bool
    tasks: int
