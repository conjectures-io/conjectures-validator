"""Explicit BendTT public protocol. Database identifiers/counters never lose JS precision."""

from datetime import datetime
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def decimal(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError("expected decimal integer")
    if not str(value).isascii() or not str(value).isdigit() or not 0 <= int(value) < 2**63:
        raise ValueError("integer outside signed BIGINT range")
    return str(int(value))


Decimal = Annotated[str, BeforeValidator(decimal)]
Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ProofState = Literal["queued", "running", "passed", "rejected", "failed"]
ProcessingState = Literal[
    "queued", "proving", "waiting", "benchmarking", "completed", "rejected", "failed"
]
EvaluationState = Literal["pending", "completed", "superseded", "cancelled"]
Outcome = Literal["promoted", "not_promoted", "benchmark_failed", "inconclusive"]
RequestState = Literal["queued", "running", "completed", "failed", "cancelled"]
AttemptState = Literal["running", "completed", "failed", "expired", "cancelled"]
Role = Literal["baseline", "incumbent", "challenger"]
Side = Literal["candidate", "incumbent", "baseline", "infrastructure"]
MeasurementStatus = Literal[
    "completed",
    "timeout",
    "input-error",
    "resource-failure",
    "invalid-output",
    "invalid-observation",
    "invalid-timing",
    "budget-exhausted",
    "wrong-output",
    "affinity-mismatch",
    "diagnostic-mismatch",
]
Phase = Literal["benchmark", "probe_before", "probe_after"]
CheckerOutcome = Literal["accepted", "rejected", "exhausted", "parse-error"]
WeightStatus = Literal["planned", "dry_run", "submitted", "failed", "unknown", "skipped"]
Mode = Literal["subnet", "dev"]


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


T = TypeVar("T")


class Page(DTO, Generic[T]):
    items: list[T]
    next_cursor: str | None = None
    as_of: datetime


class Speedup(DTO):
    estimate: float
    lower: float
    upper: float
    confidence_level: Annotated[float, Field(ge=0.95, le=0.95)] = 0.95
    direction: Literal["higher_is_better"] = "higher_is_better"
    reference: Literal["incumbent", "baseline"] = "incumbent"


class Failure(DTO):
    phase: Literal["intake", "proof", "build", "benchmark", "scoring", "infrastructure"]
    kind: Literal[
        "policy_rejected",
        "proof_rejected",
        "timeout",
        "budget_exhausted",
        "resource_limit",
        "wrong_output",
        "invalid_observation",
        "lease_expired",
        "infrastructure",
        "unknown",
    ]
    side: Side | None = None
    limit_seconds: float | None = None
    elapsed_seconds: float | None = None
    message: str


class Scoring(DTO):
    metric: Literal["whole_request_wall_time"] = "whole_request_wall_time"
    aggregation: Literal["cost_tier_paired_bootstrap"] = "cost_tier_paired_bootstrap"
    minimum_time_reduction: float
    confidence_level: Annotated[float, Field(ge=0.95, le=0.95)] = 0.95
    require_speedup_lower_above: Annotated[float, Field(ge=1.0, le=1.0)] = 1.0
    regression_guard_enforced: bool
    maximum_regression: float


class Tier(DTO):
    id: str
    cases: int
    process_units: int
    rounds: int
    batch_size: int


class Limits(DTO):
    build_seconds_per_role: float
    run_seconds: float
    measurement_seconds: float
    attempt_seconds: float


class TargetPolicy(DTO):
    rounds: int | None = None
    process_units: int | None = None
    id: str
    weight: float
    machine_identity: Hash | None
    limits: Limits


class RewardPolicy(DTO):
    rank_shares: list[float]
    competition_share: float
    reign_full_days: float | None = None
    reign_zero_days: float | None = None


class Policy(DTO):
    profile_hash: Hash
    coordinator_snapshot: Hash
    git_commit: str | None
    git_dirty: bool | None
    proof_identity: Hash
    execution_identity: Hash
    scoring_identity: Hash
    manifest_sha256: Hash
    baseline_source_sha256: Hash
    source_policy: Literal["former_champions_only"]
    scoring: Scoring
    tiers: list[Tier]
    targets: list[TargetPolicy]
    rewards: RewardPolicy | None = None
    as_of: datetime | None = None


class Reference(DTO):
    promotion_id: Decimal | None
    submission_id: Decimal | None
    source_sha256: Hash
    shipped: bool = False


class Queue(DTO):
    queued: int = 0
    proving: int = 0
    waiting: int = 0
    benchmarking: int = 0


class File(DTO):
    name: Literal["Submission.lean"] = "Submission.lean"
    max_bytes: int


class Authentication(DTO):
    scheme: Literal["hotkey_signature_v2"] = "hotkey_signature_v2"
    registration_required: bool


class RegistrationPolicy(DTO):
    network: str
    netuid: int
    minimum_registered_block: Decimal


class Overview(DTO):
    slug: Literal["bendtt"] = "bendtt"
    name: Literal["BendTT checker"] = "BendTT checker"
    as_of: datetime
    status: Literal["ready", "not_ready"]
    submissions_open: bool
    intake_mode: Mode
    files: list[File]
    authentication: Authentication
    registration_policy: RegistrationPolicy
    profile_hash: Hash | None
    proof_identity: Hash | None
    scoring: Scoring | None
    queue: Queue
    champion: Reference | None
    baseline: Reference | None
    links: dict[str, str]


class Chain(DTO):
    network: str
    netuid: int
    observed_block: Decimal | None
    observed_at: datetime | None
    freshness: Literal["fresh", "stale", "unknown"]


class Registration(DTO):
    uid: int
    registered_block: Decimal
    used_by_submission: Decimal | None


class Eligibility(DTO):
    hotkey: str
    intake_mode: Mode
    eligible: bool
    reason: Literal[
        "eligible",
        "registration_bypassed",
        "not_registered",
        "registration_before_cutoff",
        "registration_used",
        "chain_unavailable",
        "submissions_closed",
    ]
    registration: Registration | None
    chain: Chain


class Accepted(DTO):
    competition: Literal["bendtt"] = "bendtt"
    submission: Decimal
    source_sha256: Hash
    proof_state: ProofState
    processing_state: ProcessingState
    intake_mode: Mode
    replayed: bool
    status_url: str


class ProofSummary(DTO):
    admission_id: Decimal | None
    identity: Hash | None
    state: ProofState
    started_at: datetime | None
    finished_at: datetime | None


class Decision(DTO):
    outcome: Outcome
    speedup: Speedup | None
    estimated_time_reduction: float | None
    guard_passed: bool | None
    reason: str


class EvaluationSummary(DTO):
    id: Decimal
    generation: int
    state: EvaluationState
    outcome: Outcome | None
    speedup: Speedup | None
    targets_completed: int
    targets_required: int
    profile_hash: Hash | None
    reference_promotion_id: Decimal | None
    created_at: datetime
    completed_at: datetime | None


class ChampionState(DTO):
    current: bool
    ever_promoted: bool


class SubmissionTiming(DTO):
    queue_seconds: float | None
    admission_seconds: float | None
    evaluation_seconds: float | None
    end_to_end_seconds: float | None
    elapsed_seconds: float


class Submission(DTO):
    id: Decimal
    hotkey: str | None
    label: str
    source_sha256: Hash
    submitted_at: datetime
    intake_mode: Mode
    processing_state: ProcessingState
    proof: ProofSummary
    evaluation: EvaluationSummary | None
    champion: ChampionState
    source_visibility: Literal["public", "withheld"]
    failure: Failure | None
    timing: SubmissionTiming
    links: dict[str, str]


class ProofPhase(DTO):
    attempt: int
    phase: str
    status: Literal["completed", "timeout", "failed"]
    started_at: datetime
    finished_at: datetime
    elapsed_seconds: float
    user_cpu_seconds: float | None
    system_cpu_seconds: float | None
    peak_rss_kib: int | None


class Proof(DTO):
    submission_id: Decimal
    admission_id: Decimal | None
    proof_identity: Hash | None
    state: ProofState
    reused: bool
    attempt_count: int
    contract: Literal["Pilot.Contract Submission.check"] = "Pilot.Contract Submission.check"
    source_sha256: Hash
    git_commit: str | None = None
    phases: list[ProofPhase]
    failure: Failure | None


class Progress(DTO):
    paired_runs_completed: int
    paired_runs_planned: int | None
    measurements_recorded: int
    measurements_planned: int | None


class Build(DTO):
    role: Role
    source_sha256: Hash
    status: Literal["completed", "timeout", "failed"]
    cache_hit: bool
    started_at: datetime
    finished_at: datetime
    elapsed_seconds: float
    peak_rss_kib: int | None
    artifact_sha256: Hash | None


class Attempt(DTO):
    id: Decimal
    request_id: Decimal
    evaluation_id: Decimal
    target: str
    number: int
    state: AttemptState
    stage: Literal["preparing", "benchmarking", "finished", "unknown"]
    started_at: datetime
    finished_at: datetime | None
    last_progress_at: datetime | None
    lease: Literal["valid", "expired", "not_applicable"]
    builds: list[Build]
    progress: Progress
    failure: Failure | None
    links: dict[str, str]


class Request(DTO):
    id: str
    request_id: Decimal
    state: RequestState
    attempts: int
    completed_attempt_id: Decimal | None
    last_progress_at: datetime | None
    progress: Progress
    speedup: Speedup | None
    failure: Failure | None


class Evaluation(DTO):
    id: Decimal
    submission_id: Decimal
    generation: int
    state: EvaluationState
    profile_hash: Hash | None
    coordinator_snapshot: Hash
    admission_id: Decimal
    incumbent: Reference
    baseline_source_sha256: Hash
    created_at: datetime
    completed_at: datetime | None
    policy: Scoring | None
    decision: Decision | None
    targets: list[Request]
    links: dict[str, str]


class Diagnostic(DTO):
    available: Literal[False] = False
    truncated: Literal[False] = False
    text: None = None


class Measurement(DTO):
    id: Decimal
    attempt_id: Decimal
    phase: Phase
    round_index: int
    case_id: str
    group: str
    role: Role
    input_sha256: Hash
    status: MeasurementStatus
    started_at: datetime
    finished_at: datetime
    wall_ns: Decimal | None
    cpu_ns: Decimal | None
    check_and_render_ns: Decimal | None
    parse_ns: Decimal | None
    sandbox_seconds: float
    peak_rss_kib: int | None
    output_bytes: Decimal | None
    returncode: int
    checker_outcome: CheckerOutcome | None
    diagnostic: Diagnostic = Field(default_factory=Diagnostic)
    members_recorded: int
    links: dict[str, str]


class Member(DTO):
    index: int
    case_id: str
    input_sha256: Hash
    parse_ns: Decimal
    check_and_render_ns: Decimal
    output_bytes: Decimal
    checker_outcome: Literal["accepted", "rejected", "exhausted"]
    diagnostic: Diagnostic = Field(default_factory=Diagnostic)


class Champion(DTO):
    promotion_id: Decimal
    submission_id: Decimal
    hotkey: str | None
    source_sha256: Hash
    promoted_at: datetime
    current: bool
    evaluation_id: Decimal
    profile_hash: Hash | None
    speedup_over_predecessor: Speedup | None
    source_visibility: Literal["public", "withheld"]


class Quality(DTO):
    status: Literal["pending", "usable", "inconclusive-environment"]
    good_probe_fraction: float | None
    probes_recorded: int
    probes_planned: int | None


class BaselineComparison(DTO):
    challenger_speedup: float | None
    incumbent_speedup: float | None
    method: Literal["unpaired_per_unit_medians_weighted_geometric_mean"] = (
        "unpaired_per_unit_medians_weighted_geometric_mean"
    )
    paired: Literal[False] = False
    confidence_interval: None = None


class AttemptTiming(DTO):
    queue_seconds: float | None
    build_seconds: float | None
    benchmark_seconds: float | None
    attempt_seconds: float | None


class TargetReport(DTO):
    id: str
    attempt_id: Decimal | None
    speedup: Speedup | None
    quality: Quality
    baseline_comparison: BaselineComparison
    timing: AttemptTiming
    failure: Failure | None


class Baseline(DTO):
    source_sha256: Hash
    telemetry_only: Literal[True] = True


class Report(DTO):
    submission_id: Decimal
    evaluation_id: Decimal | None = None
    evaluation: None = None
    complete: bool = False
    decision: Decision | None = None
    baseline: Baseline | None = None
    targets: list[TargetReport] = Field(default_factory=list)
    links: dict[str, str] = Field(default_factory=dict)


class CorpusSource(DTO):
    project: str
    url: str | None


class CorpusCase(DTO):
    id: str
    title: str
    group: str
    tier: str
    rounds: int
    input_sha256: Hash
    expected_outcome: Literal["accepted", "rejected"]
    source: CorpusSource
    description: str


class CorpusPage(Page[CorpusCase]):
    profile_hash: Hash


class WeightSet(DTO):
    id: Decimal
    mode: Literal["dry_run", "live"]
    status: WeightStatus
    block: Decimal
    epoch_block: Decimal
    champion_promotion_id: Decimal | None
    snapshot_sha256: Hash
    created_at: datetime
    finished_at: datetime | None
    reason: str
    links: dict[str, str]


class Allocation(DTO):
    uid: int
    hotkey: str
    weight: float
    role: Literal["miner", "treasury"]


class Rewards(DTO):
    as_of: datetime
    policy: RewardPolicy | None
    latest_weight_set: WeightSet | None
    measured_emission: None = None
    links: dict[str, str]


class TargetHealth(DTO):
    id: str
    activity: Literal["busy", "recent", "stale", "unknown"]
    last_contact_at: datetime | None


class Health(DTO):
    status: Literal["ready", "degraded", "not_ready"]
    as_of: datetime
    database: Literal["up", "down"]
    coordinator: Literal["recent", "stale", "unknown"]
    chain: Literal["fresh", "stale", "unknown", "not_required"]
    active_evaluation_id: Decimal | None
    targets: list[TargetHealth]
    submissions_open: bool
