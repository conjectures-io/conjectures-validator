"""Explicit async adapter to the compression-owned schema (migration 0011+).

No copied ORM, migrations, verifier or scoring implementation is imported here.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from types import SimpleNamespace

from sqlalchemy import text
from submission_api.errors import ServiceUnavailable

SUB_FIELDS = """id, hotkey, baseline_key, baseline_active, digest, submitted_at, state,
exit_code, source_sha256, verifier_fingerprint, verification_attempt,
static_verified_at, lean_verified_at, aggregation_id, admission_check_id,
claimed_at, finished_at, account_id"""
PUBLIC = "(hotkey IS NOT NULL OR baseline_key IS NOT NULL)"


async def compatible(session):
    columns = set(
        (
            await session.execute(
                text("""
        SELECT table_name || '.' || column_name FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name IN
        ('submissions', 'submission_files', 'weight_sets')
    """)
            )
        ).scalars()
    )
    if (
        not {
            "submission_files.content",
            "submissions.verification_attempt",
            "submissions.account_id",
            "weight_sets.api_snapshot",
        }
        <= columns
    ):
        raise ServiceUnavailable(
            "Compression schema requires its owner migration 0011 or later.",
            reason_code="COMPETITION_SCHEMA_UNAVAILABLE",
        )


async def lock_hotkey(session, hotkey):
    key = int.from_bytes(
        hashlib.sha256(("compression-intake:" + hotkey).encode()).digest()[:8], "big", signed=True
    )
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


async def queue_depth(session):
    return (
        await session.execute(
            text(
                "SELECT count(*) FROM submissions WHERE "
                + PUBLIC
                + " AND state IN ('queued','verifying')"
            )
        )
    ).scalar_one()


async def is_registered(session, hotkey):
    return bool(
        (
            await session.execute(
                text("SELECT EXISTS(SELECT 1 FROM registrations WHERE ss58_hot=:hotkey)"),
                {"hotkey": hotkey},
            )
        ).scalar_one()
    )


async def registered_by(session, *, hotkey, coldkey):
    return bool(
        (
            await session.execute(
                text("""SELECT EXISTS(SELECT 1 FROM registrations
        WHERE ss58_hot=:hotkey AND ss58_cold=:coldkey)"""),
                {"hotkey": hotkey, "coldkey": coldkey},
            )
        ).scalar_one()
    )


async def may_queue(session, hotkey):
    slots = (
        await session.execute(
            text("""SELECT count(*) FROM registrations r
        WHERE ss58_hot=:hotkey AND NOT EXISTS
        (SELECT 1 FROM entitlement_claims c WHERE c.registration_id=r.id)"""),
            {"hotkey": hotkey},
        )
    ).scalar_one()
    pending = (
        await session.execute(
            text("""SELECT count(*) FROM submissions
        WHERE hotkey=:hotkey AND state IN ('queued','verifying')"""),
            {"hotkey": hotkey},
        )
    ).scalar_one()
    return pending < slots, slots, pending


async def find_submission(session, *, hotkey, digest):
    row = (
        (
            await session.execute(
                text("SELECT id, state FROM submissions WHERE hotkey=:hotkey AND digest=:digest"),
                {"hotkey": hotkey, "digest": digest},
            )
        )
        .mappings()
        .first()
    )
    return SimpleNamespace(**row) if row else None


async def add_submission(session, *, hotkey, digest, parse_source, proof_source, account_id=None):
    sid = (
        await session.execute(
            text("""INSERT INTO submissions(hotkey,digest,account_id)
        VALUES (:hotkey,:digest,:account_id) ON CONFLICT (hotkey,digest) DO NOTHING RETURNING id"""),
            {
                "hotkey": hotkey,
                "digest": digest,
                "account_id": str(account_id) if account_id else None,
            },
        )
    ).scalar_one_or_none()
    if sid is None:
        row = await find_submission(session, hotkey=hotkey, digest=digest)
        return row.id, False
    await session.execute(
        text("""INSERT INTO submission_files(submission_id,name,content)
        VALUES (:id,:name,:content)"""),
        [
            {"id": sid, "name": "parse.rs", "content": parse_source},
            {"id": sid, "name": "Parse.lean", "content": proof_source},
        ],
    )
    return sid, True


async def get_submission(session, sid):
    row = (
        (
            await session.execute(
                text("SELECT " + SUB_FIELDS + " FROM submissions WHERE id=:id AND " + PUBLIC),
                {"id": sid},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def hit_rate_limit(session, subject, *, limit, window_seconds):
    now = int(datetime.now(UTC).timestamp())
    start = datetime.fromtimestamp(now - now % window_seconds, UTC)
    count = (
        await session.execute(
            text("""INSERT INTO rate_limit_windows(subject,window_start,hits)
        VALUES (:subject,:start,1) ON CONFLICT(subject,window_start)
        DO UPDATE SET hits=rate_limit_windows.hits+1 RETURNING hits"""),
            {"subject": subject, "start": start},
        )
    ).scalar_one()
    return count <= limit, count


def with_bounty(result):
    """Attach each score row's bounty total from the pass's api_snapshot (migration 0012+).

    A pass from before the competition recorded bounties has no section; its totals stay
    unknown (None) rather than zero.
    """
    bounty = result["api_snapshot"].get("bounty") or {}
    earned = {str(b["submission_id"]): b for b in bounty.get("submissions", [])}
    for score in result["scores"]:
        entry = earned.get(str(score["submission_id"]))
        score["bounty_rao"] = entry["earned_rao"] if entry else None
        score["bounty_capped"] = entry["capped"] if entry else None
    return result


# A pass that stopped before scoring (a skipped epoch) stores `api_snapshot` as JSON `null`,
# not SQL NULL: the competition's JSONB column persists Python None that way, and that model
# is pinned by its verifier fingerprint. `IS NOT NULL` is true of JSON `null`, so it would
# select the skip and hand every reader None. Only an object is a published snapshot.
PUBLISHED = "jsonb_typeof(api_snapshot) = 'object'"


async def snapshot(session, sid=None):
    if sid is None:
        sql = f"SELECT id, created_at, dry_run, accepted, api_snapshot FROM weight_sets WHERE {PUBLISHED} ORDER BY id DESC LIMIT 1"
        params = {}
    else:
        sql = f"SELECT id, created_at, dry_run, accepted, api_snapshot FROM weight_sets WHERE id=:id AND {PUBLISHED}"
        params = {"id": sid}
    row = (await session.execute(text(sql), params)).mappings().first()
    if row is None:
        return None
    result = dict(row)
    result["scores"] = [
        dict(r)
        for r in (
            await session.execute(
                text("""SELECT submission_id,
        aggregation_id, admission_check_id, hotkey, baseline_key, on_frontier,
        pareto_weight, improvement_weight, combined_weight, payable_weight, burn_reason
        FROM score_snapshots WHERE weight_set_id=:id ORDER BY submission_id"""),
                {"id": row["id"]},
            )
        ).mappings()
    ]
    with_bounty(result)
    observed = result["api_snapshot"].get("observed_submissions")
    if observed is not None:
        live = [
            list(row)
            for row in (
                await session.execute(
                    text("""SELECT id, state,
            aggregation_id, admission_check_id, source_sha256, verifier_fingerprint, baseline_active
            FROM submissions WHERE hotkey IS NOT NULL OR baseline_active ORDER BY id""")
                )
            ).all()
        ]
        if live != observed:
            result["freshness"] = "stale"
            result["freshness_reason"] = (
                "Submission membership or selected evidence changed after this pass."
            )
    return result


async def aggregation(session, aid):
    if aid is None:
        return None
    row = (
        (
            await session.execute(
                text("""SELECT id, statistics, context, calculator_version,
        raw_bytes, bytes, parse_seconds, compression_seconds, source_sha256
        FROM benchmark_aggregations WHERE id=:id"""),
                {"id": aid},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def evidence(session, sub):
    aggregate = await aggregation(session, sub["aggregation_id"])
    decision = None
    reference_aggregation = None
    if sub["admission_check_id"] is not None:
        row = (
            (
                await session.execute(
                    text("""SELECT details, created_at, candidate_aggregation_id,
            reference_aggregation_id FROM submission_admission_checks
            WHERE id=:id AND submission_id=:sid"""),
                    {"id": sub["admission_check_id"], "sid": sub["id"]},
                )
            )
            .mappings()
            .first()
        )
        if (
            row
            and row["candidate_aggregation_id"] == sub["aggregation_id"]
            and sub["state"] == "accepted"
            and sub["lean_verified_at"]
        ):
            decision = {**row["details"], "created_at": row["created_at"].isoformat()}
            reference_aggregation = await aggregation(session, row["reference_aggregation_id"])
    return {
        "submission": sub,
        "aggregation": aggregate,
        "admission": decision,
        "reference_aggregation": reference_aggregation,
        "scoring_input": False,
    }
