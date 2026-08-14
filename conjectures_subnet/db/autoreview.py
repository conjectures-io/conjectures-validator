"""Read-only queries over the `autoreview` schema, for the reviewer-facing panel.

The write side of these two tables lives in `conjectures-autoreview`, which owns the money, the
provider and the archive. This module only reads them, and it is the only place in the validator
that does.

Three decisions worth stating, because each one is a thing a later change could quietly undo.

**Nothing here decides who may read it.** The rows carry model prose about a miner's submission,
which is not public — but the gate is a role on the route (`require_role`), not a predicate in a
query, because unlike the public feeds there is no "publishable subset" of an advisory assessment.
A caller either may see the review material or may not.

**The submission half is not re-implemented.** A reviewer needs the submission's identity, title,
statement and verifier state alongside the assessments, and `conjectures_subnet.db.public` already
answers exactly that for `/v1/results/in-review`. Reading it twice, from two modules, is how the
two answers come to disagree about which submissions are awaiting review. So the router composes:
`public.in_review_page` for the submissions, `attempts_for` for what the models said about them.

**One statement per page, not one per submission.** A page of five submissions is one query over
`stage_results_submission_idx`, keyed by the ids already in hand. The rows are small — the prose
lives in a JSONB column, and the largest single verdict we have recorded is a few kilobytes — so
there is no per-row fetch to defer and no reason for a second round trip.

The columns are named rather than selected as mapped objects. That is deliberate: it makes what
crosses this boundary an allowlist, so a column added to `stage_results` later has to be named here
before it can reach a response.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from conjectures_subnet.db.autoreview_models import (
    AdvisoryConfidence,
    AdvisoryOutcome,
    AutoreviewRun,
    AutoreviewStageResult,
    StageStatus,
)


@dataclass(frozen=True)
class AttemptRow:
    """One advisory assessment: one stage, one model, one call that was paid for.

    A submission has many of these — one per stage per pass, plus a `SKIPPED` row for every stage a
    cascade stopped short of, and a `FAILED` row for every stage that could not answer. All three
    are real history and all three are returned: a stage that was never reached and a stage that
    errored are different facts, and the row's `status` is what says which.

    `verdict` is the archived JSON object, unmodified. It is the same document
    `stage_results_promoted_match_verdict` compares the promoted columns against, so the promoted
    fields on this row and the ones inside `verdict` cannot disagree — the database refuses the row
    if they do.
    """

    id: int
    submission_id: uuid.UUID
    run_id: int
    # Which pass this belonged to. `1` for the first sweep on a submission, and a second pass adds
    # `2` rather than replacing it: an assessment is evidence, and evidence is not overwritten.
    attempt: int
    stage: str
    stage_version: str
    status: StageStatus
    model_requested: str
    # What the router actually served. Differs from `model_requested` when a provider substitutes,
    # which is a fact about the answer and the reason both are recorded.
    model_served: str | None
    provider: str | None
    reason_code: str | None
    outcome: AdvisoryOutcome | None
    confidence: AdvisoryConfidence | None
    summary: str | None
    input_attempted_to_instruct: bool | None
    verdict: Mapping[str, Any] | None
    # The search parameters, when the stage was allowed to search. Its presence is the only thing a
    # reader needs from it: an assessment that searched the web and one that reasoned from the pack
    # alone carry different weight.
    search: Mapping[str, Any] | None
    # Never null. The contract is that this array can be empty but is never missing, and the
    # retrieved page text is not in it — see `stage_results.citations`.
    citations: Sequence[Mapping[str, Any]]
    # Why a row is not COMPLETED. Required by `stage_results_incomplete_says_why`, so a SKIPPED or
    # FAILED row always says something.
    detail: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    cost_usd: Decimal | None
    attempt_sha256: bytes | None
    archive_path: str | None
    started_at: datetime | None
    finished_at: datetime | None
    review_policy_version: str

    @property
    def key(self) -> str:
        """A stable, opaque name for this attempt, and the name of its archive directory.

        `<stage>-<first 8 hex of attempt_sha256>` is how `conjectures-autoreview` names the
        directory holding the request, the response and the attempt record, so a reviewer reading
        this key can find the evidence on disk without a second lookup.

        A row with no digest never produced an archive — nothing was sent, so there is nothing to
        digest — and falls back to the row's own id. That keeps the key unique per attempt, which is
        what a client rendering a list of them needs, without inventing a digest that points at a
        directory that does not exist.
        """
        if self.attempt_sha256 is None:
            return f"{self.stage}-row{self.id}"
        return f"{self.stage}-{self.attempt_sha256.hex()[:8]}"


async def attempts_for(
    session: AsyncSession, submission_ids: Sequence[uuid.UUID]
) -> Mapping[uuid.UUID, tuple[AttemptRow, ...]]:
    """Every recorded assessment for these submissions, newest first within each.

    Keyed by submission id, and a submission with no assessments is simply absent from the mapping
    rather than being an error. That case is normal and it is not a gap: a submission is due for
    review the moment Lean verifies it, whether or not the advisory service has reached it yet, and
    a reviewer needs to see it either way.

    Ordered newest first because a later pass is the more informative one — it read a later pack, or
    a later prompt — while the earlier pass stays visible underneath it.
    """
    if not submission_ids:
        return {}

    statement = (
        select(
            AutoreviewStageResult.id,
            AutoreviewStageResult.submission_id,
            AutoreviewStageResult.run_id,
            AutoreviewStageResult.stage,
            AutoreviewStageResult.stage_version,
            AutoreviewStageResult.status,
            AutoreviewStageResult.model_requested,
            AutoreviewStageResult.model_served,
            AutoreviewStageResult.provider,
            AutoreviewStageResult.reason_code,
            AutoreviewStageResult.outcome,
            AutoreviewStageResult.confidence,
            AutoreviewStageResult.summary,
            AutoreviewStageResult.input_attempted_to_instruct,
            AutoreviewStageResult.verdict,
            AutoreviewStageResult.search,
            AutoreviewStageResult.citations,
            AutoreviewStageResult.detail,
            AutoreviewStageResult.prompt_tokens,
            AutoreviewStageResult.completion_tokens,
            AutoreviewStageResult.cost_usd,
            AutoreviewStageResult.attempt_sha256,
            AutoreviewStageResult.archive_path,
            AutoreviewStageResult.started_at,
            AutoreviewStageResult.finished_at,
            AutoreviewRun.attempt,
            AutoreviewRun.review_policy_version,
        )
        # An explicit ON: the mirror declares no `relationship()`, so that a join is written where
        # it happens instead of being implied by an attribute.
        .join(AutoreviewRun, AutoreviewRun.id == AutoreviewStageResult.run_id)
        .where(AutoreviewStageResult.submission_id.in_(tuple(submission_ids)))
        # `started_at` is null on a SKIPPED row, and those sort last rather than first: a stage the
        # cascade never reached is the least informative thing on the list. `id` breaks the tie so
        # the order is total and a page is reproducible.
        .order_by(
            AutoreviewStageResult.started_at.desc().nullslast(),
            AutoreviewStageResult.id.desc(),
        )
    )

    grouped: dict[uuid.UUID, list[AttemptRow]] = defaultdict(list)
    for row in (await session.execute(statement)).all():
        grouped[row.submission_id].append(
            AttemptRow(
                id=row.id,
                submission_id=row.submission_id,
                run_id=row.run_id,
                attempt=row.attempt,
                stage=row.stage,
                stage_version=row.stage_version,
                status=row.status,
                model_requested=row.model_requested,
                model_served=row.model_served,
                provider=row.provider,
                reason_code=row.reason_code,
                outcome=row.outcome,
                confidence=row.confidence,
                summary=row.summary,
                input_attempted_to_instruct=row.input_attempted_to_instruct,
                verdict=row.verdict,
                search=row.search,
                citations=tuple(row.citations or ()),
                detail=row.detail,
                prompt_tokens=row.prompt_tokens,
                completion_tokens=row.completion_tokens,
                cost_usd=row.cost_usd,
                attempt_sha256=row.attempt_sha256,
                archive_path=row.archive_path,
                started_at=row.started_at,
                finished_at=row.finished_at,
                review_policy_version=row.review_policy_version,
            )
        )
    return {key: tuple(value) for key, value in grouped.items()}


__all__ = ["AttemptRow", "attempts_for"]
