"""SQLAlchemy models — the runtime view of the validator schema.

These mirror ``deploy/migrate/sql/V001__initial_schema.sql``, which is the source
of truth: Flyway applies it in deployment, and nothing here is ever used to
create the production schema. A column, constraint or index changed in a
migration must be reflected here by hand — no tool diffs plain SQL against ORM
metadata, so the mirror is only as honest as the person editing it.

``Base.metadata.create_all()`` reproduces the schema faithfully enough for tests,
including the domains, enums, partial indexes, composite foreign keys and the
updated_at trigger. Confirm that with the schema-drift check rather than
assuming it: build one database from the migrations and one from this metadata,
then compare their catalogs.

No ``relationship()`` definitions on purpose. Several tables carry composite
foreign keys that repeat ``submission_id``, so a join path is only unambiguous
with an explicit ``primaryjoin``. Write the joins in the query instead, where
the intent is visible.
"""

from __future__ import annotations

import datetime as dt
import enum
import uuid

from sqlalchemy import (
    DDL,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    Text,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import DOMAIN, ENUM, INET, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# --- Domains ------------------------------------------------------------------
# Raw 32 bytes, never a 'sha256:<hex>' string, and a Bittensor SS58 address for
# prefix 42. Both reject bad input at the column, so nothing downstream has to
# re-check length or alphabet.

SHA256 = DOMAIN(
    "sha256",
    LargeBinary(),
    check="octet_length(VALUE) = 32",
    create_type=True,
)

SS58 = DOMAIN(
    "ss58",
    Text(),
    check=r"VALUE ~ '^[1-9A-HJ-NP-Za-km-z]{48}$'",
    create_type=True,
)


# --- Enums --------------------------------------------------------------------
# Python member values match the PostgreSQL labels exactly. The four submission
# statuses are independent axes, not one lifecycle: a submission has a
# verification state AND a review state AND a reward state at all times.


class TaskMode(enum.StrEnum):
    """What a task asks for. Mirrors verifier.task_policy.PRODUCTION_TASK_MODES."""

    FORMALIZED = "formalized"  # prove the conjecture
    COUNTEREXAMPLE = "counterexample"  # prove its negation


class VerificationState(enum.StrEnum):
    UNVERIFIED = "UNVERIFIED"
    REJECTED = "REJECTED"
    VERIFIED = "VERIFIED"


class ManualReviewState(enum.StrEnum):
    UNREVIEWED = "UNREVIEWED"
    REJECTED = "REJECTED"
    APPROVED = "APPROVED"


class RewardState(enum.StrEnum):
    INELIGIBLE = "INELIGIBLE"
    ELIGIBLE = "ELIGIBLE"
    REWARDED = "REWARDED"
    FAILED = "FAILED"


class PayoutState(enum.StrEnum):
    PENDING = (
        "PENDING"  # committed before the extrinsic is signed; unresolved, not failed
    )
    SUBMITTED = "SUBMITTED"  # broadcast, awaiting finality; safe to poll forever
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"


class ReviewOutcome(enum.StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ReviewerKind(enum.StrEnum):
    HUMAN = "HUMAN"  # the only kind that may reject a Lean-valid proof
    AUTOMATIC = (
        "AUTOMATIC"  # manual review was disabled; still a recorded policy decision
    )
    ADVISORY = "ADVISORY"  # an LLM pre-check; evidence, never binding


def _pg_enum(python_enum: type[enum.StrEnum], name: str) -> ENUM:
    """A native PostgreSQL enum storing the member values, not their names."""
    return ENUM(
        python_enum,
        name=name,
        create_type=True,
        values_callable=lambda e: [member.value for member in e],
    )


TASK_MODE = _pg_enum(TaskMode, "task_mode")
VERIFICATION_STATE = _pg_enum(VerificationState, "verification_state")
MANUAL_REVIEW_STATE = _pg_enum(ManualReviewState, "manual_review_state")
REWARD_STATE = _pg_enum(RewardState, "reward_state")
PAYOUT_STATE = _pg_enum(PayoutState, "payout_state")
REVIEW_OUTCOME = _pg_enum(ReviewOutcome, "review_outcome")
REVIEWER_KIND = _pg_enum(ReviewerKind, "reviewer_kind")


class Proof(Base):
    """Proof bytes, content-addressed.

    Separate from submissions so it can be made write-once: REVOKE UPDATE, DELETE
    from the service role and the bytes we verified physically cannot be rewritten.
    """

    __tablename__ = "proofs"

    digest: Mapped[bytes] = mapped_column(SHA256, primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    byte_length: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # pg_catalog-qualified: bare sha256(...) could read as a cast to the domain.
        CheckConstraint(
            "digest = pg_catalog.sha256(content)", name="proof_digest_matches"
        ),
        CheckConstraint(
            "byte_length = octet_length(content)", name="proof_length_matches"
        ),
    )


class Submission(Base):
    """One paid submission. Holds current state; history lives in the event tables."""

    __tablename__ = "submissions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    hotkey: Mapped[str] = mapped_column(SS58, nullable=False)
    idempotency_key: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    request_digest: Mapped[bytes] = mapped_column(SHA256, nullable=False)
    # No FK on task_id: the task repo is the source of truth.
    task_id: Mapped[str] = mapped_column(Text, nullable=False)
    task_bundle_sha256: Mapped[bytes] = mapped_column(SHA256, nullable=False)
    # Both derived from the allowlist at intake, never sent by the miner.
    problem_id: Mapped[str] = mapped_column(Text, nullable=False)
    task_mode: Mapped[TaskMode] = mapped_column(TASK_MODE, nullable=False)

    # Drop the UNIQUE if identical proofs should ever both be payable.
    proof_digest: Mapped[bytes] = mapped_column(
        SHA256, ForeignKey("proofs.digest"), nullable=False, unique=True
    )

    payment_reference: Mapped[str] = mapped_column(Text, nullable=False)
    payment_sender: Mapped[str] = mapped_column(SS58, nullable=False)
    payment_amount_rao: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payment_block: Mapped[int] = mapped_column(BigInteger, nullable=False)
    hotkey_signature: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    verification_status: Mapped[VerificationState] = mapped_column(
        VERIFICATION_STATE,
        nullable=False,
        server_default=VerificationState.UNVERIFIED.value,
    )
    manual_review_status: Mapped[ManualReviewState] = mapped_column(
        MANUAL_REVIEW_STATE,
        nullable=False,
        server_default=ManualReviewState.UNREVIEWED.value,
    )
    reward_status: Mapped[RewardState] = mapped_column(
        REWARD_STATE, nullable=False, server_default=RewardState.INELIGIBLE.value
    )
    failure_reason: Mapped[str | None] = mapped_column(Text)

    manual_review_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    review_policy_version: Mapped[str] = mapped_column(Text, nullable=False)

    bounty_amount_rao: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bounty_policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    bounty_inputs: Mapped[dict | None] = mapped_column(JSONB)

    verification_lease_until: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    verification_lease_owner: Mapped[str | None] = mapped_column(Text)
    verification_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("length(task_id) BETWEEN 1 AND 255", name="task_id_nonempty"),
        CheckConstraint(
            "length(problem_id) BETWEEN 1 AND 255", name="problem_id_nonempty"
        ),
        CheckConstraint(
            "length(payment_reference) BETWEEN 1 AND 128",
            name="payment_reference_nonempty",
        ),
        CheckConstraint("payment_amount_rao > 0", name="payment_amount_positive"),
        CheckConstraint("payment_block > 0", name="payment_block_positive"),
        CheckConstraint(
            "octet_length(hotkey_signature) = 64", name="hotkey_signature_len"
        ),
        CheckConstraint(
            "length(review_policy_version) BETWEEN 1 AND 64",
            name="review_policy_version_nonempty",
        ),
        CheckConstraint("bounty_amount_rao > 0", name="bounty_amount_positive"),
        CheckConstraint(
            "length(bounty_policy_version) BETWEEN 1 AND 64",
            name="bounty_policy_version_nonempty",
        ),
        CheckConstraint(
            "verification_attempts >= 0", name="verification_attempts_nonneg"
        ),
        # Both halves or neither: a lease with no owner cannot be traced to a process,
        # and an owner with no expiry never releases.
        CheckConstraint(
            "(verification_lease_until IS NULL) = (verification_lease_owner IS NULL)",
            name="verification_lease_paired",
        ),
        CheckConstraint(
            "verification_lease_owner IS NULL "
            "OR length(verification_lease_owner) BETWEEN 1 AND 128",
            name="verification_lease_owner_len",
        ),
        UniqueConstraint(
            "hotkey", "idempotency_key", name="submissions_idempotency_unique"
        ),
        UniqueConstraint(
            "payment_reference", name="submissions_payment_reference_unique"
        ),
        CheckConstraint("updated_at >= created_at", name="updated_not_before_created"),
        # Worker queues, oldest first, for FOR UPDATE SKIP LOCKED. Partial, so only
        # rows still awaiting work are indexed. The lease is not in the verification
        # predicate: now() is not immutable, so expiry is filtered, not indexed.
        Index(
            "submissions_verification_queue_idx",
            "created_at",
            postgresql_where=text("verification_status = 'UNVERIFIED'"),
        ),
        Index(
            "submissions_review_queue_idx",
            "created_at",
            postgresql_where=text(
                "verification_status = 'VERIFIED' AND manual_review_status = 'UNREVIEWED'"
            ),
        ),
        Index(
            "submissions_reward_queue_idx",
            "created_at",
            postgresql_where=text("reward_status = 'ELIGIBLE'"),
        ),
        Index("submissions_task_idx", "task_id", text("created_at DESC")),
        Index("submissions_hotkey_idx", "hotkey", text("created_at DESC")),
        # No proof_digest index: the UNIQUE above already builds one.
        Index(
            "submissions_problem_reward_unique",
            "problem_id",
            unique=True,
            postgresql_where=text("reward_status <> 'INELIGIBLE'"),
        ),
        Index(
            "submissions_problem_verified_idx",
            "problem_id",
            "task_mode",
            postgresql_where=text("verification_status = 'VERIFIED'"),
        ),
    )


# The service must not be trusted to maintain updated_at, because a raw UPDATE
# that forgets it would leave the row looking untouched.
event.listen(
    Submission.__table__,
    "after_create",
    # Body indentation is deliberately flush left: pg_get_functiondef() returns
    # the source verbatim, so indenting it here would make this function differ
    # textually from the migration's for any schema-drift comparison.
    DDL(
        "CREATE FUNCTION submissions_touch_updated_at() RETURNS TRIGGER AS $$\n"
        "BEGIN\n"
        "    NEW.updated_at := now();\n"
        "    RETURN NEW;\n"
        "END;\n"
        "$$ LANGUAGE plpgsql;\n"
        "\n"
        "CREATE TRIGGER submissions_touch_updated_at\n"
        "    BEFORE UPDATE ON submissions\n"
        "    FOR EACH ROW EXECUTE FUNCTION submissions_touch_updated_at();"
    ),
)
event.listen(
    Submission.__table__,
    "before_drop",
    DDL("DROP FUNCTION IF EXISTS submissions_touch_updated_at() CASCADE;"),
)


class VerificationRun(Base):
    """One completed verifier run, inserted once when the run finishes.

    Several runs per submission are normal (infra retry, re-verification after a
    pin bump), so this keeps what Submission.verification_status only summarises.
    """

    __tablename__ = "verification_runs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    submission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("submissions.id"), nullable=False
    )

    # Re-derived from live Lean, not trusted from disk.
    task_bundle_sha256: Mapped[bytes] = mapped_column(SHA256, nullable=False)
    proof_digest: Mapped[bytes] = mapped_column(
        SHA256, ForeignKey("proofs.digest"), nullable=False
    )

    verifier_version: Mapped[str] = mapped_column(Text, nullable=False)
    container_digest: Mapped[bytes] = mapped_column(SHA256, nullable=False)
    # 'landrun' in production; anything else means no real isolation.
    sandbox_mode: Mapped[str] = mapped_column(Text, nullable=False)

    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason_code: Mapped[str] = mapped_column(Text, nullable=False)
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    # The gate booleans; a projection of report, not the authority.
    checks: Mapped[dict | None] = mapped_column(JSONB)
    # Exact report bytes, so the digest stays recomputable; NULL if the run died first.
    report: Mapped[bytes | None] = mapped_column(LargeBinary)
    report_digest: Mapped[bytes | None] = mapped_column(SHA256)

    # Duration is finished_at - started_at. Not stored, so the two cannot disagree.
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "(report IS NULL) = (report_digest IS NULL)", name="runs_report_paired"
        ),
        CheckConstraint(
            "report IS NULL OR report_digest = pg_catalog.sha256(report)",
            name="runs_report_digest_matches",
        ),
        CheckConstraint(
            "finished_at >= started_at", name="runs_finished_after_started"
        ),
        # One submission's attempts, in order. UNIQUE is free — id is already the
        # primary key — and leaves the pair available as a composite foreign-key
        # target.
        Index("verification_runs_submission_idx", "submission_id", "id", unique=True),
        Index("verification_runs_reason_idx", "reason_code", text("finished_at DESC")),
    )


class RewardEvent(Base):
    """One payout attempt, paid as a direct alpha transfer.

    Weights go to a fixed treasury uid that funds these, so no per-submission
    weight exists and nothing here is scored. The amount is not decided here: it was
    quoted and frozen on the submission at intake.

    The row is inserted as PENDING and committed BEFORE the extrinsic is signed,
    then its chain fields fill in as it progresses. Inserting after the transfer
    instead would lose the payout on a crash. A PENDING row with no reference is
    unresolved, not failed: it must block further payouts for that submission
    until a human reconciles it against the chain.

    Several rows per submission are permitted on purpose. The CLI checks for an
    existing payout before paying, but if it ever pays twice the second transfer
    is real and must be recordable; a rejected INSERT would only hide it.
    Duplicates are found by query, not prevented.
    """

    __tablename__ = "reward_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    submission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("submissions.id"), nullable=False
    )

    eligibility_reason: Mapped[str] = mapped_column(Text, nullable=False)

    amount_rao: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Captured, not derived from submissions: this is where the money actually
    # went, an external fact. Alpha is held as stake, so a transfer needs both keys.
    destination_coldkey: Mapped[str] = mapped_column(SS58, nullable=False)
    destination_hotkey: Mapped[str] = mapped_column(SS58, nullable=False)

    status: Mapped[PayoutState] = mapped_column(
        PAYOUT_STATE, nullable=False, server_default=PayoutState.PENDING.value
    )
    extrinsic_reference: Mapped[str | None] = mapped_column(Text)
    submitted_block: Mapped[int | None] = mapped_column(BigInteger)
    finalized_block: Mapped[int | None] = mapped_column(BigInteger)
    failure_reason: Mapped[str | None] = mapped_column(Text)

    initiated_by: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    submitted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("amount_rao > 0", name="reward_amount_positive"),
        # PENDING means exactly "no extrinsic exists yet", so status and reference
        # must not drift apart. FAILED is exempt: an attempt can die before broadcast.
        CheckConstraint(
            "status NOT IN ('SUBMITTED', 'CONFIRMED') "
            "OR (extrinsic_reference IS NOT NULL AND submitted_at IS NOT NULL)",
            name="reward_submitted_needs_reference",
        ),
        CheckConstraint(
            "status <> 'CONFIRMED' OR (finalized_block IS NOT NULL AND confirmed_at IS NOT NULL)",
            name="reward_confirmed_needs_finality",
        ),
        CheckConstraint(
            "status <> 'FAILED' OR failure_reason IS NOT NULL",
            name="reward_failed_needs_reason",
        ),
        CheckConstraint(
            "(submitted_block IS NULL OR submitted_block > 0) "
            "AND (finalized_block IS NULL OR finalized_block > 0)",
            name="reward_blocks_positive",
        ),
        CheckConstraint(
            "finalized_block IS NULL OR submitted_block IS NULL "
            "OR finalized_block >= submitted_block",
            name="reward_finalized_not_before_submitted",
        ),
        CheckConstraint(
            "submitted_at IS NULL OR submitted_at >= created_at",
            name="reward_submitted_not_before_created",
        ),
        CheckConstraint(
            "confirmed_at IS NULL OR (submitted_at IS NOT NULL AND confirmed_at >= submitted_at)",
            name="reward_confirmed_after_submitted",
        ),
        # Not the dedup key: one extrinsic cannot be two payouts, so this catches
        # recording the same transfer twice, while a genuine second transfer has
        # its own reference and is allowed. Partial, so unpaid rows don't collide
        # on NULL.
        Index(
            "reward_events_extrinsic_idx",
            "extrinsic_reference",
            unique=True,
            postgresql_where=text("extrinsic_reference IS NOT NULL"),
        ),
        # UNIQUE is free and makes the pair a foreign-key target.
        Index("reward_events_submission_idx", "submission_id", "id", unique=True),
        Index(
            "reward_events_pending_idx",
            "created_at",
            postgresql_where=text("status IN ('PENDING', 'SUBMITTED')"),
        ),
        Index(
            "reward_events_destination_idx",
            "destination_coldkey",
            text("created_at DESC"),
        ),
    )


class ReviewDecision(Base):
    """Append-only review history.

    A correction is a new row pointing at what it supersedes, never an UPDATE.
    Submission.manual_review_status summarises the latest binding row here. A
    review may reject a Lean-valid proof but can never make a Lean-invalid one
    valid, so nothing in this table may contradict verification.
    """

    __tablename__ = "review_decisions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    submission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("submissions.id"), nullable=False
    )

    decision: Mapped[ReviewOutcome] = mapped_column(REVIEW_OUTCOME, nullable=False)
    kind: Mapped[ReviewerKind] = mapped_column(REVIEWER_KIND, nullable=False)
    # Operator identity, or the model id for ADVISORY.
    reviewer: Mapped[str] = mapped_column(Text, nullable=False)

    policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    # Structured, because a rejection is shown to the miner.
    reason_code: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    # ADVISORY output kept as-is: model, verdict, and what it was asked.
    evidence: Mapped[dict | None] = mapped_column(JSONB)

    # Corrections chain instead of overwriting. The FK is composite, so a
    # decision can only supersede another decision on the SAME submission.
    supersedes_id: Mapped[int | None] = mapped_column(BigInteger)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("length(reviewer) BETWEEN 1 AND 255", name="reviewer_nonempty"),
        # Free (id is already the primary key). Needed as a foreign-key target by
        # the self-reference below, and it doubles as the per-submission history
        # index — btree scans backwards, so latest-first needs no DESC index.
        UniqueConstraint(
            "submission_id", "id", name="review_decisions_submission_unique"
        ),
        CheckConstraint(
            "supersedes_id IS DISTINCT FROM id", name="review_supersedes_not_self"
        ),
        ForeignKeyConstraint(
            ["submission_id", "supersedes_id"],
            ["review_decisions.submission_id", "review_decisions.id"],
            name="review_supersedes_same_submission",
        ),
        Index("review_decisions_reviewer_idx", "reviewer", text("created_at DESC")),
        Index("review_decisions_reason_idx", "reason_code", text("created_at DESC")),
    )

class ApiRejectionLog(Base):
    """Telemetry, not source of truth.

    Intake is payment-gated, so a refused request creates no submission and would
    otherwise leave no trace at all. This is the only place a miner who paid and
    got rejected is recorded, and the only view of abuse patterns.

    No foreign keys and nothing references it, so it can be truncated or aged out
    freely. It will outgrow every other table here; give it a retention window
    before launch.

    Deliberately NO sha256/ss58 domains and no UUID columns: every field below is
    unvalidated client input, and a domain CHECK would refuse the row precisely
    when the input is malformed, which is the case we most need logged.
    """

    __tablename__ = "api_rejection_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    occurred_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    reason_code: Mapped[str] = mapped_column(Text, nullable=False)
    http_status: Mapped[int | None] = mapped_column(SmallInteger)

    # Claimed, never verified. If we had verified it, this would be a submission.
    hotkey_claimed: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    task_id: Mapped[str | None] = mapped_column(Text)
    task_bundle_sha256: Mapped[str | None] = mapped_column(Text)
    proof_digest: Mapped[str | None] = mapped_column(Text)
    # Size only; rejected proof bytes are not kept.
    proof_byte_length: Mapped[int | None] = mapped_column(Integer)
    request_digest: Mapped[str | None] = mapped_column(Text)
    payment_reference: Mapped[str | None] = mapped_column(Text)

    # Personal data, so the retention window is not optional.
    source_ip: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("api_rejection_log_recent_idx", text("occurred_at DESC")),
        Index("api_rejection_log_reason_idx", "reason_code", text("occurred_at DESC")),
        Index(
            "api_rejection_log_hotkey_idx",
            "hotkey_claimed",
            text("occurred_at DESC"),
            postgresql_where=text("hotkey_claimed IS NOT NULL"),
        ),
        Index(
            "api_rejection_log_payment_idx",
            "payment_reference",
            postgresql_where=text("payment_reference IS NOT NULL"),
        ),
    )


__all__ = [
    "ApiRejectionLog",
    "Base",
    "ManualReviewState",
    "PayoutState",
    "Proof",
    "ReviewDecision",
    "ReviewOutcome",
    "ReviewerKind",
    "RewardEvent",
    "RewardState",
    "Submission",
    "TaskMode",
    "VerificationRun",
    "VerificationState",
]
