"""Response models for the world-readable surface.

Separate from `schemas.py` on purpose. Those models answer a miner who authenticated with a
hotkey signature and is reading their own submission; these are served to anyone, with no
credential at all, and are rendered by a public website. The two sets have different rules, and
mixing them in one module is how a field that belongs to one ends up on the other.

**Nothing on this surface may identify a miner.** Not the hotkey, not the paying coldkey, not
the payment reference, not the extrinsic. A verified result is attributed to conjectures.io;
there is no author credit. Per-task activity is published as salted, per-task pseudonyms, so a
reader can see that four distinct solvers attempted a conjecture without learning who, and
cannot join those pseudonyms across conjectures.

**Nothing on this surface may carry proof bytes or verifier output.** `Main.lean` is never
served publicly, in-review results carry no proof file, and the public verification report is
built by *allowlisting* fields rather than by removing `stdout_tail` and `stderr_tail` — so a
field added to `VerificationReport` later is withheld by default instead of published by
accident.

`extra="forbid"` and `frozen=True` are inherited from the same `Model` base the miner surface
uses, so a typo in a field name fails at construction rather than silently serialising nothing.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


ItemT = TypeVar("ItemT")


# --- Pagination ----------------------------------------------------------------------------


class CursorPage(Model, Generic[ItemT]):
    """One page of a keyset-paginated feed.

    `next_cursor` is opaque and signed; see `submission_api/pagination.py`. It is null exactly
    when there is no further page, so a client loops until it is null rather than comparing
    counts. There is deliberately no total: counting a growing feed on every page read is a
    table scan an anonymous caller should not be able to ask for.
    """

    items: tuple[ItemT, ...]
    next_cursor: str | None = None


# --- Catalog -------------------------------------------------------------------------------


class Reference(Model):
    """A pointer to where the conjecture is stated, as the source declaration records it.

    `label` is the human text and `url` the link, split apart here because the catalog stores
    them as one Markdown string and a website should not have to parse Markdown to render a
    link. `url` is null when the source gave no link.
    """

    label: str
    url: str | None = None


class MachineContract(Model):
    """Everything a solver needs to produce an admissible bundle, and nothing else.

    This is the machine-readable half of a conjecture: the exact identifiers the proof must
    define, the axioms it may depend on, the imports it may not use, and the limits it is
    checked against. It is a copy of the audited manifest, so a solver that satisfies it offline
    is checked against the same values here.
    """

    task_id: str
    reward_family_id: str
    task_bundle_sha256: str
    target_type_sha256s: tuple[str, ...]
    bundle_format: str
    task_mode: str
    classification: str
    challenge_module: str
    solution_module: str
    target_theorem: str
    theorem_names: tuple[str, ...]
    definition_names: tuple[str, ...]
    permitted_axioms: tuple[str, ...]
    forbidden_dependencies: tuple[str, ...]
    timeout_seconds: int
    max_submission_bytes: int
    max_bundle_bytes: int
    adapter_version: int
    answer_policy: dict[str, Any] = Field(default_factory=dict)


class BountyInfo(Model):
    """What a verified proof of this conjecture is currently quoted at.

    Indicative, not a promise: the amount is frozen onto a submission when it is accepted, and
    this is the rule's answer as of now. `policy_version` names the rule so a reader can tell a
    reprice from a rule change.
    """

    amount_rao: int = Field(
        description="Alpha base units. Not the TAO rao of `submission_price_rao`."
    )
    policy_version: str


class ConjectureSummary(Model):
    """One conjecture as it appears in a list. No Lean source, no full statement."""

    slug: str
    title: str = Field(
        description=(
            "The fully-qualified source theorem. An identifier a mathematician can cite, not "
            "prose — no human title exists upstream, and `summary` is the readable half"
        )
    )
    statement: str = Field(
        description="The formal statement, pretty-printed by Lean from the source declaration"
    )
    summary: str | None = Field(
        default=None, description="The source docstring: the conjecture in words"
    )
    category: str
    classification: str
    task_mode: str
    tier: str
    ams_subjects: tuple[int, ...]
    is_open: bool = Field(
        description="True when no formal proof is known upstream, which is what makes it a bounty"
    )
    task_bundle_sha256: str
    bounty: BountyInfo
    attempts: int = Field(description="Paid verification attempts recorded against this task")


class ConjectureDetail(Model):
    """One conjecture in full: the statement, the Lean the solver compiles against, and the
    machine contract it is checked by.

    `challenge_lean` is the exact `Challenge.lean` whose bytes are hashed into
    `machine_contract.task_bundle_sha256`, so a reader can verify the published statement
    against the published commitment without trusting this response.
    """

    slug: str
    title: str = Field(description="The fully-qualified source theorem; an identifier, not prose")
    statement: str
    summary: str | None = None
    category: str
    classification: str
    task_mode: str
    tier: str
    ams_subjects: tuple[int, ...]
    is_open: bool
    source_theorem: str
    source_module: str
    source_path: str
    supported_modes: tuple[str, ...]
    references: tuple[Reference, ...]
    challenge_lean: str
    machine_contract: MachineContract
    bounty: BountyInfo
    submission_price_rao: int = Field(
        description="TAO rao for one verification attempt; one credit"
    )
    attempts: int
    repository_commit: str
    pins: tuple["PinInfo", ...]


class FacetValue(Model):
    value: str
    count: int


class Facet(Model):
    """One filterable dimension and how many conjectures fall in each of its values.

    Counts are computed over the result set *before* that facet's own filter is applied, which
    is what makes a facet list usable: selecting `category=research open` must not collapse the
    category facet to a single row.
    """

    field: str
    values: tuple[FacetValue, ...]


class ConjectureListResponse(Model):
    total: int = Field(description="Conjectures matching the filters, before paging")
    items: tuple[ConjectureSummary, ...]
    facets: tuple[Facet, ...]
    repository_commit: str
    limit: int
    offset: int


class PinInfo(Model):
    component: str
    repository: str | None = None
    commit: str | None = None
    toolchain: str | None = None
    version: str | None = None
    enabled: bool | None = None


class PoolMeta(Model):
    """Pool-wide metadata: what is on offer, what an attempt costs, and what verifies it."""

    repository_commit: str
    bundle_format: str
    conjectures: int
    open_conjectures: int
    tiers: tuple[FacetValue, ...]
    task_modes: tuple[FacetValue, ...]
    categories: tuple[FacetValue, ...]
    # One credit buys one verification attempt, which is what the miner-facing intake charges.
    # Named as a credit here because that is the word the website uses.
    credit_price_rao: int
    credits_per_attempt: int
    treasury_address: str = Field(
        description="The payment recipient an attempt is paid to, on finalized chain state"
    )
    max_bundle_bytes: int
    bounty: BountyInfo
    pins: tuple[PinInfo, ...]
    pins_sha256: str


# --- Activity ------------------------------------------------------------------------------


class PublicActivityItem(Model):
    """One anonymised event on a conjecture.

    `solver` is `HMAC(activity_salt, task_id || hotkey)`, truncated — stable within a
    conjecture so repeat attempts are visibly the same solver, and unlinkable across
    conjectures because the task id is inside the MAC. `occurred_at` is truncated to the hour:
    a precise timestamp is joinable against the public chain, where the funding transfer for
    that attempt is visible with its sender, which would undo the pseudonym.
    """

    event: str = Field(description="attempt | verified | rejected | certified")
    occurred_at: datetime
    solver: str


class ConjectureActivity(Model):
    slug: str
    attempts: int
    solvers: int
    verified: int
    certified: int
    items: tuple[PublicActivityItem, ...]


# --- Results -------------------------------------------------------------------------------

ATTRIBUTION = "conjectures.io"


class PublicResult(Model):
    """A certified result: Lean-verified, human-approved, and paid out.

    Attributed to conjectures.io. There is no author credit, and no field here can be joined
    back to the miner who submitted it.
    """

    id: uuid.UUID
    slug: str
    title: str
    statement: str
    task_bundle_sha256: str
    attribution: str = ATTRIBUTION
    verified_at: datetime | None = None
    certified_at: datetime | None = Field(
        default=None, description="When the payout for this result was confirmed on chain"
    )
    bounty_amount_rao: int
    bounty_policy_version: str
    verifier_version: str | None = None
    sandbox_mode: str | None = None
    report_available: bool = False


class InReviewResult(Model):
    """Lean-verified and awaiting manual review.

    No proof file, and no report digest: the proof has passed the kernel but not the reward
    decision, and publishing the artifact before that decision would hand a pending result to
    anyone who wanted to resubmit it elsewhere.
    """

    id: uuid.UUID
    slug: str
    title: str
    statement: str
    task_bundle_sha256: str
    attribution: str = ATTRIBUTION
    verified_at: datetime | None = None
    review_policy_version: str
    report_available: bool = False


class PublicVerificationReport(Model):
    """The verifier's own report, reduced to what may be published.

    Built by allowlist in `submission_api/routers/results.py`. `stdout_tail` and `stderr_tail`
    are excluded because Lean's output quotes the submitted proof back, and
    `submission_sha256` is excluded because `submissions.proof_digest` is globally unique —
    publishing it would let anyone test a candidate proof for prior submission.
    """

    id: uuid.UUID
    slug: str
    report_sha256: str
    report: dict[str, Any]


# --- System --------------------------------------------------------------------------------


class QueueDepths(Model):
    awaiting_verification: int
    awaiting_review: int
    awaiting_reward: int


class PinRotationWindow(Model):
    """The weekly drain-and-rotate window from README.md's pin policy.

    Configured, not inferred: only the operator knows when they take submissions down. `drained`
    is the precondition the policy states — no submission queued, awaiting review, or awaiting
    reward — so a reader can see whether the next window is able to start.
    """

    weekday: int = Field(description="Monday is 0, Sunday is 6, in UTC")
    starts_at: datetime
    ends_at: datetime
    in_progress: bool
    drained: bool


class SystemStatus(Model):
    status: str = Field(description="ok | degraded | paused")
    submissions_open: bool
    repository_commit: str
    queue_depths: QueueDepths
    pin_rotation: PinRotationWindow
    banner: str | None = None


__all__ = [
    "ATTRIBUTION",
    "BountyInfo",
    "ConjectureActivity",
    "ConjectureDetail",
    "ConjectureListResponse",
    "ConjectureSummary",
    "CursorPage",
    "Facet",
    "FacetValue",
    "InReviewResult",
    "MachineContract",
    "Model",
    "PinInfo",
    "PinRotationWindow",
    "PoolMeta",
    "PublicActivityItem",
    "PublicResult",
    "PublicVerificationReport",
    "QueueDepths",
    "Reference",
]
