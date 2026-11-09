"""Public TorchLean reads; proof, measurements and promotion are separate states."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

TorchLeanProofState = Literal["queued", "running", "proved", "rejected"]
TorchLeanProcessingState = Literal[
    "queued", "proving", "waiting", "benchmarking", "completed", "rejected", "disqualified"
]


class TorchLeanJobCounts(BaseModel):
    queued: int = 0
    running: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0


class TorchLeanSideRuns(BaseModel):
    finished: int
    mean_seconds: float


class TorchLeanTimeout(BaseModel):
    """How an entry ran out of time. A measured result, not an error: the outcome is `timed-out`."""

    phase: str | None = Field(None, description="`benchmark`, or `build` for a native build")
    limit: str | None = Field(
        None,
        description="`invocation`: one run passed its own cap (`run_seconds`); `budget`: the paired "
        "rounds used up the shared `benchmark_seconds`, and the entry's runs took longer on average",
    )
    running: str | None = Field(None, description="The side that was running when time ran out")
    allowance_seconds: float | None = None
    budget_seconds: float | None = None
    elapsed_seconds: float | None = None
    stopped_after_seconds: float | None = Field(
        None, description="How long the stopped run had gone: a lower bound on that run's time"
    )
    runs: dict[str, TorchLeanSideRuns] | None = Field(
        None,
        description="Per side, the paired runs that finished and their mean time; a budget timeout "
        "is blamed by time per run, not by total, as one side may have finished one run more",
    )
    cell: str | None = Field(None, description="The `<workload>|<cell>` that was running")
    cells_finished: int | None = None
    cells_total: int | None = None
    environment: str | None = None
    seed: int | None = None
    rounds_completed: int | None = None
    rounds_planned: int | None = None


class TorchLeanAdmission(BaseModel):
    outcome: str | None = None
    score: float | None = None
    gain_lower: float | None = None
    guard_passed: bool | None = None
    reason: str | None = None
    # Decisions recorded before timeouts were kept say only what the reason did.
    timeout: TorchLeanTimeout | None = None
    suite_scores: dict[str, float] | None = Field(
        None,
        description="Per benchmark suite (`synthetic`, `real`), the weighted time ratio against the "
        "reference; `score` combines them by the plan's `suite_weights`. Null where the "
        "competition times one synthetic suite only",
    )


class TorchLeanEvaluation(BaseModel):
    id: int
    board: str
    generation: int = Field(
        description="Retry/replanning generation of this submission on this board"
    )
    state: Literal["pending", "completed", "superseded"]
    reference_promotion_id: int | None
    reference_hash: str
    config_hash: str
    created_at: datetime
    completed_at: datetime | None
    decision: TorchLeanAdmission | None
    jobs: TorchLeanJobCounts


class TorchLeanSubmission(BaseModel):
    id: int
    hotkey: str
    source_sha256: str
    submitted_at: datetime
    proof_state: TorchLeanProofState
    processing_state: TorchLeanProcessingState
    stage: str | None
    reason: str | None
    intake_mode: Literal["subnet", "dev"]
    source_available: bool
    evaluations: list[TorchLeanEvaluation]


class TorchLeanSubmissionPage(BaseModel):
    """Newest first. `next_cursor` fetches the next page and is null on the last one."""

    submissions: list[TorchLeanSubmission]
    next_cursor: str | None = None


class TorchLeanEnvironment(BaseModel):
    id: str
    weight: float
    identity: str | None = None


class TorchLeanReference(BaseModel):
    promotion_id: int | None
    submission: int | None
    source_sha256: str
    shipped: bool


class TorchLeanBoard(BaseModel):
    id: str
    config_hash: str | None
    models: dict[str, float]
    reward_share: float | None
    promotion_bar: float | None
    guard_limit: float | None
    environments: list[TorchLeanEnvironment]
    current_promotion_id: int | None
    paused: bool | None
    pause_reason: str | None


class TorchLeanBoards(BaseModel):
    configuration_hash: str | None
    publication_hash: str | None
    boards: list[TorchLeanBoard]


class TorchLeanHealth(BaseModel):
    ok: bool
    policy_ready: bool
    queued: int
    proving: int
    waiting: int
    benchmarking: int
    competition_share: float | None = Field(
        default=None,
        description="Unknown until the competition publishes its validator-weight share",
    )
    rank_shares: list[float] | None
    paused_policy: str | None
    publication_hash: str | None


class TorchLeanChallenger(BaseModel):
    submission: int
    hotkey: str
    source_sha256: str
    evaluation_id: int
    generation: int
    state: str
    ratio: float | None
    admission: str | None
    completed_at: datetime | None
    promotion_id: int | None


class TorchLeanLeaderboard(BaseModel):
    board: str
    config_hash: str | None
    configuration_hash: str | None
    reference: TorchLeanReference
    challengers: list[TorchLeanChallenger]


class TorchLeanChampion(BaseModel):
    promotion_id: int
    generation: int
    config_hash: str
    submission: int
    hotkey: str
    source_sha256: str
    ratio: float | None
    promoted_at: datetime
    current: bool
    disqualified_at: datetime | None
    source_disqualified: bool
    reward_rank: int | None
    rank_share: float | None
    competition_fraction: float | None
    source_available: bool


class TorchLeanChampions(BaseModel):
    board: str
    active: bool
    publication_hash: str | None
    champions: list[TorchLeanChampion]


class TorchLeanObservation(BaseModel):
    reference_ns: float
    candidate_ns: float
    reference_checksum: int
    candidate_checksum: int


class TorchLeanCellTime(BaseModel):
    ns: float
    checksum: int


class TorchLeanRound(BaseModel):
    reference_first: bool
    cells: dict[str, TorchLeanObservation]


class TorchLeanWorkload(BaseModel):
    # One of a seed's workloads under the multi-seed plan; cells are keyed "<id>|<cell>".
    id: str
    seed: int
    profile: str
    weight: float


class TorchLeanBaselineCell(BaseModel):
    """One cell timed with the shipped baseline kernels: a fixed yardstick, never a score input."""

    baseline_median_ns: float | None = None
    baseline_repetitions: int | None = None
    baseline_ns_per_request: float | None = None
    candidate_median_ns_per_request: float | None = None
    reference_median_ns_per_request: float | None = None
    candidate_to_baseline: float | None = None
    reference_to_baseline: float | None = None


class TorchLeanBaseline(BaseModel):
    source_sha256: str | None = Field(None, description="The baseline `Kernel.lean` timed")
    cells: dict[str, TorchLeanBaselineCell] = Field(
        default_factory=dict, description="Per `<workload>|<cell>`, unpaired medians"
    )


class TorchLeanRealWorkloadResult(BaseModel):
    """Medians over the paired rounds of one real model on its real samples."""

    family: str | None = None
    model: str | None = None
    dataset: str | None = None
    samples_per_run: int | None = None
    candidate_median_ns: float | None = None
    reference_median_ns: float | None = None
    candidate_to_reference: float | None = None
    baseline_median_ns: float | None = None
    candidate_to_baseline: float | None = None


class TorchLeanRealFamily(BaseModel):
    candidate_to_reference: float | None = Field(
        None, description="Equal-weight geometric mean over the family's real workloads"
    )
    candidate_to_baseline: float | None = None
    workloads: list[str] = Field(default_factory=list)


class TorchLeanRealSuite(BaseModel):
    workloads: dict[str, TorchLeanRealWorkloadResult]
    families: dict[str, TorchLeanRealFamily]


class TorchLeanRealWorkload(BaseModel):
    """A real model and dataset the plan times besides the synthetic cells."""

    id: str
    family: str
    model: str
    dataset: str
    adapter: str | None = None
    asset_revision: str | None = None
    weights_sha256: str | None = None
    samples_sha256: str | None = None
    sample_count: int | None = None
    samples_per_run: int | None = None


class TorchLeanMeasurement(BaseModel):
    environment: str
    seed: int
    state: str
    attempts: int
    started_at: datetime | None
    completed_at: datetime | None
    error_kind: str | None
    # Explicit fields only: the raw worker payload may contain host paths or claim metadata.
    identity: str | None
    repetitions: dict[str, int] | None
    # Null for single-workload plans, which time each cell once per round.
    workloads: list[TorchLeanWorkload] | None = None
    rounds: list[TorchLeanRound] | None
    score: float | None
    guards: dict[str, float] | None
    # A failed job's evidence: `repetitions` and `rounds` above are then what it had completed.
    timeout: TorchLeanTimeout | None = None
    calibration: dict[str, float] | None = None
    round_in_progress: dict[str, dict[str, TorchLeanCellTime]] | None = Field(
        None, description="Per side, the cells of the round that was stopped"
    )
    round_in_progress_samples: dict[str, list[dict[str, TorchLeanCellTime]]] | None = Field(
        None,
        description="Per side, the cells of each input of the stopped round that the side had "
        "finished, in order. A benchmark that times every input separately reports the stopped "
        "round here instead of in `round_in_progress`",
    )
    finished: dict[str, TorchLeanCellTime] | None = Field(
        None, description="The cells the stopped run had finished"
    )
    evidence_truncated: bool = Field(
        False, description="The worker's evidence was too large to keep"
    )
    shared: bool = Field(
        False,
        description="The job timed this entry once for several boards (a comparison bundle); "
        "these are the observations against this board's reference",
    )
    suite_scores: dict[str, float] | None = None
    real_family_scores: dict[str, float] | None = None
    baseline: TorchLeanBaseline | None = None
    real: TorchLeanRealSuite | None = None


class TorchLeanEvaluationReport(TorchLeanEvaluation):
    cells: dict[str, float]
    timing: dict[str, float]
    admission_policy: dict[str, float]
    measurements: list[TorchLeanMeasurement]
    suites: list[str] | None = Field(
        None, description="The benchmark suites scored, e.g. `synthetic` and `real`"
    )
    suite_weights: dict[str, float] | None = None
    real_workloads: list[TorchLeanRealWorkload] | None = None
    baseline_sha256: str | None = Field(
        None, description="The baseline `Kernel.lean` each job also times, for reference only"
    )


class TorchLeanReport(BaseModel):
    submission: TorchLeanSubmission
    evaluations: list[TorchLeanEvaluationReport]
