"""Response models for the reviewer-facing surface.

A third audience, and a third set of rules. `schemas.py` answers a miner about their own
submission; `schemas_public.py` answers anyone at all; these answer a signed-in reviewer holding
the `REVIEWER` role, and they carry material neither of the other two may see: what three models
said about a submission, in prose, before a human has decided anything.

**Allowlisted, like the public report and for the same reason.** `stage_results.verdict` is a JSONB
column written by another repository on its own release cycle. Passing it through verbatim would
mean that whatever `conjectures-autoreview` adds to a verdict next appears on this endpoint without
anyone choosing to publish it. So every field is named here, exactly as `lib/admin/schema.ts` in the
review panel reads it, and an unknown key is dropped rather than forwarded. The cost is that a new
verdict field needs one line here; the benefit is that adding one cannot leak by default.

**A citation carries no page text.** `AdminCitation` has three fields and `content` is not among
them — the retrieved third-party text is deliberately not stored (see `AUTOREVIEW_STORE.md`), and
naming the fields here means that if a future projection stored it anyway, this surface still would
not serve it.

**A finding quotes the submission.** `AdminFinding.quote` is bytes a miner supplied, selected by a
model as evidence of an injection attempt. It is data, not markup and not instructions, and the
panel renders it through its `UntrustedQuote` component for that reason. Nothing on this surface
should ever be interpolated into a prompt.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from submission_api.schemas_public import Model


class AdminFinding(Model):
    """One thing a model says it found, with the text it is pointing at.

    `severity` is the model's own grading, and `where` names the part of the evidence pack the quote
    came from. Both are optional because a stage may report a finding without either.
    """

    claim: str | None = None
    quote: str | None = None
    where: str | None = None
    severity: str | None = None


class AdminPriorSource(Model):
    """A candidate piece of prior art the originality stage considered.

    `published` is the source's own wording about its date, hedges included ("arXiv identifier
    suggests April 2026"), and is deliberately not parsed into a timestamp: a claim about a date is
    not a date, and turning one into the other here would launder a guess into a fact.

    `resolves_this_target` is the only load-bearing boolean. A source can be highly relevant and
    still not resolve the exact target, which is the distinction between prior art and context.
    """

    url: str | None = None
    title: str | None = None
    published: str | None = None
    date_evidence: str | None = None
    establishes: str | None = None
    resolves_this_target: bool | None = None
    correspondence: str | None = None


class AdminCitation(Model):
    """A page the model was served during a search. Never its text — see the module docstring."""

    url: str | None = None
    title: str | None = None
    retrieved_at: str | None = None


class AdminSearch(Model):
    """That the stage searched, and with what bounds.

    The search *prompt* is not here. It is our own instruction to the provider, not evidence about
    the submission, and it is long enough to bury the rest of the response.
    """

    id: str | None = None
    engine: str | None = None
    max_results: int | None = None


class AdminVerdict(Model):
    """What one model concluded, in its own words.

    The first five fields are common to every stage. The rest are stage-specific and null elsewhere:
    the four `*_reading`/`settled_portion`/`definitions_not_shown` fields belong to faithfulness, and
    `target_reading` through `catalogue_signal` to originality. They are one model rather than three
    because a reviewer reads one list of attempts, and because the promoted columns
    (`reason_code`, `confidence`, `summary`) are the same three columns whatever stage wrote them.

    `input_attempted_to_instruct` is the one field worth reading first: it reports that the submitted
    material addressed the reviewer. That is not a verdict about the mathematics, it is a signal that
    somebody wrote to the reviewing model, and the panel surfaces it above every other cell.
    """

    reason_code: str | None = None
    confidence: str | None = None
    summary: str | None = None
    findings: tuple[AdminFinding, ...] = ()
    input_attempted_to_instruct: bool | None = None

    informal_reading: str | None = None
    formal_reading: str | None = None
    settled_portion: str | None = None
    definitions_not_shown: tuple[str, ...] = ()

    target_reading: str | None = None
    searched_for: tuple[str, ...] = ()
    prior_sources: tuple[AdminPriorSource, ...] = ()
    catalogue_signal: str | None = None


class AdminUsage(Model):
    """Token counts, as the provider reported them. The money is `AdminStageAttempt.cost_usd`."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class AdminStageAttempt(Model):
    """One paid assessment: one stage, one model, one call.

    `status` is the first thing to read. `COMPLETED` carries a verdict; `SKIPPED` means the cascade
    stopped before reaching this stage, and `FAILED` means the stage was attempted and could not
    answer. Both of the latter carry `detail` and no verdict, and the difference between them
    matters: a skipped stage costs nothing and a failed one may already have been billed.

    `outcome` is derived from `reason_code` by the stage's policy and recorded at assessment time
    rather than re-derived here. It is not the model's choice — a formalization defect is an
    `APPROVE`, because the miner proved the task as published — and a client that re-derived it from
    the reason code would be reimplementing policy that has already moved once.
    """

    # `<stage>-<8 hex>`, and also the name of the archive directory holding the request, the
    # response and the attempt record: `assessments/<submission_id>/<key>`. Stable, so a reviewer
    # can cite one attempt and an operator can find its bytes.
    key: str
    submission_id: uuid.UUID
    # Which pass on this submission produced it. A second pass adds attempts rather than replacing
    # them, so the same stage can appear more than once with different ordinals.
    attempt: int
    stage: str
    stage_version: str
    status: str
    detail: str | None = None
    outcome: str | None = None
    model_requested: str
    model_served: str | None = None
    provider: str | None = None
    search: AdminSearch | None = None
    usage: AdminUsage | None = None
    # A decimal string, not a float: the column is `NUMERIC(12, 6)` and a float would quietly
    # discard the precision the column exists to keep. Null when nothing was billed.
    cost_usd: str | None = None
    citations: tuple[AdminCitation, ...] = ()
    verdict: AdminVerdict | None = None
    # The review policy in force when this ran, which is not necessarily the submission's current
    # one: a policy version can advance between an assessment and the human decision that cites it.
    review_policy_version: str
    started_at: datetime | None = None
    finished_at: datetime | None = None


class AdminReview(Model):
    """One submission awaiting a reward decision, with every assessment recorded against it.

    A subset of `InReviewResult` — same field names, same meanings — plus `attempts` and the current
    `manual_review_status`. The subset is the point: a reviewer needs the submission's identity, what
    it is against, and what the models said, and nothing here prices a bounty or names a payout.

    `attempts` is empty for a submission the advisory service has not reached yet. That is not a
    gap in the response: a submission is due for review the moment Lean verifies it, and a reviewer
    may decide it with no advisory input at all.
    """

    submission_id: uuid.UUID
    slug: str = Field(description="The conjecture this submission is against, as a stable slug")
    display_title: str
    task_id: str
    hotkey: str
    statement: str = Field(description="The elaborated Lean statement, from the catalog")
    task_bundle_sha256: str
    verified_at: datetime | None = None
    review_policy_version: str
    report_available: bool = False
    # Carried so the detail endpoint can be opened on an already-decided submission without the
    # reviewer having to guess. The queue itself lists only `UNREVIEWED`.
    manual_review_status: str
    attempts: tuple[AdminStageAttempt, ...] = ()


__all__ = [
    "AdminCitation",
    "AdminFinding",
    "AdminPriorSource",
    "AdminReview",
    "AdminSearch",
    "AdminStageAttempt",
    "AdminUsage",
    "AdminVerdict",
]
