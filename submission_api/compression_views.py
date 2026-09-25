"""Presentation of recorded compression evidence. No scoring or resampling."""

from __future__ import annotations

import re
from . import schemas_compression as s

GATE = {
    "queued": "queued",
    "verifying": "running",
    "accepted": "passed",
    "rejected": "failed",
    "error": "error",
}
EXPLANATIONS = {
    "passed": "The lower confidence bound is above zero: the speed improvement passed.",
    "inconclusive": "The measurements do not establish a speed improvement at the required confidence.",
    "excluded": "The submission is outside the scoring limits.",
    "dominated": "An existing point is at least as good on both scoring axes.",
    "not_required": "No speed comparison is required for this contribution.",
    "pending": "Awaiting current verification, benchmark or admission evidence.",
    "invalid_evidence": "The evidence is not valid for the current scoring context.",
}


def identifier(value):
    return str(value) if value is not None else None


def timestamp(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def metrics(aggregation):
    if not aggregation:
        return None
    stats = aggregation.get("statistics") or {}
    return s.Metrics(
        balanced_time_ratio=(stats.get("relative_timing") or {}).get("time_ratio"),
        mean_file_compression_pct=(stats.get("compression") or {}).get("ratio_pct"),
        total_compression_seconds=aggregation.get("compression_seconds"),
        lz77_seconds=aggregation.get("parse_seconds"),
        byte_weighted_compression_pct=(
            100 * aggregation["bytes"] / aggregation["raw_bytes"]
            if aggregation.get("raw_bytes")
            else None
        ),
    )


def interval(aggregation):
    m = metrics(aggregation)
    if not m or m.balanced_time_ratio is None:
        return None
    stats = aggregation.get("statistics") or {}
    values = (stats.get("intervals") or {}).get("balanced_time_ratio")
    if not values:
        return None
    return s.TimingInterval(
        metric="balanced_time_ratio",
        estimate=m.balanced_time_ratio,
        lower=values[0],
        upper=values[1],
        confidence_level=stats["confidence"],
        method=stats["method"],
        draws=stats["draws"],
    )


def bounds(detail):
    b = (detail or {}).get("scoring_bounds")
    if not b:
        return None
    return s.Bounds(
        eligible=b.get("eligible"),
        violations=b.get("violations", []),
        max_balanced_time_ratio=b.get("max_time_ratio"),
        max_mean_file_compression_pct=b.get("max_compression_pct"),
        observed_balanced_time_ratio=b.get("time_ratio"),
        observed_mean_file_compression_pct=b.get("compression_pct"),
    )


def admission(item, *, freshness="unknown"):
    detail = item.get("admission") or {}
    outcome = detail.get("outcome", "pending")
    recorded = outcome in {"passed", "inconclusive", "excluded", "dominated", "not_required"}
    ref = detail.get("reference") or {}
    return s.Admission(
        decision_id=identifier(item["submission"].get("admission_check_id")) if recorded else None,
        status="recorded"
        if recorded
        else "invalid"
        if outcome == "invalid_evidence"
        else "pending",
        outcome=outcome if recorded else None,
        admitted=outcome in {"passed", "not_required"} if recorded else None,
        reason_code=detail.get("reason_code", "awaiting-current-scoring-evidence"),
        explanation=EXPLANATIONS.get(outcome, EXPLANATIONS["pending"]),
        reference_submission_id=identifier(ref.get("submission_id")),
        freshness=freshness,
    )


RAO_PER_ALPHA = 1_000_000_000


def alpha(rao):
    return rao / RAO_PER_ALPHA if rao is not None else None


def submission(item, score=None, snapshot_id=None, *, freshness="unknown"):
    sub = item["submission"]
    allocation = None
    if score is not None:
        allocation = s.Score(
            snapshot_id=str(snapshot_id),
            **{
                k: score[k]
                for k in (
                    "on_frontier",
                    "pareto_weight",
                    "improvement_weight",
                    "combined_weight",
                    "payable_weight",
                )
            },
            payment_eligible=(
                score.get("burn_reason") is None or score["payable_weight"] > 0
            ),
            unpaid_reason=score.get("burn_reason"),
            bounty_earned_alpha=alpha(score.get("bounty_rao")),
            bounty_capped=score.get("bounty_capped"),
        )
    return s.Submission(
        id=str(sub["id"]),
        kind="baseline" if sub.get("baseline_key") else "miner",
        hotkey=sub["hotkey"],
        baseline_name=sub.get("baseline_key"),
        submitted_at=timestamp(sub["submitted_at"]),
        gate_status=GATE.get(sub["state"], "unknown"),
        aggregation_id=identifier((item.get("aggregation") or {}).get("id")),
        metrics=metrics(item.get("aggregation")),
        admission=admission(item, freshness=freshness),
        score=allocation,
    )


def decision(item):
    summary = admission(item)
    if summary.status != "recorded":
        return None
    detail = item["admission"]
    stats = detail.get("statistics")
    speed = None
    ref = detail.get("reference") or {}
    if stats:
        # Stored gains are percentages; API gain units are fractions.
        reference_time = stats["reference_time"]
        gain_interval = s.DisplayInterval(
            lower=stats["lower_pct"] / 100,
            upper=stats["upper_pct"] / 100,
            confidence_level=0.90,
            method=stats["method_version"],
            sidedness="two-sided",
        )
        speed = s.SpeedTest(
            estimated_gain=stats["gain_pct"] / 100,
            lower_confidence_bound=stats["lower_pct"] / 100,
            confidence_level=stats["confidence"],
            threshold=stats["threshold_pct"] / 100,
            comparison="strictly_greater",
            passed=summary.outcome == "passed",
            method=stats["method_version"],
            draws=stats["draws"],
            resampling="mixed" if stats.get("shared_observations") else "independent-runs",
            file_count=stats.get("file_count", len(stats["files"]) if "files" in stats else None),
            corpora=[
                pair[0] for pair in (detail.get("evaluation_context") or {}).get("corpora", [])
            ],
            limitations=[stats["limitations"]],
            gain_display_interval=gain_interval,
            comparison_range=s.DisplayInterval(
                lower=reference_time * (1 - gain_interval.upper),
                upper=reference_time * (1 - gain_interval.lower),
                confidence_level=0.90,
                method="reference-anchored-relative-comparison",
                sidedness="two-sided",
            ),
            candidate=detail.get("candidate"),
            reference=ref,
            frontier_before=detail.get("frontier_before", []),
            candidate_timing=interval(item.get("aggregation")),
            reference_timing=interval(item.get("reference_aggregation")),
        )
    return s.AdmissionDecision(
        id=summary.decision_id,
        submission_id=str(item["submission"]["id"]),
        created_at=detail.get("created_at"),
        outcome=summary.outcome,
        admitted=bool(summary.admitted),
        reason_code=summary.reason_code,
        explanation=summary.explanation,
        policy_version=detail.get("policy_version"),
        candidate_aggregation_id=identifier(item["submission"].get("aggregation_id")),
        reference_submission_id=summary.reference_submission_id,
        reference_aggregation_id=identifier(ref.get("aggregation_id")),
        bounds=bounds(detail),
        speed_test=speed,
    )


def pipeline(sub):
    result = {}
    for name, field in (("static", "static_verified_at"), ("lean", "lean_verified_at")):
        result[name] = s.Stage(
            status="passed"
            if sub.get(field)
            else "unknown"
            if sub["state"] in {"rejected", "error", "verifying"}
            else "pending",
            finished_at=timestamp(sub.get(field)),
        )
    for name in ("benchmark", "aggregation"):
        result[name] = s.Stage(
            status="passed"
            if sub.get("aggregation_id")
            else "unknown"
            if sub["state"] in {"rejected", "error", "verifying"}
            else "pending"
        )
    return result


def sanitized_report(value):
    if value is None:
        return None
    value = re.sub(r"(?:postgres(?:ql)?|https?)://[^\s]+", "[redacted-url]", value)
    value = re.sub(r"(?<![\w])/(?:[^\s:/]+/)+[^\s:]*", "[internal-path]", value)
    return re.sub(r"(?i)(password|token|secret|api_key)\s*[=:]\s*[^\s]+", r"\1=[redacted]", value)
