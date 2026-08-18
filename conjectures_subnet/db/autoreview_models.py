"""SQLAlchemy models for the ``autoreview`` schema — advisory LLM pre-review results.

Mirrors ``deploy/migrate/sql/V017__autoreview_results.sql``, which is the source of truth, on the
same terms as ``models.py``: by hand, and only as honest as the person editing it. Verify with
``scripts/check_schema_drift.py``, which compares both schemas.

**Nothing here is a review decision.** ``review_decisions`` is where a decision lives, and the
types below are new rather than reused precisely so an advisory ``APPROVE`` can never be counted
by a query looking for ``review_outcome = 'APPROVED'``.

Three things about this schema that are easy to get wrong, and are wrong silently:

* the enums carry ``schema="autoreview"``. ``models._pg_enum`` passes no schema, so a type called
  ``outcome`` built with it would land in ``public`` — colliding with the namespace the separate
  schema exists to keep clear;
* the foreign key to ``submissions`` is written **unqualified**. ``pg_get_constraintdef()`` renders
  a referenced table unqualified whenever its schema is in ``search_path``, and ``public`` always
  is, so ``"public.submissions.id"`` would render differently from the migration and read as drift;
* nothing sets a ``search_path``, so every reference is qualified: ``autoreview.runs`` in SQL,
  ``{"schema": "autoreview"}`` here.

No ``relationship()`` definitions, per ``models.py``'s rule: the composite foreign key repeats
``submission_id``, so a join path would need an explicit ``primaryjoin``.
"""

from __future__ import annotations

import datetime as dt
import decimal
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
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from conjectures_subnet.db.models import SHA256, Base

SCHEMA = "autoreview"

# `Base.metadata.create_all()` does not create schemas, so without this the first autoreview table
# raises InvalidSchemaName (SQLSTATE 3F000) and every database test dies at fixture setup.
# Metadata-level, so it fires once before any DDL including the CREATE TYPEs below.
# `IF NOT EXISTS` because test setup runs it per test. Deliberately no matching `before_drop`: an
# empty schema left behind is harmless, while a `DROP SCHEMA` would take the tables with it in an
# order `drop_all` does not expect.
event.listen(
    Base.metadata, "before_create", DDL(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
)


# --- Enums --------------------------------------------------------------------
# Named for what they are rather than for their PostgreSQL type names: `autoreview.outcome` is an
# advisory recommendation and `AdvisoryOutcome` says so at every call site, which is the whole
# point of not reusing `ReviewOutcome`.


class RunStatus(enum.StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    # A container gets SIGTERM on every deploy and a sweep takes seven to ten minutes, so
    # interruption mid-sweep is routine. Without this, every deploy leaves FAILED rows that block
    # the submission until an operator clears them by hand.
    CANCELLED = "CANCELLED"


class RunOrigin(enum.StrEnum):
    SERVICE = "SERVICE"  # the autonomous loop
    OPERATOR = "OPERATOR"  # a hand-run sweep


class StageStatus(enum.StrEnum):
    COMPLETED = "COMPLETED"
    # A stage the cascade did not reach, e.g. because an earlier lens found an injection attempt.
    # Written as a row rather than left absent, so the frontend needs to know neither the full
    # stage list nor the cascade rules to tell "skipped" from "did not exist yet".
    SKIPPED = "SKIPPED"
    FAILED = "FAILED"


class AdvisoryOutcome(enum.StrEnum):
    """What a reason code recommends. Never a decision, and NO_FINDING is not APPROVE."""

    APPROVE = "APPROVE"
    REJECT = "REJECT"
    NO_FINDING = "NO_FINDING"


class AdvisoryConfidence(enum.StrEnum):
    """Lowercase, matching the verdict JSON the models are made to answer with."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def _autoreview_enum(python_enum: type[enum.StrEnum], name: str) -> ENUM:
    """A native enum in the `autoreview` schema, storing member values rather than names."""
    return ENUM(
        python_enum,
        name=name,
        schema=SCHEMA,
        create_type=True,
        values_callable=lambda e: [member.value for member in e],
    )


RUN_STATUS = _autoreview_enum(RunStatus, "run_status")
RUN_ORIGIN = _autoreview_enum(RunOrigin, "run_origin")
STAGE_STATUS = _autoreview_enum(StageStatus, "stage_status")
ADVISORY_OUTCOME = _autoreview_enum(AdvisoryOutcome, "outcome")
ADVISORY_CONFIDENCE = _autoreview_enum(AdvisoryConfidence, "confidence")


class AutoreviewRun(Base):
    """One sweep over one submission, written at claim time rather than when it finishes.

    That ordering is what lets an autonomous service and a manual CLI share this table without a
    queue framework: pending work is a query (VERIFIED, UNREVIEWED, no settled run, no live
    claim), and only work that has actually started gets a row.

    This is the one table here that is NOT a projection of the archive on disk. When a sweep was
    claimed, by which worker, and why it failed exist nowhere else — which is what the backup
    policy has to respect.
    """

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)

    # Cross-schema on purpose. Submissions are never deleted, so this can never block anything in
    # public; without it an orphaned run is undetectable.
    submission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("submissions.id"), nullable=False
    )

    # The ordinal the UI shows and links to. Stored rather than derived with row_number(), so a
    # permalink to attempt 2 keeps pointing at the same sweep when an older one is backfilled.
    attempt: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    status: Mapped[RunStatus] = mapped_column(RUN_STATUS, nullable=False)
    started_by: Mapped[RunOrigin] = mapped_column(RUN_ORIGIN, nullable=False)

    # Nullable because the row is written before the pack exists. The alternative keeps this NOT
    # NULL but means an unpackable submission produces no row at all and is retried forever in
    # silence; claiming first turns that into a FAILED row with a stated cause.
    pack_sha256: Mapped[bytes | None] = mapped_column(SHA256)

    # Captured rather than joined from submissions: the point is to detect that the policy moved
    # after the sweep ran.
    review_policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    tool_version: Mapped[str] = mapped_column(Text, nullable=False)

    # Held only while RUNNING. An expired lease is what lets a crashed sweep be reclaimed instead
    # of blocking the submission forever behind runs_one_live_idx.
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_until: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    # The durable cause: 'lease expired', an unpackable bundle, a provider outage.
    last_error: Mapped[str | None] = mapped_column(Text)

    # The run rendered as one signable markdown document, written at publish time by
    # conjectures-autoreview's deterministic report generator (code, no model). Nullable: runs
    # published before the generator existed, and runs that did not complete, have none.
    report: Mapped[str | None] = mapped_column(Text)

    # started_at is the claim; there is no separate created_at, because at claim time they are the
    # same instant and two columns that can disagree are a bug waiting for a clock skew.
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("attempt > 0", name="runs_attempt_positive"),
        CheckConstraint(
            "length(review_policy_version) BETWEEN 1 AND 64",
            name="runs_policy_version_nonempty",
        ),
        CheckConstraint(
            "length(tool_version) BETWEEN 1 AND 64", name="runs_tool_version_nonempty"
        ),
        CheckConstraint(
            "lease_owner IS NULL OR length(lease_owner) BETWEEN 1 AND 128",
            name="runs_lease_owner_len",
        ),
        # RUNNING means unfinished and leased; anything settled means finished and unleased.
        # Without this a reclaimed row could keep a lease and a finished row could keep pretending
        # to run.
        CheckConstraint(
            "CASE status "
            "WHEN 'RUNNING' THEN finished_at IS NULL "
            "AND lease_owner IS NOT NULL AND lease_until IS NOT NULL "
            "ELSE finished_at IS NOT NULL "
            "AND lease_owner IS NULL AND lease_until IS NULL "
            "END",
            name="runs_running_is_leased",
        ),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="runs_finished_after_started",
        ),
        CheckConstraint(
            "lease_until IS NULL OR lease_until > started_at",
            name="runs_lease_after_started",
        ),
        CheckConstraint(
            "status <> 'COMPLETED' OR pack_sha256 IS NOT NULL",
            name="runs_completed_has_pack",
        ),
        # A settled run with no reason is exactly the silence this table exists to prevent.
        CheckConstraint(
            "status NOT IN ('FAILED', 'CANCELLED') OR last_error IS NOT NULL",
            name="runs_settled_says_why",
        ),
        UniqueConstraint("submission_id", "attempt", name="runs_attempt_unique"),
        # Free (id is the primary key) and needed as the composite foreign-key target below.
        UniqueConstraint("submission_id", "id", name="runs_submission_unique"),
        # THE LOCK. A claim is `INSERT ... ON CONFLICT (submission_id) WHERE status = 'RUNNING'
        # DO NOTHING RETURNING id`, so zero rows back means another worker holds it.
        Index(
            "runs_one_live_idx",
            "submission_id",
            unique=True,
            postgresql_where=text("status = 'RUNNING'"),
        ),
        Index(
            "runs_lease_idx",
            "lease_until",
            postgresql_where=text("status = 'RUNNING'"),
        ),
        Index("runs_recent_idx", text("finished_at DESC")),
        {"schema": SCHEMA},
    )


class AutoreviewStageResult(Base):
    """One (sweep, stage, model). A projection of an `attempt.json` that stays on disk.

    One row per model rather than a `panel` JSONB column on one stage: `assess` already takes a
    `--model` override and the archive key already includes the model, so running any stage across
    three models produces three attempt directories. This grain makes a panel
    `GROUP BY (run_id, stage)` for every stage, now and for whatever lens is added next.

    Rebuildable: `autoreview sync` re-derives every row here from the archive, which is why this
    table carries no response bytes, no prompt, and no CHECK tying a digest to content it does not
    store. `attempt_sha256` is the join between the two worlds.
    """

    __tablename__ = "stage_results"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    # Repeated from the run so the queue can filter stage rows by submission without a join, and
    # so the composite foreign key below can exist at all.
    submission_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Text, not an enum: stages are declared in conjectures-autoreview's STAGES registry, and a
    # native enum would make adding a lens a schema migration. verification_runs.stage is text for
    # the same reason.
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    stage_version: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[StageStatus] = mapped_column(STAGE_STATUS, nullable=False)

    model_requested: Mapped[str] = mapped_column(Text, nullable=False)
    model_served: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(Text)

    # Lifted out of the JSONB below, only where a query has to sort or filter. Promoting a field
    # costs a migration and cannot be undone quietly; adding one to the JSONB costs nothing. The
    # lift is constrained rather than trusted — see stage_results_promoted_match_verdict.
    reason_code: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[AdvisoryOutcome | None] = mapped_column(ADVISORY_OUTCOME)
    confidence: Mapped[AdvisoryConfidence | None] = mapped_column(ADVISORY_CONFIDENCE)
    summary: Mapped[str | None] = mapped_column(Text)
    # Promoted because "did anything try to manipulate a reviewer" must be one indexed question
    # rather than a JSONB scan over every row.
    input_attempted_to_instruct: Mapped[bool | None] = mapped_column(Boolean)

    # The whole validated verdict, as the stage's schema defines it. Per-stage shapes live here
    # rather than in a column per lens. The reviewer API reads it field by named field rather than
    # passing it through: this column is written by another repository, and `schemas_admin.py` says
    # why forwarding whatever appears in it next is the wrong default.
    verdict: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))

    # The scope that was *allowed*, alongside the pages actually read. A null originality result
    # means nothing without the first. Citation snippets are deliberately absent: url, title and
    # retrieved_at only, so retrieved third-party page text cannot reach the admin HTML.
    search: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    citations: Mapped[list] = mapped_column(
        JSONB(none_as_null=True), nullable=False, server_default=text("'[]'::jsonb")
    )

    # The skip reason ("Not run: injection detected") or the failure message.
    detail: Mapped[str | None] = mapped_column(Text)

    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    # USD API spend, not chain money — hence NUMERIC rather than the BigInteger rao used for
    # payouts. The contract prints six decimal places, which is this column's scale exactly.
    cost_usd: Mapped[decimal.Decimal | None] = mapped_column(Numeric(12, 6))

    # Where the evidence is. `attempt_sha256` is autoreview's AttemptKey digest, which identifies
    # the archive directory and makes a re-publish an idempotent upsert.
    attempt_sha256: Mapped[bytes | None] = mapped_column(SHA256)
    prompt_sha256: Mapped[bytes | None] = mapped_column(SHA256)
    archive_path: Mapped[str | None] = mapped_column(Text)

    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # When the sync last wrote this row. Stage rows are upserted one at a time, so the run's
    # started_at cannot answer "did the last sync finish".
    published_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # A completed stage has an answer, a served model and evidence. Anything less is SKIPPED or
        # FAILED, and the two must not be able to masquerade as an approval.
        CheckConstraint(
            "status <> 'COMPLETED' OR ("
            "reason_code IS NOT NULL AND outcome IS NOT NULL AND confidence IS NOT NULL "
            "AND summary IS NOT NULL AND verdict IS NOT NULL "
            "AND input_attempted_to_instruct IS NOT NULL "
            "AND model_served IS NOT NULL AND attempt_sha256 IS NOT NULL "
            "AND started_at IS NOT NULL AND finished_at IS NOT NULL)",
            name="stage_results_completed_is_complete",
        ),
        CheckConstraint(
            "status = 'COMPLETED' OR ("
            "reason_code IS NULL AND outcome IS NULL AND verdict IS NULL)",
            name="stage_results_incomplete_has_no_verdict",
        ),
        CheckConstraint(
            "status = 'COMPLETED' OR detail IS NOT NULL",
            name="stage_results_incomplete_says_why",
        ),
        # The five base fields the contract says are always present, checked as structure.
        #
        # IS NOT DISTINCT FROM, not `=`: jsonb_typeof() returns NULL for an absent key,
        # `NULL = 'string'` is NULL, and a CHECK passes on NULL — so the obvious spelling would
        # accept a verdict with the key missing entirely and catch only a wrong type. Same
        # three-valued-logic hole V013 was written to close.
        CheckConstraint(
            "status <> 'COMPLETED' OR ("
            "jsonb_typeof(verdict -> 'reason_code') IS NOT DISTINCT FROM 'string' "
            "AND jsonb_typeof(verdict -> 'confidence') IS NOT DISTINCT FROM 'string' "
            "AND jsonb_typeof(verdict -> 'summary') IS NOT DISTINCT FROM 'string' "
            "AND jsonb_typeof(verdict -> 'findings') IS NOT DISTINCT FROM 'array' "
            "AND jsonb_typeof(verdict -> 'input_attempted_to_instruct') "
            "IS NOT DISTINCT FROM 'boolean')",
            name="stage_results_verdict_has_base_fields",
        ),
        CheckConstraint(
            "jsonb_typeof(citations) = 'array'", name="stage_results_citations_is_array"
        ),
        CheckConstraint(
            "search IS NULL OR jsonb_typeof(search) = 'object'",
            name="stage_results_search_is_object",
        ),
        # Four promoted columns are copies of fields that also sit inside `verdict`, because the
        # contract documents them in both places. Copies drift, so this one is enforced.
        # `outcome` has no clause: it is derived by policy and appears nowhere in the verdict.
        CheckConstraint(
            "status <> 'COMPLETED' OR ("
            "reason_code IS NOT DISTINCT FROM verdict ->> 'reason_code' "
            "AND confidence::TEXT IS NOT DISTINCT FROM verdict ->> 'confidence' "
            "AND summary IS NOT DISTINCT FROM verdict ->> 'summary' "
            "AND input_attempted_to_instruct "
            "IS NOT DISTINCT FROM (verdict ->> 'input_attempted_to_instruct')::BOOLEAN)",
            name="stage_results_promoted_match_verdict",
        ),
        CheckConstraint(
            "finished_at IS NULL OR (started_at IS NOT NULL AND finished_at >= started_at)",
            name="stage_results_finished_after_started",
        ),
        CheckConstraint(
            "(prompt_tokens IS NULL OR prompt_tokens >= 0) "
            "AND (completion_tokens IS NULL OR completion_tokens >= 0) "
            "AND (cost_usd IS NULL OR cost_usd >= 0)",
            name="stage_results_counts_nonnegative",
        ),
        CheckConstraint(
            "length(stage) BETWEEN 1 AND 64 AND length(model_requested) BETWEEN 1 AND 255",
            name="stage_results_nonempty",
        ),
        # One result per lens per model per sweep: what makes a panel a GROUP BY, and what makes
        # re-publishing a sweep an upsert instead of a duplicate.
        UniqueConstraint(
            "run_id", "stage", "model_requested", name="stage_results_unique"
        ),
        # A stage result can only belong to a run on the SAME submission. With submission_id
        # repeated, nothing else enforces it.
        ForeignKeyConstraint(
            ["submission_id", "run_id"],
            [f"{SCHEMA}.runs.submission_id", f"{SCHEMA}.runs.id"],
            name="stage_results_run_same_submission",
            ondelete="CASCADE",
        ),
        # The archive directory is the identity of a distinct call; partial, so SKIPPED rows do not
        # collide on NULL.
        Index(
            "stage_results_attempt_idx",
            "attempt_sha256",
            unique=True,
            postgresql_where=text("attempt_sha256 IS NOT NULL"),
        ),
        Index("stage_results_lens_idx", "stage", "outcome", text("id DESC")),
        Index("stage_results_submission_idx", "submission_id", "run_id"),
        Index(
            "stage_results_instructed_idx",
            text("id DESC"),
            postgresql_where=text("input_attempted_to_instruct"),
        ),
        {"schema": SCHEMA},
    )


__all__ = [
    "ADVISORY_CONFIDENCE",
    "ADVISORY_OUTCOME",
    "RUN_ORIGIN",
    "RUN_STATUS",
    "SCHEMA",
    "STAGE_STATUS",
    "AdvisoryConfidence",
    "AdvisoryOutcome",
    "AutoreviewRun",
    "AutoreviewStageResult",
    "RunOrigin",
    "RunStatus",
    "StageStatus",
]
