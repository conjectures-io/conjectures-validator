"""Initial compression API contract; all weights are competition fractions."""

from typing import Literal
from pydantic import BaseModel


class Context(BaseModel):
    snapshot_id: str | None = None
    computed_at: str | None = None
    policy_version: str | None = None
    status: Literal["ready", "not_ready"] = "not_ready"
    freshness: Literal["current", "stale", "unknown"] = "unknown"
    freshness_reason: str | None = None


class Metrics(BaseModel):
    balanced_time_ratio: float | None = None
    mean_file_compression_pct: float | None = None
    total_compression_seconds: float | None = None
    lz77_seconds: float | None = None
    byte_weighted_compression_pct: float | None = None


class Stage(BaseModel):
    status: Literal["pending", "running", "passed", "failed", "stale", "unknown"]
    started_at: str | None = None
    finished_at: str | None = None
    reason_code: str | None = None
    message: str | None = None


class Admission(BaseModel):
    decision_id: str | None = None
    status: Literal["pending", "recorded", "invalid", "unknown"] = "pending"
    outcome: Literal["passed", "not_required", "inconclusive", "dominated", "excluded"] | None = (
        None
    )
    admitted: bool | None = None
    reason_code: str | None = None
    explanation: str | None = None
    reference_submission_id: str | None = None
    freshness: Literal["current", "stale", "unknown"] = "unknown"


class Score(BaseModel):
    snapshot_id: str
    on_frontier: bool
    pareto_weight: float
    improvement_weight: float
    combined_weight: float
    payable_weight: float
    payment_eligible: bool
    unpaid_reason: str | None = None


class Submission(BaseModel):
    id: str
    kind: Literal["miner", "baseline"]
    hotkey: str | None
    baseline_name: str | None
    submitted_at: str
    gate_status: Literal["queued", "running", "passed", "failed", "error", "unknown"]
    aggregation_id: str | None = None
    metrics: Metrics | None = None
    admission: Admission
    score: Score | None = None


class SubmissionPage(BaseModel):
    context: Context
    items: list[Submission]
    next_cursor: str | None = None


class TimingInterval(BaseModel):
    metric: str
    estimate: float
    lower: float
    upper: float
    confidence_level: float
    method: str
    draws: int


class Bounds(BaseModel):
    eligible: bool | None = None
    max_balanced_time_ratio: float | None = None
    max_mean_file_compression_pct: float | None = None
    observed_balanced_time_ratio: float | None = None
    observed_mean_file_compression_pct: float | None = None
    violations: list[str] = []


class ParetoPoint(Submission):
    frontier_order: int | None = None
    timing_interval: TimingInterval | None = None
    bounds: Bounds | None = None


class ParetoPage(BaseModel):
    context: Context
    axes: dict[str, str]
    bounds: Bounds
    items: list[ParetoPoint]
    next_cursor: str | None = None


class Ranking(BaseModel):
    rank: int
    hotkey: str
    submission_ids: list[str]
    pareto_weight: float
    improvement_weight: float
    combined_weight: float
    payable_weight: float


class Leaderboard(BaseModel):
    context: Context
    ranking: list[Ranking]
    next_cursor: str | None = None


class Weights(BaseModel):
    competition: str
    context: Context
    weights: dict[str, float]
    payable_competition_weight: float | None = None
    unpaid_competition_weight: float | None = None
    competition_share: float | None = None
    weight_set_id: str | None = None
    dry_run: bool | None = None
    chain_accepted: bool | None = None


class Accepted(BaseModel):
    competition: str
    submission: str
    state: str
    digest: str
    created: bool
    slots_remaining: int
    status_url: str


class Detail(BaseModel):
    submission: Submission
    digest: str
    source_sha256: str | None
    pipeline_observed_at: str
    pipeline: dict[str, Stage]
    current_evidence: dict[str, str | None]
    bounds: Bounds | None
    context: Context
    links: dict[str, str]


class DisplayInterval(BaseModel):
    lower: float
    upper: float
    confidence_level: float
    method: str
    sidedness: str


class SpeedTest(BaseModel):
    estimated_gain: float
    lower_confidence_bound: float
    confidence_level: float
    threshold: float
    comparison: str
    passed: bool
    method: str
    draws: int
    resampling: str
    file_count: int | None
    corpora: list[str]
    limitations: list[str]
    candidate_timing: TimingInterval | None = None
    reference_timing: TimingInterval | None = None
    gain_display_interval: DisplayInterval | None = None
    comparison_range: DisplayInterval | None = None
    candidate: dict | None = None
    reference: dict | None = None
    frontier_before: list[dict] = []


class AdmissionDecision(BaseModel):
    id: str | None
    submission_id: str
    created_at: str | None = None
    outcome: str
    admitted: bool
    reason_code: str | None
    explanation: str
    policy_version: str | None
    candidate_aggregation_id: str | None
    reference_submission_id: str | None
    reference_aggregation_id: str | None
    bounds: Bounds | None
    speed_test: SpeedTest | None


class AdmissionResponse(BaseModel):
    context: Context | None
    admission: Admission
    decision: AdmissionDecision | None


class Competition(BaseModel):
    slug: str
    name: str
    submissions_open: bool
    queue_depth: int
    current_snapshot_id: str | None
    description: str
    files: list[dict]
    metric_definitions: list[dict]
    policy: dict | None
    policy_status: str
    execution_limits: dict
    context: Context


class Index(BaseModel):
    items: list[Competition]
