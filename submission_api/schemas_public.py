"""Response models for the world-readable surface.

Separate from `schemas.py` on purpose. Those models answer a miner who authenticated with a
hotkey signature and is reading their own submission; these are served to anyone, with no
credential at all, and are rendered by a public website. The two sets have different rules, and
mixing them in one module is how a field that belongs to one ends up on the other.

**A result names its solver's hotkey; nothing here reaches their money.** The hotkey is
published on `PublicResult`, `InReviewResult` and `PublicSolution` by product decision — a result
is credited to the hotkey that produced it. The paying coldkey, the payment reference and the
extrinsic remain absent from every model on this surface, and that is the boundary still enforced
structurally: a hotkey is the public identity a miner signs with, while those three lead to the
funds behind it. Per-task activity is still published as salted pseudonyms, but they are only as
strong as the solver's absence from the results feed — see `conjectures_subnet.db.public.activity`.

**Proof bytes are published only for an approved submission, and verifier output never is.**
`Main.lean` is served by `PublicSolution`, and only once review has approved the submission —
the gate lives in `conjectures_subnet.db.public.accepted_solution`, so a row that is merely
Lean-verified is listed on the feed with no proof to fetch. Verifier output remains withheld at
every state: the public report is built by *allowlisting* fields rather than by removing
`stdout_tail` and `stderr_tail`, so a field added to `VerificationReport` later is withheld by
default instead of published by accident.

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
    reward_target_id: str
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
    """The live estimate for this reward target, or why it is no longer available."""

    amount_rao: int | None = Field(
        description="Live Subnet Alpha estimate in base units; null when unavailable."
    )
    amount_usd: str | None = Field(
        description=(
            "Current USD display estimate from TaoStats; null with amount_rao or when the "
            "external rate is unavailable."
        )
    )
    policy_version: str
    available: bool
    reason: str = Field(
        description=(
            "OPEN | CLAIM_HELD | ALREADY_SOLVED | NOT_IN_BOUNTY_POOL | WITHDRAWN. "
            "WITHDRAWN means the conjecture has left the pool; see `retirement`"
        )
    )
    as_of: datetime
    locked: bool = Field(
        default=False,
        description="Always false: submission acceptance does not reserve this amount.",
    )


class BountyPoolInfo(Model):
    """The common inputs behind every live task estimate."""

    policy_version: str
    balance_rao: int
    balance_usd: str | None = Field(
        description=(
            "Current USD display value of balance_rao from TaoStats; null when the external "
            "rate is unavailable."
        )
    )
    wallet_coldkey: str
    wallet_hotkey: str
    netuid: int
    asset: str = Field(description="alpha")
    open_targets: int
    total_age_weight: int
    constant_numerator: int
    constant_denominator: int
    as_of: datetime
    locked_at_submission: bool = False


SLUG_DESCRIPTION = (
    "Stable public identity, derived from the theorem's reward target. Unchanged by a pin "
    "rotation, so it is safe to link, bookmark and cite. Not the same string as `task_id`"
)


class ConjectureTask(Model):
    """One task issued against a conjecture: one attack direction, at one pinned revision.

    A conjecture is issued as one task per mode — `formalized` to prove it, `counterexample` to
    refute it — and a solver commits to `task_id` and `task_bundle_sha256` in a bundle. Both are
    seeded with the pinned source revision and change on every rotation, which is exactly why
    they are fields here rather than the conjecture's name.
    """

    task_id: str = Field(
        description="What a submission bundle names. Changes on every pin rotation"
    )
    task_mode: str = Field(description="formalized | counterexample")
    task_bundle_sha256: str
    attempts: int = Field(
        description="Paid verification attempts recorded against this task alone"
    )


class ConjectureSummary(Model):
    """One conjecture as it appears in a list. No Lean source, no full statement.

    One entry per conjecture, not per task: the two attack directions are folded into `tasks`.
    """

    slug: str = Field(description=SLUG_DESCRIPTION)
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
    task_modes: tuple[str, ...] = Field(
        description="The directions this conjecture can be attacked from"
    )
    tier: str
    ams_subjects: tuple[int, ...]
    is_open: bool = Field(
        description="True when no formal proof is known upstream, which is what makes it a bounty"
    )
    problem_id: str = Field(
        description="The pinned per-revision identity of this conjecture. Moves on a rotation"
    )
    reward_target_id: str = Field(
        description="The stable bounty identity `slug` is derived from. One payout per target"
    )
    tasks: tuple[ConjectureTask, ...]
    bounty: BountyInfo
    attempts: int = Field(
        description="Paid verification attempts against this conjecture in either direction"
    )


class ConjectureTaskDetail(ConjectureTask):
    """One task in full: the Lean a solver compiles against and the contract it is checked by.

    `challenge_lean` is the exact `Challenge.lean` whose bytes are hashed into
    `machine_contract.task_bundle_sha256`, so a reader can verify the published statement against
    the published commitment without trusting this response.
    """

    challenge_lean: str
    machine_contract: MachineContract | None = Field(
        default=None,
        description=(
            "The contract a submission is checked against; null on a retired conjecture, whose "
            "bundle no longer exists and which therefore has nothing to submit against"
        ),
    )


class RetirementInfo(Model):
    """Why a conjecture stopped accepting submissions, and where that was decided.

    Present only on a retired conjecture. Its presence — not a status string — is what tells a
    client the target is closed; `bounty.reason` reports `WITHDRAWN` for the same reason, so a
    client reading only the bounty still gets a correct answer.
    """

    retired_on: str = Field(description="ISO date the target was withdrawn from the pool")
    reason_code: str = Field(
        description=(
            "The retirement code, e.g. SOLVED, SOURCE_MISMATCH + EXPLOITABLE. Not a reward "
            "review outcome: a target can close without any submission having been rewarded"
        )
    )
    reason: str = Field(description="The recorded reason, code and detail together")
    decision_url: str | None = Field(
        default=None,
        description=(
            "The published reward-review decision behind this retirement, when one exists. A "
            "dependency or audit retirement has none"
        ),
    )
    recovered_from_commit: str = Field(
        description=(
            "The task-repository commit that deleted the bundles. The statement and Lean served "
            "here were recovered from its parent and checked against the digest the bundle's own "
            "manifest published"
        )
    )


class ConjectureDetail(Model):
    """One conjecture in full, with every task issued against it.

    The statement, docstring, category and classification are properties of the conjecture and
    are published once. The Lean source and the machine contract are properties of a *task* — one
    per attack direction — and live under `tasks`.
    """

    slug: str = Field(description=SLUG_DESCRIPTION)
    title: str = Field(description="The fully-qualified source theorem; an identifier, not prose")
    statement: str
    summary: str | None = None
    category: str
    classification: str
    task_modes: tuple[str, ...]
    tier: str
    ams_subjects: tuple[int, ...]
    is_open: bool
    problem_id: str
    reward_target_id: str
    source_theorem: str
    source_module: str
    source_path: str
    supported_modes: tuple[str, ...]
    references: tuple[Reference, ...]
    tasks: tuple[ConjectureTaskDetail, ...]
    bounty: BountyInfo
    retirement: RetirementInfo | None = Field(
        default=None,
        description=(
            "Set when this conjecture has been withdrawn from the pool. The page stays readable "
            "so results and attribution earned against it remain citable, but no submission is "
            "accepted: every task reports a null `machine_contract`"
        ),
    )
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


class ConjectureVariantRef(Model):
    """One direction of one variant conjecture, as a pointer into the catalog.

    A `slug` rather than a `task_id`: the point of the index is to be linkable, and a task id
    moves on every pin rotation. `GET /v1/catalog/conjectures/{slug}` is where the statement, the
    Lean and the machine contract live, which is why none of them are repeated here.

    One row per attack direction, so a variant that can be both proved and refuted appears twice
    with the same slug and a different `task_mode`. That is the pairing a solver picks: a slug on
    its own does not say which direction has a task issued against it.
    """

    slug: str = Field(description=SLUG_DESCRIPTION)
    task_mode: str = Field(description="formalized | counterexample")
    retired: bool = Field(
        default=False,
        description=(
            "True when this variant has been withdrawn from the pool. Its page stays readable and "
            "the results earned against it stay citable, but nothing can be submitted against it. "
            "Filter on this to get the directions still worth attempting"
        ),
    )


class ConjectureIndexEntry(Model):
    """One upstream problem, and every pooled variant of it.

    Coarser than `ConjectureSummary`, which is one row per conjecture. Upstream formalises a
    problem as a root theorem plus any number of `.variants.*` siblings, each of which is its own
    reward target — so Erdős 1 is eight conjectures and one problem. This is the problem-level
    view, for a caller building a table of contents rather than a bounty list.

    Retired conjectures appear here, flagged, at both levels. That is the one place this surface
    differs from `/v1/catalog/conjectures`, which lists only what may be submitted against: a
    problem that has left the pool is still part of what the pool has covered, and its page is
    still readable. `retired` is what keeps that from reading as an offer.
    """

    slug: str = Field(description=SLUG_DESCRIPTION)
    source_theorem: str = Field(
        description="The fully-qualified theorem at `slug`; an identifier, not prose"
    )
    erdos_problem_number: int | None = Field(
        default=None,
        description=(
            "The erdosproblems.com problem number, read from the source module. Null for every "
            "other collection in the pool — Wikipedia, OEIS, the Millennium problems — which is "
            "an ordinary thing for a conjecture to be rather than missing data. Not unique and "
            "not a key: one problem is often formalised as several independent root theorems in "
            "one module, so many entries share a number. `slug` is the identity"
        ),
    )
    qualifier: str | None = Field(
        default=None,
        description=(
            "Which variant `slug` names, when the problem is represented by one. Null when the "
            "root theorem itself is in the pool; set when only variants are, in which case the "
            "best-precedence variant stands in for the problem"
        ),
    )
    retired: bool = Field(
        default=False,
        description=(
            "True when the conjecture at `slug` has been withdrawn from the pool. Describes that "
            "one conjecture and never the family: a problem whose root is retired can still hold "
            "submittable variants, so do not filter problems on this — read `variants[].retired`"
        ),
    )
    variants: tuple[ConjectureVariantRef, ...] = Field(
        default=(),
        description=(
            "The family's other members, live and retired alike, one row per attack direction. "
            "Never includes `slug` itself, so a problem with no pooled variants reports an empty "
            "list"
        ),
    )


class ConjectureIndexResponse(Model):
    """Every problem in the pool, in one response.

    Unpaginated on purpose. It is a few hundred rows of identifiers with no statements, no Lean
    and no database read, and a table of contents that arrives in pages is not one — a caller
    would have to reassemble it before it was usable. `repository_commit` names the pinned
    revision the index describes, so a client can tell one rotation's index from the next.
    """

    total: int = Field(description="Problems in the pool; the length of `items`")
    items: tuple[ConjectureIndexEntry, ...]
    repository_commit: str


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
    bounty: BountyPoolInfo
    pins: tuple[PinInfo, ...]
    pins_sha256: str


# --- Activity ------------------------------------------------------------------------------


class PublicActivityItem(Model):
    """One anonymised event on a conjecture.

    `solver` is `HMAC(activity_salt, reward_target_id || hotkey)`, truncated — stable within a
    conjecture so repeat attempts are visibly the same solver, and unlinkable across conjectures
    because the conjecture's identity is inside the MAC. Keyed on the reward target rather than
    on a task id for two reasons: a solver who attempts both directions of one conjecture must
    appear as one solver on its page, and a pin rotation must not silently rename everybody.
    `occurred_at` is truncated to the hour:
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


class PublicReviewDecision(Model):
    """The binding review decision and its deliberately public rationale.

    Internal reviewer notes, reviewer identity, and raw agent evidence are absent by
    construction. ``notes_public`` is null for historical or automatic decisions that did not
    record a publishable explanation.
    """

    decision: str = Field(description="APPROVED | REJECTED")
    reason_code: str
    notes_public: str | None = None
    policy_version: str
    decided_at: datetime


class PublicResult(Model):
    """One submission on a public feed, at whatever state it has reached.

    Named for its original use — the certified feed — but the shape every result endpoint answers
    with, including `GET /v1/results/submissions`, which lists every submission whether it is
    queued, rejected, in review, or paid out. The three `*_status` fields are how a client tells
    those apart; the timestamps below say when, and are null until the state they name is reached.

    Credited to the `hotkey` that submitted it. Nothing here reaches that miner's funds: no
    paying coldkey, no payment reference, no extrinsic.
    """

    id: uuid.UUID
    hotkey: str = Field(
        description="The hotkey that submitted this proof, as an SS58 address"
    )
    verification_status: str = Field(
        description=(
            "Where Lean got to: UNVERIFIED (queued or running), VERIFIED, or REJECTED"
        )
    )
    manual_review_status: str = Field(
        description=(
            "The reward decision on a Lean-verified proof: UNREVIEWED, APPROVED, or REJECTED. "
            "APPROVED covers both a human approval and the recorded automatic decision when "
            "manual review is disabled"
        )
    )
    reward_status: str = Field(
        description=(
            "Where the payout got to: INELIGIBLE, ELIGIBLE (owed, not yet paid), REWARDED "
            "(confirmed on chain), or FAILED"
        )
    )
    slug: str = Field(
        description=(
            "The conjecture this result is against, as a stable slug. Derived from the row's own "
            "reward target, so a result produced under an earlier pin still links to the current "
            "conjecture page"
        )
    )
    task_id: str = Field(
        description="The task this result was produced against, at the pin then in force"
    )
    title: str
    statement: str
    task_bundle_sha256: str
    attribution: str = ATTRIBUTION
    verified_at: datetime | None = Field(
        default=None,
        description=(
            "When the verifier finished with this submission, whatever the verdict. Set for a "
            "rejected submission too — Lean ran and reached one; verification_status says which. "
            "Null only while no run has finished"
        ),
    )
    certified_at: datetime | None = Field(
        default=None, description="When the payout for this result was confirmed on chain"
    )
    bounty_amount_rao: int
    bounty_amount_usd: str | None = Field(
        description=(
            "Current USD display value of bounty_amount_rao from TaoStats; null when the "
            "external rate is unavailable."
        )
    )
    bounty_policy_version: str
    verifier_version: str | None = None
    sandbox_mode: str | None = None
    report_available: bool = Field(
        default=False,
        description=(
            "Whether the verifier report is published for this result, at "
            "GET /v1/results/{id}/report. True for every Lean-verified submission, whatever "
            "manual review later decides; false for queued or Lean-rejected rows"
        ),
    )
    review: PublicReviewDecision | None = Field(
        default=None,
        description=(
            "Latest binding review and its public rationale; null while review is pending. "
            "Present on a review-rejected row too — the rationale is the point of publishing it"
        ),
    )
    solution_available: bool = Field(
        default=False,
        description=(
            "Whether the proof is published for this result, at "
            "GET /v1/results/{id}/solution. True once review has approved the submission, so a "
            "result that is Lean-verified but still in review is listed here with no solution"
        ),
    )


class InReviewResult(Model):
    """Lean-verified and awaiting manual review.

    No proof file, and no report digest: the proof has passed the kernel but not the reward
    decision, and publishing the artifact before that decision would hand a pending result to
    anyone who wanted to resubmit it elsewhere.
    """

    id: uuid.UUID
    hotkey: str = Field(
        description="The hotkey that submitted this proof, as an SS58 address"
    )
    slug: str = Field(description="The conjecture this result is against, as a stable slug")
    task_id: str
    title: str
    statement: str
    task_bundle_sha256: str
    attribution: str = ATTRIBUTION
    verified_at: datetime | None = None
    review_policy_version: str
    report_available: bool = False


class PublicSolution(Model):
    """The proof that closed a conjecture, as the miner submitted it.

    Published only for an approved submission. Served as text rather than as the original zip
    bundle: the bundle also carries the manifest the miner sent, and the proof is what a reader
    wants — `Main.lean` is guaranteed UTF-8 and NUL-free by `verifier.submission`, so it survives
    a JSON string intact.

    Credited to the `hotkey` that submitted it, alongside the site attribution.
    """

    id: uuid.UUID
    hotkey: str = Field(
        description="The hotkey that submitted this proof, as an SS58 address"
    )
    slug: str = Field(description="The conjecture this proof closes, as a stable slug")
    filename: str = Field(description="The path the bytes occupy in the verified bundle")
    source: str = Field(description="The Lean source, verbatim as verified")
    proof_sha256: str = Field(
        description=(
            "The digest of these exact bytes. Published here because the proof itself is now "
            "public, so it can no longer be used to test an unpublished candidate for prior "
            "submission — the reason it is withheld from the verification report"
        )
    )
    byte_length: int
    attribution: str = ATTRIBUTION


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
    "BountyPoolInfo",
    "ConjectureActivity",
    "ConjectureDetail",
    "ConjectureListResponse",
    "ConjectureSummary",
    "ConjectureTask",
    "ConjectureTaskDetail",
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
    "PublicReviewDecision",
    "PublicSolution",
    "PublicVerificationReport",
    "QueueDepths",
    "Reference",
    "RetirementInfo",
]
