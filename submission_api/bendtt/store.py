"""Bounded relational reads and atomic intake; no access to worker artifact paths."""

from dataclasses import dataclass
from datetime import datetime
from typing import TypeVar

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from submission_api import schemas_bendtt as s
from submission_api.errors import ApiError, NotFound, ServiceUnavailable
from submission_api.settings import Settings
from submission_api.bendtt.pagination import Window, page

BASE = "/v1/competitions/bendtt"
M = TypeVar("M", bound=BaseModel)


def number(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("invalid database integer")
    return value


def moment(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("invalid database timestamp")
    return value


def string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid database string")
    return value


async def rows(
    session: AsyncSession, query: str, params: dict[str, object] | None = None
) -> list[dict[str, object]]:
    result = await session.execute(text(query), params or {})
    return [dict(row) for row in result.mappings()]


async def compatible(session: AsyncSession) -> None:
    if (
        await session.scalar(
            text(
                "SELECT to_regclass('public.api_runtime') IS NOT NULL AND to_regprocedure('public.api_enqueue(text,text,text,uuid,boolean)') IS NOT NULL"
            )
        )
        is not True
    ):
        raise ServiceUnavailable(
            "Apply BendTT migration 0003 and API grants",
            reason_code="COMPETITION_SCHEMA_UNAVAILABLE",
        )
    await session.execute(text("SELECT competition_id FROM api_runtime LIMIT 0"))


def speedup(row: dict[str, object]) -> s.Speedup | None:
    if any(row.get(key) is None for key in ("speedup_estimate", "speedup_lower", "speedup_upper")):
        return None
    return s.Speedup.model_validate(
        dict(
            estimate=row["speedup_estimate"], lower=row["speedup_lower"], upper=row["speedup_upper"]
        )
    )


def failure(phase: str, kind: str, side: object = None) -> s.Failure:
    # Operator-owned messages only. Raw reason/stdout may contain private source or paths.
    return s.Failure.model_validate(
        dict(phase=phase, kind=kind, side=side, message=f"{phase}: {kind}")
    )


PROGRESS_QUERY = (
    "SELECT r.id,e.submission_id,e.id AS evaluation_id,r.target,"
    "e.completed_at AS evaluated_at,e.outcome='promoted' AS promoted,"
    "e.profile_hash,e.baseline_source_sha256,"
    "CASE WHEN r.state='completed' THEN coalesce(t.challenger_baseline_speedup,"
    "CASE WHEN EXISTS(SELECT 1 FROM api_builds b WHERE "
    "b.attempt_id=r.completed_attempt_id AND b.role='incumbent' "
    "AND b.status='completed' AND b.source_sha256=e.baseline_source_sha256) "
    "THEN r.speedup_estimate END) END AS baseline_speedup,"
    "CASE WHEN r.state<>'completed' THEN 'unavailable' "
    "WHEN t.challenger_baseline_speedup IS NOT NULL THEN 'telemetry' "
    "WHEN r.speedup_estimate IS NOT NULL AND EXISTS(SELECT 1 FROM api_builds b "
    "WHERE b.attempt_id=r.completed_attempt_id AND b.role='incumbent' "
    "AND b.status='completed' AND b.source_sha256=e.baseline_source_sha256) "
    "THEN 'direct_baseline' ELSE 'unavailable' END AS method "
    "FROM api_requests r JOIN api_evaluations e ON e.id=r.evaluation_id "
    "LEFT JOIN api_target_summaries t ON t.request_id=r.id "
    "WHERE e.competition_id=:c AND e.state='completed'"
)

@dataclass(frozen=True, slots=True)
class Store:
    session: AsyncSession
    settings: Settings
    competition: int
    now: datetime
    policy: s.Policy | None
    observed_at: datetime | None

    @classmethod
    async def open(cls, session: AsyncSession, settings: Settings) -> "Store":
        await compatible(session)
        found = await rows(
            session,
            "SELECT c.id,r.observed_at,p.policy FROM competitions c LEFT JOIN api_runtime r ON r.competition_id=c.id LEFT JOIN api_profiles p ON p.profile_hash=r.profile_hash WHERE c.slug=:slug",
            dict(slug=settings.bendtt_competition),
        )
        if not found:
            raise ServiceUnavailable("BendTT is not initialized", reason_code="POLICY_NOT_READY")
        now = moment(await session.scalar(text("SELECT transaction_timestamp()")))
        row = found[0]
        policy = s.Policy.model_validate(row["policy"]) if row["policy"] is not None else None
        return cls(
            session,
            settings,
            number(row["id"]),
            now,
            policy,
            None if row["observed_at"] is None else moment(row["observed_at"]),
        )

    @property
    def dev(self) -> bool:
        return self.settings.bendtt_dev_skip_registration

    async def get(self, kind: str, ident: int) -> dict[str, object]:
        queries = {
            "submission": "SELECT * FROM api_submissions WHERE id=:id AND competition_id=:c",
            "evaluation": "SELECT * FROM api_evaluations WHERE id=:id AND competition_id=:c",
            "attempt": "SELECT * FROM api_attempts WHERE id=:id AND competition_id=:c",
            "measurement": "SELECT m.* FROM api_measurements m JOIN api_attempts a ON a.id=m.attempt_id WHERE m.id=:id AND a.competition_id=:c",
            "weight-set": "SELECT * FROM api_weight_sets WHERE id=:id AND competition_id=:c",
        }
        result = await rows(self.session, queries[kind], dict(id=ident, c=self.competition))
        if not result:
            raise NotFound("BendTT resource not found")
        return result[0]

    async def intake_config(self) -> dict[str, object]:
        found = await rows(
            self.session,
            "SELECT * FROM api_intake_settings WHERE competition_id=:c",
            dict(c=self.competition),
        )
        if not found:
            raise ServiceUnavailable(
                "API intake has not been provisioned", reason_code="POLICY_NOT_READY"
            )
        row = found[0]
        expected = dict(
            dev_mode=self.dev,
            network=self.settings.bendtt_chain_network,
            netuid=self.settings.bendtt_chain_netuid,
            minimum_registered_block=self.settings.bendtt_min_registered_block,
            max_chain_age_seconds=self.settings.bendtt_max_chain_age_seconds,
            max_source_bytes=self.settings.bendtt_max_source_bytes,
        )
        if any(row[key] != value for key, value in expected.items()) or (
            self.dev and self.settings.production
        ):
            raise ServiceUnavailable(
                "API/database intake configuration mismatch", reason_code="POLICY_NOT_READY"
            )
        return row

    async def chain(self) -> s.Chain:
        found = await rows(
            self.session,
            "SELECT * FROM api_chain WHERE network=:n AND netuid=:u",
            dict(n=self.settings.bendtt_chain_network, u=self.settings.bendtt_chain_netuid),
        )
        row = found[0] if found else None
        age = None if row is None else (self.now - moment(row["observed_at"])).total_seconds()
        return s.Chain.model_validate(
            dict(
                network=self.settings.bendtt_chain_network,
                netuid=self.settings.bendtt_chain_netuid,
                observed_block=None if row is None else row["block"],
                observed_at=None if row is None else row["observed_at"],
                freshness="unknown"
                if age is None
                else "fresh"
                if -5 <= age <= self.settings.bendtt_max_chain_age_seconds
                else "stale",
            )
        )

    async def eligibility(self, hotkey: str) -> s.Eligibility:
        cfg = await self.intake_config()
        result = await self.session.scalar(
            text("SELECT api_eligibility(:slug,:hotkey)"),
            dict(hotkey=hotkey, slug=self.settings.bendtt_competition),
        )
        if not isinstance(result, dict) or result.get("error"):
            raise ServiceUnavailable("Intake policy unavailable", reason_code="POLICY_NOT_READY")
        if (
            not self.settings.bendtt_submissions_enabled
            or self.settings.submissions_paused
            or not cfg["enabled"]
        ):
            result = dict(reason="submissions_closed")
        return s.Eligibility.model_validate(
            dict(
                hotkey=hotkey,
                intake_mode="dev" if self.dev else "subnet",
                eligible=result["reason"] in ("eligible", "registration_bypassed"),
                reason=result["reason"],
                registration=result.get("registration"),
                chain=await self.chain(),
            )
        )

    async def enqueue(self, hotkey: str, content: str, request_id: str) -> tuple[int, bool]:
        await self.intake_config()
        result = await self.session.scalar(
            text("SELECT api_enqueue(:slug,:hotkey,:content,CAST(:request AS uuid),:dev)"),
            dict(
                slug=self.settings.bendtt_competition,
                hotkey=hotkey,
                content=content,
                request=request_id,
                dev=self.dev,
            ),
        )
        if not isinstance(result, dict):
            raise ServiceUnavailable("Intake returned invalid receipt")
        if code := result.get("error"):
            statuses = {
                "NOT_REGISTERED": 402,
                "NO_ENTITLEMENT": 402,
                "REGISTRATION_BEFORE_CUTOFF": 402,
                "IDEMPOTENCY_CONFLICT": 409,
                "SUBMISSIONS_CLOSED": 403,
                "SUBMISSION_TOO_LARGE": 413,
                "MALFORMED_REQUEST": 400,
            }
            code = string(code)
            raise ApiError(
                "BendTT upload refused: " + code,
                status_code=statuses.get(code, 503),
                reason_code=code,
            )
        await self.session.commit()
        return int(string(result["submission"])), result["replayed"] is True

    async def proof_row(self, sub: dict[str, object]) -> dict[str, object] | None:
        found = await rows(
            self.session,
            "SELECT a.* FROM api_evaluations e JOIN api_admissions a ON a.id=e.admission_id WHERE e.submission_id=:id ORDER BY e.id DESC LIMIT 1",
            dict(id=sub["id"]),
        )
        if found:
            return found[0]
        if self.policy is None:
            return None
        found = await rows(
            self.session,
            "SELECT * FROM api_admissions WHERE source_sha256=:source AND proof_identity=:identity",
            dict(source=sub["source_sha256"], identity=self.policy.proof_identity),
        )
        return found[0] if found else None

    async def evaluation_summary(self, row: dict[str, object]) -> s.EvaluationSummary:
        completed = await self.session.scalar(
            text("SELECT count(*) FROM api_requests WHERE evaluation_id=:id AND state='completed'"),
            dict(id=row["id"]),
        )
        targets = row["expected_targets"]
        if not isinstance(targets, list):
            raise ValueError("invalid expected targets")
        return s.EvaluationSummary.model_validate(
            dict(
                id=row["id"],
                generation=row["generation"],
                state=row["state"],
                outcome=row["outcome"],
                speedup=speedup(row),
                targets_completed=completed,
                targets_required=len(targets),
                profile_hash=row["profile_hash"],
                reference_promotion_id=row["incumbent_promotion_id"],
                created_at=row["created_at"],
                completed_at=row["completed_at"],
            )
        )

    async def submission(self, row: dict[str, object]) -> s.Submission:
        proof = await self.proof_row(row)
        evaluations = await rows(
            self.session,
            "SELECT * FROM api_evaluations WHERE submission_id=:id ORDER BY id DESC LIMIT 1",
            dict(id=row["id"]),
        )
        evaluation = evaluations[0] if evaluations else None
        state = "queued"
        failed = None
        if proof is not None:
            if proof["state"] == "running":
                state = "proving"
            elif proof["state"] == "rejected":
                state, failed = "rejected", failure("proof", "proof_rejected")
            elif proof["state"] == "failed":
                state, failed = "failed", failure("infrastructure", "infrastructure")
            elif proof["state"] == "passed":
                state = "waiting"
        if evaluation is not None and evaluation["state"] == "completed":
            state = "completed"
        elif evaluation is not None and evaluation["state"] == "pending":
            active = await self.session.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM api_requests WHERE evaluation_id=:id AND state='running')"
                ),
                dict(id=evaluation["id"]),
            )
            state = "benchmarking" if active else "waiting"
        start = moment(row["submitted_at"])
        finish = (
            evaluation["completed_at"]
            if evaluation is not None and evaluation["state"] == "completed"
            else proof["finished_at"]
            if proof is not None and proof["state"] in ("rejected", "failed")
            else None
        )
        first = (
            evaluation["created_at"]
            if evaluation is not None
            else proof["started_at"]
            if proof is not None
            else None
        )

        def duration(a: object, b: object) -> float | None:
            return (
                None
                if a is None or b is None
                else max(0.0, (moment(b) - moment(a)).total_seconds())
            )

        benchmark_seconds = None
        if evaluation is not None:
            measured = await rows(
                self.session,
                "SELECT max(greatest(0,extract(epoch FROM (a.finished_at-b.finished_at)))) "
                "AS seconds FROM api_requests r JOIN LATERAL "
                "(SELECT * FROM api_attempts WHERE request_id=r.id ORDER BY id DESC LIMIT 1) "
                "a ON true JOIN LATERAL "
                "(SELECT max(finished_at) AS finished_at,count(*) AS builds "
                "FROM api_builds WHERE attempt_id=a.id AND status='completed') b ON true "
                "WHERE r.evaluation_id=:id AND a.finished_at IS NOT NULL AND b.builds=3 "
                "AND a.finished_at>=b.finished_at",
                dict(id=evaluation["id"]),
            )
            if measured and measured[0]["seconds"] is not None:
                benchmark_seconds = float(str(measured[0]["seconds"]))
        return s.Submission.model_validate(
            dict(
                id=row["id"],
                hotkey=row["hotkey"],
                label=row["label"],
                source_sha256=row["source_sha256"],
                submitted_at=start,
                intake_mode="dev" if row["intake"] == "dev" else "subnet",
                processing_state=state,
                proof=dict(
                    admission_id=None if proof is None else proof["id"],
                    identity=self.policy.proof_identity
                    if proof is None and self.policy
                    else None
                    if proof is None
                    else proof["proof_identity"],
                    state="queued" if proof is None else proof["state"],
                    started_at=None if proof is None else proof["started_at"],
                    finished_at=None if proof is None else proof["finished_at"],
                ),
                evaluation=None
                if evaluation is None
                else await self.evaluation_summary(evaluation),
                champion=dict(current=row["current"], ever_promoted=row["ever_promoted"]),
                source_visibility="public" if row["source_available"] else "withheld",
                failure=failed,
                timing=dict(
                    benchmark_seconds=benchmark_seconds,
                    queue_seconds=duration(start, max(start, moment(first)))
                    if first is not None
                    else None,
                    admission_seconds=None if proof is None else proof["elapsed_seconds"],
                    evaluation_seconds=None
                    if evaluation is None
                    else duration(evaluation["created_at"], evaluation["completed_at"]),
                    end_to_end_seconds=duration(start, finish),
                    elapsed_seconds=max(
                        0.0,
                        ((self.now if finish is None else moment(finish)) - start).total_seconds(),
                    ),
                ),
                links={
                    key: f"{BASE}/submissions/{row['id']}/{key}"
                    for key in ("proof", "evaluations", "report", "source")
                },
            )
        )

    async def proof(self, sub: dict[str, object]) -> s.Proof:
        row = await self.proof_row(sub)
        phases = (
            []
            if row is None
            else await rows(
                self.session,
                "SELECT attempt_number AS attempt,phase,status,started_at,finished_at,elapsed_seconds,user_cpu_seconds,system_cpu_seconds,peak_rss_kib FROM api_phases WHERE admission_id=:id ORDER BY attempt_number,started_at LIMIT 1000",
                dict(id=row["id"]),
            )
        )
        return s.Proof.model_validate(
            dict(
                submission_id=sub["id"],
                admission_id=None if row is None else row["id"],
                proof_identity=self.policy.proof_identity
                if row is None and self.policy
                else None
                if row is None
                else row["proof_identity"],
                state="queued" if row is None else row["state"],
                reused=False
                if row is None or row["started_at"] is None
                else moment(row["started_at"]) < moment(sub["submitted_at"]),
                attempt_count=0 if row is None else row["attempt_count"],
                source_sha256=sub["source_sha256"],
                phases=phases,
                failure=None
                if row is None or row["state"] not in ("rejected", "failed")
                else failure(
                    "proof", "proof_rejected" if row["state"] == "rejected" else "infrastructure"
                ),
            )
        )

    async def profile(self, digest: str | None) -> s.Policy | None:
        if digest is None:
            return None
        found = await rows(
            self.session,
            "SELECT policy FROM api_profiles WHERE profile_hash=:p AND competition_id=:c",
            dict(p=digest, c=self.competition),
        )
        return s.Policy.model_validate(found[0]["policy"]) if found else None

    async def progress(self, attempt: int, evaluation: int, target: str) -> s.Progress:
        counts = await rows(
            self.session,
            "SELECT count(*) AS total,max(finished_at) AS last FROM api_measurements WHERE attempt_id=:id",
            dict(id=attempt),
        )
        pair = await self.session.scalar(
            text(
                "SELECT count(*) FROM (SELECT round_index,case_id FROM api_measurements WHERE attempt_id=:id AND phase='benchmark' AND status='completed' AND role IN ('challenger','incumbent') GROUP BY round_index,case_id HAVING count(DISTINCT role)=2) q"
            ),
            dict(id=attempt),
        )
        ev = await self.get("evaluation", evaluation)
        policy = await self.profile(
            None if ev["profile_hash"] is None else string(ev["profile_hash"])
        )
        pairs = total = None
        if policy is not None and policy.tiers:
            pairs = sum(t.process_units * t.rounds for t in policy.tiers)
            target_policy = next((v for v in policy.targets if v.id == target), None)
            if target_policy is not None:
                total = (
                    2 * pairs
                    + 3 * sum(t.process_units for t in policy.tiers)
                    + 2 * max(t.rounds for t in policy.tiers)
                )
        if policy is not None and not policy.tiers:
            selected = next((v for v in policy.targets if v.id == target), None)
            if (
                selected is not None
                and selected.rounds is not None
                and selected.process_units is not None
            ):
                pairs = selected.rounds * selected.process_units
                total = 2 * pairs + 3 * selected.process_units + 2 * selected.rounds
        return s.Progress(
            paired_runs_completed=number(pair),
            paired_runs_planned=pairs,
            measurements_recorded=number(counts[0]["total"]),
            measurements_planned=total,
        )

    async def attempt(self, row: dict[str, object]) -> s.Attempt:
        builds = await rows(
            self.session,
            "SELECT role,source_sha256,status,cache_hit,started_at,finished_at,elapsed_seconds,peak_rss_kib,artifact_sha256 FROM api_builds WHERE attempt_id=:id ORDER BY role",
            dict(id=row["id"]),
        )
        last = await self.session.scalar(
            text("SELECT max(finished_at) FROM api_measurements WHERE attempt_id=:id"),
            dict(id=row["id"]),
        )
        if last is None:
            last = await self.session.scalar(
                text("SELECT max(finished_at) FROM api_builds WHERE attempt_id=:id"),
                dict(id=row["id"]),
            )
        running = row["state"] == "running"
        bad = next((v for v in builds if v["status"] != "completed"), None)
        failed = (
            failure(
                "build",
                "timeout" if bad["status"] == "timeout" else "unknown",
                "candidate" if bad["role"] == "challenger" else bad["role"],
            )
            if bad
            else failure("infrastructure", "lease_expired")
            if row["state"] == "expired"
            else failure("benchmark", "unknown", row["failure_kind"])
            if row["state"] == "failed"
            else None
        )
        if row["state"] == "failed" and bad is None:
            observations = await rows(
                self.session,
                "SELECT status,sandbox_seconds FROM api_measurements WHERE attempt_id=:id AND status<>'completed' ORDER BY id DESC LIMIT 1",
                dict(id=row["id"]),
            )
            if observations:
                status = string(observations[0]["status"])
                kind = {
                    "timeout": "timeout",
                    "budget-exhausted": "budget_exhausted",
                    "resource-failure": "resource_limit",
                    "wrong-output": "wrong_output",
                    "diagnostic-mismatch": "wrong_output",
                }.get(status, "invalid_observation")
                failed = failure("benchmark", kind, row["failure_kind"]).model_copy(
                    update=dict(elapsed_seconds=observations[0]["sandbox_seconds"])
                )
        return s.Attempt.model_validate(
            dict(
                id=row["id"],
                request_id=row["request_id"],
                evaluation_id=row["evaluation_id"],
                target=row["target"],
                number=row["attempt_number"],
                state=row["state"],
                stage="finished"
                if not running
                else "benchmarking"
                if len(builds) == 3 and all(v["status"] == "completed" for v in builds)
                else "preparing",
                started_at=row["started_at"],
                finished_at=row["finished_at"],
                last_progress_at=last,
                lease="not_applicable"
                if not running
                else "valid"
                if moment(row["lease_expires_at"]) > self.now
                else "expired",
                builds=builds,
                progress=await self.progress(
                    number(row["id"]), number(row["evaluation_id"]), string(row["target"])
                ),
                failure=failed,
                links=dict(measurements=f"{BASE}/attempts/{row['id']}/measurements"),
            )
        )

    async def evaluation(self, row: dict[str, object]) -> s.Evaluation:
        reference = s.Reference(
            promotion_id=None,
            submission_id=None,
            source_sha256=string(row["baseline_source_sha256"]),
            shipped=True,
        )
        if row["incumbent_promotion_id"] is not None:
            found = await rows(
                self.session,
                "SELECT id AS promotion_id,submission_id,source_sha256 FROM api_promotions WHERE id=:id AND competition_id=:c",
                dict(id=row["incumbent_promotion_id"], c=self.competition),
            )
            if not found:
                raise ServiceUnavailable("Missing pinned incumbent")
            reference = s.Reference.model_validate(found[0])
        requests = await rows(
            self.session,
            "SELECT * FROM api_requests WHERE evaluation_id=:id ORDER BY target",
            dict(id=row["id"]),
        )
        targets = []
        for request in requests:
            attempts = await rows(
                self.session,
                "SELECT * FROM api_attempts WHERE request_id=:id ORDER BY id DESC LIMIT 1",
                dict(id=request["id"]),
            )
            count = await self.session.scalar(
                text("SELECT count(*) FROM api_attempts WHERE request_id=:id"),
                dict(id=request["id"]),
            )
            attempt = None if not attempts else await self.attempt(attempts[0])
            targets.append(
                s.Request.model_validate(
                    dict(
                        id=request["target"],
                        request_id=request["id"],
                        state=request["state"],
                        attempts=count,
                        completed_attempt_id=request["completed_attempt_id"],
                        last_progress_at=None if attempt is None else attempt.last_progress_at,
                        progress=dict(
                            paired_runs_completed=0,
                            paired_runs_planned=None,
                            measurements_recorded=0,
                            measurements_planned=None,
                        )
                        if attempt is None
                        else attempt.progress,
                        speedup=speedup(request),
                        failure=None if attempt is None else attempt.failure,
                    )
                )
            )
        score = speedup(row)
        decision = (
            None
            if row["outcome"] is None
            else s.Decision.model_validate(
                dict(
                    outcome=row["outcome"],
                    speedup=score,
                    estimated_time_reduction=None if score is None else 1 - 1 / score.estimate,
                    guard_passed=row["guard_passed"],
                    reason=f"Recorded coordinator outcome: {row['outcome']}",
                )
            )
        )
        policy = await self.profile(
            None if row["profile_hash"] is None else string(row["profile_hash"])
        )
        return s.Evaluation.model_validate(
            dict(
                id=row["id"],
                submission_id=row["submission_id"],
                generation=row["generation"],
                state=row["state"],
                profile_hash=row["profile_hash"],
                coordinator_snapshot=row["coordinator_snapshot_sha256"],
                admission_id=row["admission_id"],
                incumbent=reference,
                baseline_source_sha256=row["baseline_source_sha256"],
                created_at=row["created_at"],
                completed_at=row["completed_at"],
                policy=None if policy is None else policy.scoring,
                decision=decision,
                targets=targets,
                links={
                    key: f"{BASE}/evaluations/{row['id']}/{key}" for key in ("report", "attempts")
                },
            )
        )

    async def report(self, row: dict[str, object]) -> s.Report:
        evaluation = await self.evaluation(row)
        targets = []
        for request in evaluation.targets:
            attempts = await rows(
                self.session,
                "SELECT * FROM api_attempts WHERE request_id=:id ORDER BY id DESC LIMIT 1",
                dict(id=int(request.request_id)),
            )
            attempt = None if not attempts else await self.attempt(attempts[0])
            summaries = await rows(
                self.session,
                "SELECT * FROM api_target_summaries WHERE request_id=:id",
                dict(id=int(request.request_id)),
            )
            summary = summaries[0] if summaries else {}
            quality = s.Quality.model_validate(
                dict(
                    status=summary.get("quality_status", "pending"),
                    good_probe_fraction=summary.get("good_probe_fraction"),
                    probes_recorded=summary.get("probes_recorded", 0),
                    probes_planned=summary.get("probes_planned"),
                )
            )
            native = None
            build = None
            if attempt is not None:
                build = sum(v.elapsed_seconds for v in attempt.builds)
                # Full worker processing includes supervisor/launch overhead; benchmark is
                # the interval after all role builds, not just the sum of inner clocks.
                if attempt.finished_at is not None and len(attempt.builds) == 3:
                    native = max(
                        0.0,
                        (
                            attempt.finished_at - max(v.finished_at for v in attempt.builds)
                        ).total_seconds(),
                    )
            targets.append(
                s.TargetReport(
                    id=request.id,
                    attempt_id=None if attempt is None else attempt.id,
                    speedup=request.speedup,
                    quality=quality,
                    baseline_comparison=s.BaselineComparison.model_validate(
                        dict(
                            challenger_speedup=summary.get("challenger_baseline_speedup"),
                            incumbent_speedup=summary.get("incumbent_baseline_speedup"),
                        )
                    ),
                    timing=s.AttemptTiming(
                        queue_seconds=None
                        if attempt is None
                        else max(0.0, (attempt.started_at - evaluation.created_at).total_seconds()),
                        build_seconds=build,
                        benchmark_seconds=native,
                        attempt_seconds=None
                        if attempt is None or attempt.finished_at is None
                        else (attempt.finished_at - attempt.started_at).total_seconds(),
                    ),
                    failure=request.failure,
                )
            )
        return s.Report(
            submission_id=evaluation.submission_id,
            evaluation_id=evaluation.id,
            complete=evaluation.state == "completed",
            decision=evaluation.decision,
            baseline=s.Baseline(source_sha256=evaluation.baseline_source_sha256),
            targets=targets,
            links=evaluation.links,
        )

    async def collection(
        self,
        kind: str,
        parent: int | None,
        filters: dict[str, object],
        limit: int,
        cursor: str | None,
        after: int | None = None,
    ) -> Window:
        queries = {
            "submissions": "SELECT * FROM api_submissions WHERE competition_id=:c",
            "evaluations": "SELECT * FROM api_evaluations WHERE competition_id=:c AND submission_id=:parent",
            "attempts": "SELECT * FROM api_attempts WHERE competition_id=:c AND evaluation_id=:parent",
            "measurements": "SELECT m.* FROM api_measurements m JOIN api_attempts a ON a.id=m.attempt_id WHERE a.competition_id=:c AND m.attempt_id=:parent",
            "members": "SELECT mm.* FROM api_members mm JOIN api_measurements m ON m.id=mm.measurement_id JOIN api_attempts a ON a.id=m.attempt_id WHERE a.competition_id=:c AND mm.measurement_id=:parent",
            "champions": "SELECT * FROM api_promotions WHERE competition_id=:c",
            "weight-sets": "SELECT * FROM api_weight_sets WHERE competition_id=:c",
            "allocations": "SELECT wa.* FROM api_allocations wa JOIN api_weight_sets w ON w.id=wa.weight_set_id WHERE w.competition_id=:c AND wa.weight_set_id=:parent",
            "corpus": "SELECT ac.* FROM api_corpus ac JOIN api_profiles p ON p.profile_hash=ac.profile_hash WHERE p.competition_id=:c AND ac.profile_hash=:profile_hash",
        }
        queries["progress"] = PROGRESS_QUERY
        queries["history"] = (
            "SELECT * FROM api_evaluations WHERE competition_id=:c AND state='completed'"
        )
        query = queries[kind]
        params: dict[str, object] = dict(c=self.competition)
        if parent is not None:
            params["parent"] = parent
        columns = {
            "hotkey": "hotkey",
            "state": "state",
            "mode": "mode",
            "status": "status",
            "role": "role",
            "phase": "phase",
            "case_id": "case_id",
            "round_index": "round_index",
            "target": "target",
            "tier": "tier",
            "group": "group_name",
        }
        # Build filtering at SQL level before LIMIT, never page then filter in Python.
        if kind == "submissions":
            query = "SELECT sub.*,coalesce(a.state,'queued') AS proof_state,e.state AS evaluation_state,e.outcome AS outcome,EXISTS(SELECT 1 FROM api_requests r WHERE r.evaluation_id=e.id AND r.state='running') AS request_running FROM api_submissions sub LEFT JOIN LATERAL(SELECT * FROM api_evaluations WHERE submission_id=sub.id ORDER BY id DESC LIMIT 1) e ON true LEFT JOIN api_admissions a ON a.id=coalesce(e.admission_id,(SELECT id FROM api_admissions WHERE source_sha256=sub.source_sha256 AND proof_identity=:proof_identity)) WHERE sub.competition_id=:c"
            params["proof_identity"] = None if self.policy is None else self.policy.proof_identity
            query = (
                "SELECT feed.*,CASE WHEN evaluation_state='completed' THEN 'completed' WHEN evaluation_state='pending' THEN CASE WHEN request_running THEN 'benchmarking' ELSE 'waiting' END WHEN proof_state='running' THEN 'proving' WHEN proof_state='rejected' THEN 'rejected' WHEN proof_state='failed' THEN 'failed' WHEN proof_state='passed' THEN 'waiting' ELSE 'queued' END AS processing_state FROM ("
                + query
                + ") feed"
            )
            query = "SELECT * FROM (" + query + ") filtered WHERE true"
        for key, value in filters.items():
            if value is None:
                continue
            if key == "profile_hash":
                params[key] = value
                continue
            column = (
                "processing_state"
                if kind == "submissions" and key == "state"
                else "outcome"
                if key == "outcome"
                else "intake"
                if key == "intake_mode"
                else columns[key]
            )
            if key == "intake_mode":
                value = "api" if value == "subnet" else "dev"
            query += f" AND {column}=:{key}"
            params[key] = value
        key = "member_index" if kind == "members" else "uid" if kind == "allocations" else "id"
        return await page(
            self.session,
            self.settings.cursor_secret,
            f"bendtt:{kind}:{parent}",
            query,
            params,
            key=key,
            limit=limit,
            cursor=cursor,
            ascending=kind in ("measurements", "members", "allocations", "corpus"),
            after=after,
            textual=kind == "corpus",
        )

    async def overview(self) -> s.Overview:
        cfg = await self.intake_config()
        groups = await rows(
            self.session,
            "SELECT count(*) AS count,state FROM (SELECT CASE WHEN e.state='completed' THEN 'completed' WHEN e.state='pending' THEN CASE WHEN EXISTS(SELECT 1 FROM api_requests r WHERE r.evaluation_id=e.id AND r.state='running') THEN 'benchmarking' ELSE 'waiting' END WHEN a.state='running' THEN 'proving' WHEN a.state='passed' THEN 'waiting' WHEN a.state IN ('failed','rejected') THEN a.state ELSE 'queued' END AS state FROM api_submissions s LEFT JOIN LATERAL(SELECT * FROM api_evaluations WHERE submission_id=s.id ORDER BY id DESC LIMIT 1)e ON true LEFT JOIN api_admissions a ON a.id=coalesce(e.admission_id,(SELECT id FROM api_admissions WHERE source_sha256=s.source_sha256 AND proof_identity=:proof)) WHERE s.competition_id=:c) states GROUP BY state",
            dict(
                c=self.competition,
                proof=None if self.policy is None else self.policy.proof_identity,
            ),
        )
        counts = {string(v["state"]): number(v["count"]) for v in groups}
        champion = await rows(
            self.session,
            "SELECT id AS promotion_id,submission_id,source_sha256 FROM api_promotions WHERE competition_id=:c AND current",
            dict(c=self.competition),
        )
        return s.Overview(
            as_of=self.now,
            status="ready" if self.policy else "not_ready",
            submissions_open=bool(
                self.policy
                and cfg["enabled"]
                and self.settings.bendtt_submissions_enabled
                and not self.settings.submissions_paused
            ),
            intake_mode="dev" if self.dev else "subnet",
            files=[s.File(max_bytes=self.settings.bendtt_max_source_bytes)],
            authentication=s.Authentication(registration_required=not self.dev),
            registration_policy=s.RegistrationPolicy(
                network=self.settings.bendtt_chain_network,
                netuid=self.settings.bendtt_chain_netuid,
                minimum_registered_block=str(self.settings.bendtt_min_registered_block),
            ),
            profile_hash=None if self.policy is None else self.policy.profile_hash,
            proof_identity=None if self.policy is None else self.policy.proof_identity,
            scoring=None if self.policy is None else self.policy.scoring,
            queue=s.Queue(
                **{k: counts.get(k, 0) for k in ("queued", "proving", "waiting", "benchmarking")}
            ),
            champion=s.Reference.model_validate(champion[0]) if champion else None,
            baseline=None
            if self.policy is None
            else s.Reference(
                promotion_id=None,
                submission_id=None,
                source_sha256=self.policy.baseline_source_sha256,
                shipped=True,
            ),
            links={k: f"{BASE}/{k}" for k in ("submissions", "champions", "policy")},
        )

    async def weight(self, row: dict[str, object]) -> s.WeightSet:
        fields = {k: v for k, v in row.items() if k != "competition_id"}
        return s.WeightSet.model_validate(
            dict(
                fields,
                reason=f"Recorded weight-set state: {row['status']}",
                links=dict(allocations=f"{BASE}/weight-sets/{row['id']}/allocations"),
            )
        )

    async def rewards(self) -> s.Rewards:
        policy = await rows(
            self.session,
            "SELECT policy FROM api_reward_policy WHERE competition_id=:c",
            dict(c=self.competition),
        )
        latest = await rows(
            self.session,
            "SELECT * FROM api_weight_sets WHERE competition_id=:c ORDER BY id DESC LIMIT 1",
            dict(c=self.competition),
        )
        return s.Rewards(
            as_of=self.now,
            policy=s.RewardPolicy.model_validate(policy[0]["policy"]) if policy else None,
            latest_weight_set=None if not latest else await self.weight(latest[0]),
            links=dict(weight_sets=BASE + "/weight-sets"),
        )

    async def health(self) -> s.Health:
        overview = await self.overview()
        chain = await self.chain()
        active = await rows(
            self.session,
            "SELECT id FROM api_evaluations WHERE competition_id=:c AND state='pending' ORDER BY id DESC LIMIT 1",
            dict(c=self.competition),
        )
        targets = []
        for target in [] if self.policy is None else self.policy.targets:
            found = await rows(
                self.session,
                "SELECT state,lease_expires_at,started_at,finished_at FROM api_attempts WHERE competition_id=:c AND target=:t ORDER BY id DESC LIMIT 1",
                dict(c=self.competition, t=target.id),
            )
            busy = bool(
                found
                and found[0]["state"] == "running"
                and moment(found[0]["lease_expires_at"]) > self.now
            )
            targets.append(
                s.TargetHealth(
                    id=target.id,
                    activity="busy" if busy else "unknown",
                    last_contact_at=None,
                    last_benchmark_at=None if not found else moment(
                        found[0]["finished_at"] or found[0]["started_at"]
                    ),
                )
            )
        recent = (
            self.observed_at is not None and (self.now - self.observed_at).total_seconds() <= 120
        )
        status = (
            "not_ready"
            if self.policy is None
            else "degraded"
            if not recent
            or (not self.dev and chain.freshness != "fresh")
            or (active and any(t.activity != "busy" for t in targets))
            else "ready"
        )
        return s.Health.model_validate(
            dict(
                status=status,
                as_of=self.now,
                database="up",
                coordinator="unknown"
                if self.observed_at is None
                else "recent"
                if recent
                else "stale",
                chain="not_required" if self.dev else chain.freshness,
                active_evaluation_id=None if not active else active[0]["id"],
                targets=targets,
                submissions_open=overview.submissions_open,
            )
        )


    async def statistics(self) -> s.Statistics:
        result = await rows(
            self.session,
            "SELECT count(*) AS submissions,"
            "count(*) FILTER (WHERE e.state='completed' AND "
            "e.outcome IN ('promoted','not_promoted')) AS benchmarked,"
            "count(*) FILTER (WHERE a.state='rejected') AS proof_rejected,"
            "count(*) FILTER (WHERE e.outcome='benchmark_failed') AS benchmark_failed "
            "FROM api_submissions sub LEFT JOIN LATERAL "
            "(SELECT * FROM api_evaluations WHERE submission_id=sub.id "
            "ORDER BY id DESC LIMIT 1) e ON true LEFT JOIN api_admissions a "
            "ON a.id=coalesce(e.admission_id,(SELECT id FROM api_admissions "
            "WHERE source_sha256=sub.source_sha256 AND proof_identity=:proof)) "
            "WHERE sub.competition_id=:c",
            dict(c=self.competition, proof=None if self.policy is None else self.policy.proof_identity),
        )
        promoted = await rows(
            self.session,
            "SELECT count(*) AS promotions,count(DISTINCT submission_id) AS champions "
            "FROM api_promotions WHERE competition_id=:c",
            dict(c=self.competition),
        )
        return s.Statistics.model_validate({**result[0], **promoted[0], "as_of": self.now})


    async def history(self, limit: int, cursor: str | None) -> s.Page[s.HistoryEntry]:
        result = await self.collection("history", None, {}, limit, cursor)
        if not result.rows:
            return s.Page[s.HistoryEntry](items=[], next_cursor=result.next_cursor, as_of=self.now)
        identifiers = [number(row["id"]) for row in result.rows]
        measured = await rows(
            self.session, PROGRESS_QUERY + " AND e.id=ANY(:ids)",
            dict(c=self.competition, ids=identifiers),
        )
        profiles = await rows(
            self.session,
            "SELECT profile_hash,policy FROM api_profiles WHERE competition_id=:c "
            "AND profile_hash=ANY(:profiles)",
            dict(c=self.competition, profiles=list({string(row["profile_hash"]) for row in result.rows if row["profile_hash"] is not None})),
        )
        policies = {string(row["profile_hash"]): s.Policy.model_validate(row["policy"]) for row in profiles}
        grouped: dict[int, list[s.ProgressPoint]] = {}
        for raw in measured:
            grouped.setdefault(number(raw["evaluation_id"]), []).append(s.ProgressPoint.model_validate(raw))
        items = []
        for row in result.rows:
            ident = number(row["id"])
            admission = speedup(row)
            machines = [point.model_copy(update={"admission_speedup": admission}) for point in grouped.get(ident, [])]
            policy = policies.get(string(row["profile_hash"])) if row["profile_hash"] is not None else None
            expected_raw = row["expected_targets"]
            if not isinstance(expected_raw, list):
                raise ValueError("invalid expected targets")
            expected = {string(target) for target in expected_raw}
            complete = len(machines) == len(expected) and {p.target for p in machines} == expected
            direct = complete and all(
                point.method == "direct_baseline" and point.baseline_speedup is not None
                for point in machines
            )
            value = None
            method = "unavailable"
            if direct and admission is not None:
                value = admission.estimate
                method = "direct_baseline"
            elif policy is not None and complete:
                weights = {target.id: target.weight for target in policy.targets}
                if set(weights) == expected:
                    value = combined_baseline(machines, weights)
                if value is not None:
                    method = "direct_baseline" if direct else "telemetry"
            combined = s.ProgressPoint.model_validate(dict(
                id=row["id"], submission_id=row["submission_id"], evaluation_id=row["id"],
                target="combined", evaluated_at=row["completed_at"], promoted=row["outcome"] == "promoted",
                profile_hash=row["profile_hash"], baseline_source_sha256=row["baseline_source_sha256"],
                baseline_speedup=value, method=method, admission_speedup=admission,
            ))
            items.append(s.HistoryEntry(id=str(ident), combined=combined, machines=machines))
        return s.Page[s.HistoryEntry](items=items, next_cursor=result.next_cursor, as_of=self.now)


def combined_baseline(points: list[s.ProgressPoint], weights: dict[str, float]) -> float | None:
    import math

    if not weights or any(not math.isfinite(weight) or weight <= 0 for weight in weights.values()):
        raise ValueError("machine weights must be finite and positive")
    if len(points) != len(weights) or {point.target for point in points} != set(weights):
        return None
    if any(point.baseline_speedup is None for point in points):
        return None
    return math.exp(sum(
        weights[point.target] * math.log(point.baseline_speedup)
        for point in points if point.baseline_speedup is not None
    ) / sum(weights.values()))
