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
from sqlalchemy.dialects.postgresql import ARRAY, DOMAIN, ENUM, INET, JSONB, UUID
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
    reward_target_id: Mapped[str] = mapped_column(Text, nullable=False)
    task_mode: Mapped[TaskMode] = mapped_column(TASK_MODE, nullable=False)

    # Drop the UNIQUE if identical proofs should ever both be payable.
    proof_digest: Mapped[bytes] = mapped_column(
        SHA256, ForeignKey("proofs.digest"), nullable=False, unique=True
    )

    # NULLable since V003: a submission has exactly one funding source, and a credit-funded one
    # is named by `credit_ledger_id` instead. See submission_funded_exactly_once below.
    payment_reference: Mapped[str | None] = mapped_column(Text)
    payment_sender: Mapped[str | None] = mapped_column(SS58)
    payment_amount_rao: Mapped[int | None] = mapped_column(BigInteger)
    payment_block: Mapped[int | None] = mapped_column(BigInteger)
    hotkey_signature: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    # V003. The account that owns this submission, and the two rows behind the credit path.
    # All three are NULL on the extrinsic-funded path, which authenticates a hotkey and need
    # not involve an account at all.
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id")
    )
    # `use_alter=True`: submissions, credit_ledger and submission_intents reference one
    # another, so these two edges are emitted as ALTER TABLE ADD CONSTRAINT after all three
    # tables exist — exactly what V003 does. Without it SQLAlchemy cannot order the CREATEs.
    credit_ledger_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "credit_ledger.id",
            name="submissions_credit_ledger_id_fkey",
            use_alter=True,
        ),
        unique=True,
    )
    intent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "submission_intents.id",
            name="submissions_intent_id_fkey",
            use_alter=True,
        ),
        unique=True,
    )

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
            "length(reward_target_id) BETWEEN 1 AND 255",
            name="reward_target_id_nonempty",
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
        # Public result feeds, newest first, from V002. Separate from the worker queues above:
        # those are ascending and lack `id`, which the keyset page predicate compares as part
        # of a row value because `created_at` is not unique.
        Index(
            "submissions_certified_feed_idx",
            text("created_at DESC"),
            text("id DESC"),
            postgresql_where=text("reward_status = 'REWARDED'"),
        ),
        Index(
            "submissions_in_review_feed_idx",
            text("created_at DESC"),
            text("id DESC"),
            postgresql_where=text(
                "verification_status = 'VERIFIED' AND manual_review_status = 'UNREVIEWED'"
            ),
        ),
        # V003: exactly one funding source. Neither path admits an unfunded submission; they
        # differ only in what names the money — an extrinsic, or a credit ledger entry.
        CheckConstraint(
            "(payment_reference IS NOT NULL)::int + (credit_ledger_id IS NOT NULL)::int = 1",
            name="submission_funded_exactly_once",
        ),
        CheckConstraint(
            "payment_reference IS NULL "
            "OR (payment_sender IS NOT NULL "
            "AND payment_amount_rao IS NOT NULL "
            "AND payment_block IS NOT NULL)",
            name="submission_payment_is_complete",
        ),
        CheckConstraint(
            "credit_ledger_id IS NULL "
            "OR (account_id IS NOT NULL AND intent_id IS NOT NULL)",
            name="submission_credit_path_is_complete",
        ),
        Index(
            "submissions_account_idx",
            "account_id",
            text("created_at DESC"),
            postgresql_where=text("account_id IS NOT NULL"),
        ),
        Index(
            "submissions_reward_target_reward_unique",
            "reward_target_id",
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


# --- V003: accounts, credits, intents -----------------------------------------
# The account layer the website needs, and with it a second way to fund a
# submission. See deploy/migrate/sql/V003__accounts_credits_intents.sql for why
# the funding invariant on Submission above changed.

MINER_ROLE = "MINER"
REVIEWER_ROLE = "REVIEWER"
ADMIN_ROLE = "ADMIN"
ACCOUNT_ROLES = (MINER_ROLE, REVIEWER_ROLE, ADMIN_ROLE)


class LoginChallengeKind(enum.StrEnum):
    EMAIL = "EMAIL"  # a magic link token
    WALLET = "WALLET"  # a coldkey sign-in nonce
    HOTKEY_LINK = "HOTKEY_LINK"  # attaching a hotkey to an existing account


class CreditEntryKind(enum.StrEnum):
    DEPOSIT = "DEPOSIT"
    SPEND = "SPEND"
    REFUND = "REFUND"
    ADJUSTMENT = "ADJUSTMENT"
    BONUS = "BONUS"


class DepositState(enum.StrEnum):
    AWAITING_TRANSFER = "AWAITING_TRANSFER"
    # The transfer is visible but not final, so no credits are issued yet.
    SEEN_UNFINALIZED = "SEEN_UNFINALIZED"
    CREDITED = "CREDITED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


class IntentState(enum.StrEnum):
    OPEN = "OPEN"  # credit held, no bundle yet
    BUNDLE_ATTACHED = "BUNDLE_ATTACHED"  # admitted, awaiting a signature
    CONFIRMED = "CONFIRMED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


LOGIN_CHALLENGE_KIND = _pg_enum(LoginChallengeKind, "login_challenge_kind")
CREDIT_ENTRY_KIND = _pg_enum(CreditEntryKind, "credit_entry_kind")
DEPOSIT_STATE = _pg_enum(DepositState, "deposit_state")
INTENT_STATE = _pg_enum(IntentState, "intent_state")

EMAIL_SHAPE = r"^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$"


class Account(Base):
    """One website account. May be reached by email, by wallet, or by both."""

    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    # NULL for a wallet-only account. Uniqueness is case-insensitive, via the
    # functional index below: two addresses differing only in case are the same
    # mailbox, and one must not be able to claim the other's account.
    email: Mapped[str | None] = mapped_column(Text)
    email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    display_name: Mapped[str | None] = mapped_column(Text)
    roles: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("ARRAY['MINER']::TEXT[]")
    )
    # Alpha is held as stake, so a payout needs both keys. Set together or not at all.
    payout_coldkey: Mapped[str | None] = mapped_column(SS58)
    payout_hotkey: Mapped[str | None] = mapped_column(SS58)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            f"email IS NULL OR email ~ '{EMAIL_SHAPE}'", name="account_email_shape"
        ),
        CheckConstraint(
            "display_name IS NULL OR length(display_name) BETWEEN 1 AND 64",
            name="display_name_length",
        ),
        CheckConstraint(
            "roles <@ ARRAY['MINER', 'REVIEWER', 'ADMIN']::TEXT[]",
            name="account_roles_are_known",
        ),
        CheckConstraint(
            "(payout_coldkey IS NULL) = (payout_hotkey IS NULL)",
            name="payout_is_a_complete_pair",
        ),
        CheckConstraint(
            "updated_at >= created_at", name="accounts_updated_not_before_created"
        ),
        Index(
            "accounts_email_idx",
            text("lower(email)"),
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
        ),
    )


event.listen(
    Account.__table__,
    "after_create",
    DDL(
        "CREATE FUNCTION accounts_touch_updated_at() RETURNS TRIGGER AS $$\n"
        "BEGIN\n"
        "    NEW.updated_at := now();\n"
        "    RETURN NEW;\n"
        "END;\n"
        "$$ LANGUAGE plpgsql;\n"
        "\n"
        "CREATE TRIGGER accounts_touch_updated_at\n"
        "    BEFORE UPDATE ON accounts\n"
        "    FOR EACH ROW EXECUTE FUNCTION accounts_touch_updated_at();"
    ),
)
event.listen(
    Account.__table__,
    "before_drop",
    DDL("DROP FUNCTION IF EXISTS accounts_touch_updated_at() CASCADE;"),
)


class AccountWallet(Base):
    """A coldkey the account signs in with.

    Separate from LinkedHotkey because the two answer different questions: this is
    "who is logging in", that is "which miner identity may submit".
    """

    __tablename__ = "account_wallets"

    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    coldkey: Mapped[str] = mapped_column(SS58, primary_key=True)
    signature: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    linked_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "octet_length(signature) = 64", name="wallet_signature_len"
        ),
        Index("account_wallets_account_idx", "account_id"),
    )


class LinkedHotkey(Base):
    """A hotkey the account may submit under, and how a deposit is attributed.

    Globally unique: two accounts claiming one hotkey would make submission
    attribution ambiguous and leave a reward with no single owner.
    """

    __tablename__ = "linked_hotkeys"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    hotkey: Mapped[str] = mapped_column(SS58, nullable=False, unique=True)
    signature: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    linked_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "octet_length(signature) = 64", name="hotkey_link_signature_len"
        ),
        Index("linked_hotkeys_account_idx", "account_id", text("linked_at DESC")),
    )


class AccountSession(Base):
    """One signed-in browser session.

    Only the SHA-256 of the token is stored, never the token. A database read — a
    dump, a backup, a replica, an over-broad SELECT — must not yield anything
    replayable as a credential, exactly as for a password.
    """

    __tablename__ = "account_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_sha256: Mapped[bytes] = mapped_column(SHA256, nullable=False, unique=True)
    # Bound to the session rather than a bare double-submit cookie, so a value the
    # client can set is not itself the proof.
    csrf_sha256: Mapped[bytes] = mapped_column(SHA256, nullable=False)

    issued_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Rolling: extended on use, so an active session does not expire under someone
    # and an abandoned one does.
    expires_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    user_agent: Mapped[str | None] = mapped_column(Text)
    source_ip: Mapped[str | None] = mapped_column(INET)

    __table_args__ = (
        CheckConstraint(
            "expires_at > issued_at", name="session_expires_after_issue"
        ),
        Index(
            "account_sessions_live_idx",
            "expires_at",
            postgresql_where=text("revoked_at IS NULL"),
        ),
        Index("account_sessions_account_idx", "account_id", text("issued_at DESC")),
    )


class LoginChallenge(Base):
    """A single-use, short-lived secret for one of the three login flows.

    One table for all three because the rules are identical: single use, short
    lived, rate limited, and the secret stored only as a digest.
    """

    __tablename__ = "login_challenges"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    kind: Mapped[LoginChallengeKind] = mapped_column(
        LOGIN_CHALLENGE_KIND, nullable=False
    )

    # Set for HOTKEY_LINK, which attaches to a known account. NULL for the two
    # sign-in kinds, where the account may not exist yet.
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id", ondelete="CASCADE")
    )
    email: Mapped[str | None] = mapped_column(Text)
    ss58: Mapped[str | None] = mapped_column(Text)

    secret_sha256: Mapped[bytes] = mapped_column(SHA256, nullable=False, unique=True)
    # The exact bytes the client is asked to sign, stored verbatim so verification
    # never reconstructs them and cannot reconstruct them differently.
    message: Mapped[str | None] = mapped_column(Text)

    expires_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    consumed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "kind <> 'EMAIL' OR email IS NOT NULL", name="challenge_email_present"
        ),
        CheckConstraint(
            "kind NOT IN ('WALLET', 'HOTKEY_LINK') "
            "OR (ss58 IS NOT NULL AND message IS NOT NULL)",
            name="challenge_wallet_present",
        ),
        CheckConstraint(
            "kind <> 'HOTKEY_LINK' OR account_id IS NOT NULL",
            name="challenge_link_has_account",
        ),
        CheckConstraint(
            "expires_at > created_at", name="challenge_expires_after_creation"
        ),
        Index(
            "login_challenges_email_idx",
            text("lower(email)"),
            text("created_at DESC"),
            postgresql_where=text("email IS NOT NULL"),
        ),
        Index(
            "login_challenges_ss58_idx",
            "ss58",
            text("created_at DESC"),
            postgresql_where=text("ss58 IS NOT NULL"),
        ),
        Index(
            "login_challenges_expiry_idx",
            "expires_at",
            postgresql_where=text("consumed_at IS NULL"),
        ),
    )


class CreditLedgerEntry(Base):
    """One movement of an account's balance. Append-only.

    Make it append-only in deployment: REVOKE UPDATE, DELETE from the service
    role. A ledger that can be rewritten is not a ledger, and the balance is
    derived from it rather than cached anywhere.

    Amounts are in rao, signed. Credits are DERIVED, not stored: a credit is one
    verification attempt at the price in force, so the available credit count is
    the balance divided by that price. Storing a count as well would create two
    numbers that can disagree.
    """

    __tablename__ = "credit_ledger"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False
    )

    kind: Mapped[CreditEntryKind] = mapped_column(CREDIT_ENTRY_KIND, nullable=False)
    amount_rao: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # The price in force when an attempt was spent, so a later reprice does not
    # restate what a past attempt cost.
    credit_price_rao: Mapped[int | None] = mapped_column(BigInteger)

    # A SPEND names its INTENT, not its submission. `submissions.credit_ledger_id`
    # points here, so a SPEND pointing back at its submission would make the two
    # rows reference each other with neither insertable first — and this table is
    # append-only, so insert-then-backfill is not available. The intent already
    # exists when the credit is spent. The submission stays reachable through
    # SubmissionIntent.submission_id.
    deposit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "deposits.id", name="credit_ledger_deposit_fkey", use_alter=True
        ),
    )
    intent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "submission_intents.id", name="credit_ledger_intent_fkey", use_alter=True
        ),
    )
    reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("amount_rao <> 0", name="ledger_amount_nonzero"),
        CheckConstraint(
            "credit_price_rao IS NULL OR credit_price_rao > 0",
            name="ledger_price_positive",
        ),
        # Signs are fixed per kind, so a debit cannot be recorded as a credit by
        # passing the wrong sign. ADJUSTMENT is the only either-way kind, and it
        # must say why.
        CheckConstraint(
            "kind NOT IN ('DEPOSIT', 'REFUND', 'BONUS') OR amount_rao > 0",
            name="ledger_credits_are_positive",
        ),
        CheckConstraint(
            "kind <> 'SPEND' OR amount_rao < 0", name="ledger_spends_are_negative"
        ),
        CheckConstraint(
            "kind <> 'ADJUSTMENT' OR reason IS NOT NULL",
            name="ledger_adjustments_explain_themselves",
        ),
        CheckConstraint(
            "kind <> 'SPEND' "
            "OR (intent_id IS NOT NULL AND credit_price_rao IS NOT NULL)",
            name="ledger_spend_names_its_intent",
        ),
        CheckConstraint(
            "kind <> 'DEPOSIT' OR deposit_id IS NOT NULL",
            name="ledger_deposit_names_its_deposit",
        ),
        Index("credit_ledger_account_idx", "account_id", text("id DESC")),
        # One SPEND per intent. A second debit for the same attempt is
        # double-charging, and an intent is exactly one attempt.
        Index(
            "credit_ledger_spend_idx",
            "intent_id",
            unique=True,
            postgresql_where=text("kind = 'SPEND'"),
        ),
    )


class Deposit(Base):
    """An intent to send TAO to the treasury, and what was observed for it.

    `amount_rao` and `treasury_address` are recorded at creation so confirmation
    has something to check against rather than crediting whatever arrived.
    """

    __tablename__ = "deposits"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False
    )

    amount_rao: Mapped[int] = mapped_column(BigInteger, nullable=False)
    treasury_address: Mapped[str] = mapped_column(SS58, nullable=False)
    credit_price_rao: Mapped[int] = mapped_column(BigInteger, nullable=False)

    status: Mapped[DepositState] = mapped_column(
        DEPOSIT_STATE, nullable=False, server_default=DepositState.AWAITING_TRANSFER.value
    )

    extrinsic_reference: Mapped[str | None] = mapped_column(Text)
    sender_coldkey: Mapped[str | None] = mapped_column(SS58)
    observed_amount_rao: Mapped[int | None] = mapped_column(BigInteger)
    block: Mapped[int | None] = mapped_column(BigInteger)
    failure_reason: Mapped[str | None] = mapped_column(Text)

    credited_ledger_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("credit_ledger.id"), unique=True
    )

    expires_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("amount_rao > 0", name="deposit_amount_positive"),
        CheckConstraint("credit_price_rao > 0", name="deposit_price_positive"),
        CheckConstraint(
            "extrinsic_reference IS NULL "
            "OR length(extrinsic_reference) BETWEEN 1 AND 128",
            name="deposit_reference_length",
        ),
        CheckConstraint(
            "observed_amount_rao IS NULL OR observed_amount_rao > 0",
            name="deposit_observed_positive",
        ),
        CheckConstraint("block IS NULL OR block > 0", name="deposit_block_positive"),
        CheckConstraint(
            "status <> 'CREDITED' "
            "OR (credited_ledger_id IS NOT NULL "
            "AND extrinsic_reference IS NOT NULL "
            "AND observed_amount_rao IS NOT NULL "
            "AND block IS NOT NULL)",
            name="deposit_credited_needs_ledger_and_finality",
        ),
        CheckConstraint(
            "status <> 'SEEN_UNFINALIZED' OR extrinsic_reference IS NOT NULL",
            name="deposit_seen_needs_a_reference",
        ),
        CheckConstraint(
            "status <> 'FAILED' OR failure_reason IS NOT NULL",
            name="deposit_failed_needs_a_reason",
        ),
        CheckConstraint(
            "updated_at >= created_at", name="deposits_updated_not_before_created"
        ),
        # One transfer funds one deposit, the rule submissions.payment_reference
        # follows. Partial, so rows with no reference yet do not collide on NULL.
        Index(
            "deposits_extrinsic_idx",
            "extrinsic_reference",
            unique=True,
            postgresql_where=text("extrinsic_reference IS NOT NULL"),
        ),
        Index("deposits_account_idx", "account_id", text("created_at DESC")),
        Index(
            "deposits_open_idx",
            "created_at",
            postgresql_where=text(
                "status IN ('AWAITING_TRANSFER', 'SEEN_UNFINALIZED')"
            ),
        ),
    )


event.listen(
    Deposit.__table__,
    "after_create",
    DDL(
        "CREATE FUNCTION deposits_touch_updated_at() RETURNS TRIGGER AS $$\n"
        "BEGIN\n"
        "    NEW.updated_at := now();\n"
        "    RETURN NEW;\n"
        "END;\n"
        "$$ LANGUAGE plpgsql;\n"
        "\n"
        "CREATE TRIGGER deposits_touch_updated_at\n"
        "    BEFORE UPDATE ON deposits\n"
        "    FOR EACH ROW EXECUTE FUNCTION deposits_touch_updated_at();"
    ),
)
event.listen(
    Deposit.__table__,
    "before_drop",
    DDL("DROP FUNCTION IF EXISTS deposits_touch_updated_at() CASCADE;"),
)


class SubmissionIntent(Base):
    """A held credit and, once uploaded, the bundle it will be spent on.

    The hold is state on this row rather than a ledger entry, because a hold is
    not a movement of money: it is a claim on the balance that either becomes a
    SPEND or evaporates. Writing holds to the ledger would put entries there that
    later have to be undone, which is exactly what an append-only ledger cannot
    do. The available credit count is therefore the balance minus live holds.

    `proof_content` lives here rather than in `proofs` because `proofs` is the
    record of what was verified, and an unconfirmed intent has not paid for
    verification yet. It moves across in the confirming transaction.
    """

    __tablename__ = "submission_intents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False
    )
    # Checked against linked_hotkeys at creation, and what the confirming
    # signature must come from.
    hotkey: Mapped[str] = mapped_column(SS58, nullable=False)

    task_id: Mapped[str] = mapped_column(Text, nullable=False)
    task_bundle_sha256: Mapped[bytes] = mapped_column(SHA256, nullable=False)

    status: Mapped[IntentState] = mapped_column(
        INTENT_STATE, nullable=False, server_default=IntentState.OPEN.value
    )
    credits_held: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    credit_price_rao: Mapped[int] = mapped_column(BigInteger, nullable=False)

    proof_content: Mapped[bytes | None] = mapped_column(LargeBinary)
    proof_sha256: Mapped[bytes | None] = mapped_column(SHA256)
    # Computed by the server from the bundle, so the client never chooses what it
    # is signing.
    request_digest: Mapped[bytes | None] = mapped_column(SHA256)

    submission_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("submissions.id"), unique=True
    )

    expires_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "length(task_id) BETWEEN 1 AND 255", name="intent_task_id_nonempty"
        ),
        CheckConstraint("credits_held > 0", name="intent_holds_something"),
        CheckConstraint("credit_price_rao > 0", name="intent_price_positive"),
        CheckConstraint(
            "(proof_content IS NULL) = (proof_sha256 IS NULL)",
            name="intent_proof_is_paired",
        ),
        CheckConstraint(
            "proof_content IS NULL OR proof_sha256 = pg_catalog.sha256(proof_content)",
            name="intent_proof_digest_matches",
        ),
        CheckConstraint(
            "status <> 'BUNDLE_ATTACHED' "
            "OR (proof_sha256 IS NOT NULL AND request_digest IS NOT NULL)",
            name="intent_attached_has_a_proof",
        ),
        CheckConstraint(
            "status <> 'CONFIRMED' OR submission_id IS NOT NULL",
            name="intent_confirmed_has_a_submission",
        ),
        CheckConstraint(
            "expires_at > created_at", name="intent_expires_after_creation"
        ),
        CheckConstraint(
            "updated_at >= created_at", name="intents_updated_not_before_created"
        ),
        # The live-hold sum behind the available credit count, and the expiry
        # sweeper's queue.
        Index(
            "submission_intents_live_idx",
            "account_id",
            "expires_at",
            postgresql_where=text("status IN ('OPEN', 'BUNDLE_ATTACHED')"),
        ),
        Index(
            "submission_intents_account_idx", "account_id", text("created_at DESC")
        ),
    )


event.listen(
    SubmissionIntent.__table__,
    "after_create",
    DDL(
        "CREATE FUNCTION submission_intents_touch_updated_at() RETURNS TRIGGER AS $$\n"
        "BEGIN\n"
        "    NEW.updated_at := now();\n"
        "    RETURN NEW;\n"
        "END;\n"
        "$$ LANGUAGE plpgsql;\n"
        "\n"
        "CREATE TRIGGER submission_intents_touch_updated_at\n"
        "    BEFORE UPDATE ON submission_intents\n"
        "    FOR EACH ROW EXECUTE FUNCTION submission_intents_touch_updated_at();"
    ),
)
event.listen(
    SubmissionIntent.__table__,
    "before_drop",
    DDL("DROP FUNCTION IF EXISTS submission_intents_touch_updated_at() CASCADE;"),
)


class SubmissionEvent(Base):
    """What the miner sees while waiting.

    The status columns on Submission say where a submission is now; this says how
    it got there, which is the question a miner asks when nothing appears to be
    happening. Append-only, like the ledger.
    """

    __tablename__ = "submission_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    submission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("submissions.id"), nullable=False
    )

    kind: Mapped[str] = mapped_column(Text, nullable=False)
    # Miner-visible prose. Optional: the kind is the machine-readable part.
    detail: Mapped[str | None] = mapped_column(Text)
    context: Mapped[dict | None] = mapped_column(JSONB)
    actor: Mapped[str] = mapped_column(Text, nullable=False)

    occurred_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("kind ~ '^[A-Z][A-Z0-9_]{0,63}$'", name="event_kind_shape"),
        # The timeline read: one submission, oldest first. btree scans either
        # direction, so no DESC variant is needed.
        Index("submission_events_submission_idx", "submission_id", "id"),
    )


# --- The chain watcher --------------------------------------------------------
# Mirrors deploy/migrate/sql/V005__chain_transfer_watch.sql. Two tables: what
# arrived, and where the watcher has read to.


class ChainTransferState(enum.StrEnum):
    UNATTRIBUTED = "UNATTRIBUTED"  # observed, no account owns the sending coldkey
    CREDITED = "CREDITED"
    IGNORED = "IGNORED"  # deliberately not credited, and it says why


CHAIN_TRANSFER_STATE = _pg_enum(ChainTransferState, "chain_transfer_state")


class ChainTransfer(Base):
    """One `Balances.Transfer` event into the watched address.

    Separate from `Deposit` because the two answer different questions: a deposit
    is what an account said it would send, a transfer is what arrived. Most
    arrivals have no declaration behind them, and an arrival nobody can be
    matched to is exactly the row an operator has to be able to see.

    Append-mostly. The chain facts — block, sender, amount, reference — are
    written once and never change; only the attribution columns move, and only
    ever away from UNATTRIBUTED.
    """

    __tablename__ = "chain_transfers"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)

    # `block-extrinsic-event`. The event index and not just the extrinsic: a
    # `utility.batch` emits several Transfer events under one extrinsic, and
    # keying on the extrinsic alone would collapse two payments into one row.
    extrinsic_reference: Mapped[str] = mapped_column(Text, nullable=False)

    block: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # The block's own Timestamp.set inherent, not when we read it. This is what
    # "after the genesis timestamp" is judged against.
    block_timestamp: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    extrinsic_index: Mapped[int] = mapped_column(Integer, nullable=False)
    event_index: Mapped[int] = mapped_column(Integer, nullable=False)

    sender_coldkey: Mapped[str] = mapped_column(SS58, nullable=False)
    recipient: Mapped[str] = mapped_column(SS58, nullable=False)
    amount_rao: Mapped[int] = mapped_column(BigInteger, nullable=False)

    status: Mapped[ChainTransferState] = mapped_column(
        CHAIN_TRANSFER_STATE,
        nullable=False,
        server_default=ChainTransferState.UNATTRIBUTED.value,
    )

    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id")
    )
    deposit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deposits.id"), unique=True
    )

    # Both are for an operator reading this table; NEITHER is authoritative.
    # `credit_ledger` holds the rao and `credits.credit_balance` divides it, so a
    # 0.7 TAO transfer at 0.5 records credits_granted = 1 and leaves 0.2 in the
    # balance towards the next one. Recomputing a balance from this column would
    # throw those remainders away.
    credit_price_rao: Mapped[int | None] = mapped_column(BigInteger)
    credits_granted: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )

    note: Mapped[str | None] = mapped_column(Text)

    observed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "length(extrinsic_reference) BETWEEN 1 AND 128",
            name="transfer_reference_length",
        ),
        CheckConstraint("block > 0", name="transfer_block_positive"),
        CheckConstraint(
            "extrinsic_index >= 0", name="transfer_extrinsic_index_nonnegative"
        ),
        CheckConstraint("event_index >= 0", name="transfer_event_index_nonnegative"),
        CheckConstraint("amount_rao > 0", name="transfer_amount_positive"),
        CheckConstraint(
            "credit_price_rao IS NULL OR credit_price_rao > 0",
            name="transfer_price_positive",
        ),
        CheckConstraint("credits_granted >= 0", name="transfer_credits_nonnegative"),
        CheckConstraint(
            "note IS NULL OR length(note) BETWEEN 1 AND 500",
            name="transfer_note_length",
        ),
        # A credited transfer has all of its attribution, or it is not credited.
        # The deposit carries the ledger entry, so naming the deposit names the
        # money.
        CheckConstraint(
            "status <> 'CREDITED' "
            "OR (account_id IS NOT NULL "
            "AND deposit_id IS NOT NULL "
            "AND credit_price_rao IS NOT NULL)",
            name="transfer_credited_needs_attribution",
        ),
        CheckConstraint(
            "status = 'CREDITED' OR credits_granted = 0",
            name="transfer_uncredited_grants_nothing",
        ),
        CheckConstraint(
            "status <> 'IGNORED' OR note IS NOT NULL",
            name="transfer_ignored_needs_a_reason",
        ),
        CheckConstraint(
            "updated_at >= observed_at",
            name="chain_transfers_updated_not_before_observed",
        ),
        # The idempotency of the whole watcher. A block re-read after a restart, a
        # crash between recording and advancing the cursor, or an overlapping
        # rescan all land here instead of crediting a transfer twice.
        Index("chain_transfers_reference_idx", "extrinsic_reference", unique=True),
        # The same fact by its parts, so a malformed reference cannot smuggle a
        # duplicate past the index above.
        Index(
            "chain_transfers_position_idx",
            "block",
            "extrinsic_index",
            "event_index",
            unique=True,
        ),
        Index("chain_transfers_sender_idx", "sender_coldkey", text("block DESC")),
        Index(
            "chain_transfers_account_idx",
            "account_id",
            text("block DESC"),
            postgresql_where=text("account_id IS NOT NULL"),
        ),
        # The operator's queue: money that arrived and belongs to nobody yet.
        Index(
            "chain_transfers_unattributed_idx",
            "block",
            postgresql_where=text("status = 'UNATTRIBUTED'"),
        ),
    )


event.listen(
    ChainTransfer.__table__,
    "after_create",
    DDL(
        "CREATE FUNCTION chain_transfers_touch_updated_at() RETURNS TRIGGER AS $$\n"
        "BEGIN\n"
        "    NEW.updated_at := now();\n"
        "    RETURN NEW;\n"
        "END;\n"
        "$$ LANGUAGE plpgsql;\n"
        "\n"
        "CREATE TRIGGER chain_transfers_touch_updated_at\n"
        "    BEFORE UPDATE ON chain_transfers\n"
        "    FOR EACH ROW EXECUTE FUNCTION chain_transfers_touch_updated_at();"
    ),
)
event.listen(
    ChainTransfer.__table__,
    "before_drop",
    DDL("DROP FUNCTION IF EXISTS chain_transfers_touch_updated_at() CASCADE;"),
)


class ChainWatchCursor(Base):
    """Where the watcher has read to, and what it believes it is watching.

    `recipient`, `netuid`, `uid` and `watch_from` are recorded rather than left to
    configuration alone, because all four decide which money this validator
    considers its own. The worker compares its environment against this row at
    startup and refuses to run when they disagree: adopting a new address
    silently would leave the old one's arrivals uncredited, and moving
    `watch_from` earlier silently would re-scan a range whose transfers were
    never meant to buy credits.

    One high-water mark rather than a ledger of scanned heights. This worker
    follows the finalized head forwards in one process; a per-block ledger is
    what a parallel historical backfill needs, which this is not.
    """

    __tablename__ = "chain_watch_cursor"

    watcher: Mapped[str] = mapped_column(Text, primary_key=True)

    recipient: Mapped[str] = mapped_column(SS58, nullable=False)
    netuid: Mapped[int] = mapped_column(Integer, nullable=False)
    uid: Mapped[int] = mapped_column(Integer, nullable=False)

    # Transfers in blocks before this are not this validator's business.
    watch_from: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    # The first block at or after `watch_from`, resolved once by bisecting
    # on-chain block times and then never recomputed — a second bisection against
    # a re-synced node could land either side of it and change which transfers
    # exist at all.
    start_block: Mapped[int] = mapped_column(BigInteger, nullable=False)
    start_block_timestamp: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # `start_block - 1` on a fresh cursor, meaning "nothing scanned yet".
    last_scanned_block: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_scanned_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "watcher ~ '^[a-z][a-z0-9-]{0,63}$'", name="watcher_name_shape"
        ),
        CheckConstraint("netuid >= 0", name="cursor_netuid_nonnegative"),
        CheckConstraint("uid >= 0", name="cursor_uid_nonnegative"),
        CheckConstraint("start_block > 0", name="cursor_start_block_positive"),
        CheckConstraint(
            "last_scanned_block >= start_block - 1",
            name="cursor_never_reads_before_its_start",
        ),
        CheckConstraint(
            "start_block_timestamp >= watch_from",
            name="cursor_start_block_is_at_or_after_watch_from",
        ),
    )


__all__ = [
    "ACCOUNT_ROLES",
    "ADMIN_ROLE",
    "MINER_ROLE",
    "REVIEWER_ROLE",
    "Account",
    "AccountSession",
    "AccountWallet",
    "ApiRejectionLog",
    "Base",
    "ChainTransfer",
    "ChainTransferState",
    "ChainWatchCursor",
    "CreditEntryKind",
    "CreditLedgerEntry",
    "Deposit",
    "DepositState",
    "IntentState",
    "LinkedHotkey",
    "LoginChallenge",
    "LoginChallengeKind",
    "ManualReviewState",
    "PayoutState",
    "Proof",
    "ReviewDecision",
    "ReviewOutcome",
    "ReviewerKind",
    "RewardEvent",
    "RewardState",
    "Submission",
    "SubmissionEvent",
    "SubmissionIntent",
    "TaskMode",
    "VerificationRun",
    "VerificationState",
]
