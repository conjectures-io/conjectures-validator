"""Explicit async adapter to the FloatLib-owned schema (`conjectures-optimisation-float-kernels`).

Read-only, through the `platform_api` role that repository grants (`deploy/platform/
platform_api.sql`): `submissions`, `promotions` and the published `weight_sets.api_snapshot`.
No ORM, migration, gate or scoring code is copied here; what the board means is the schema's
contract (`deploy/platform/CONTRACT.md` there), restated in the queries below:

* a slot's champion is its latest promotion not demoted; with none, the shipped champion holds;
* the board counts accepted rows timed against the current champion (`champion_sha256`), each
  hotkey's best ratio, fastest first, the champion's row leading;
* admission outcomes are not recomputed: the weight setter publishes them for the boards it
  pays, and only those boards have them.
"""

from __future__ import annotations

import json

from sqlalchemy import text

from submission_api.errors import ServiceUnavailable

SUB_FIELDS = """id, hotkey, slot, digest, submitted_at, state, exit_code, stage, reason,
ratio, classes, seed, incumbent_sha256, champion_sha256, verifier_fingerprint, finished_at"""

REQUIRED_COLUMNS = frozenset(
    {
        "submissions.slot",
        "submissions.ratio",
        "submissions.classes",
        "submissions.champion_sha256",
        "submissions.verifier_fingerprint",
        "submissions.report",
        "promotions.submission_id",
        "promotions.promoted_at",
        "promotions.demoted_at",
        "weight_sets.api_snapshot",
    }
)


async def compatible(session):
    """Refuse a schema older than FloatLib's migration 0016, or one `platform_api` cannot read.

    The grant first: `information_schema` lists only the columns the role may use, so a
    `promotions` table it cannot read would otherwise look like one that does not exist yet.
    """
    unreadable = (
        await session.execute(
            text(
                "SELECT to_regclass('promotions') IS NOT NULL "
                "AND NOT has_table_privilege(current_user, 'promotions', 'SELECT')"
            )
        )
    ).scalar_one()
    if unreadable:
        raise ServiceUnavailable(
            "FloatLib's promotions are not readable; re-run its deploy/platform/grant.sh.",
            reason_code="COMPETITION_SCHEMA_UNAVAILABLE",
        )
    columns = set(
        (
            await session.execute(
                text("""
        SELECT table_name || '.' || column_name FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name IN
        ('submissions', 'promotions', 'weight_sets')
    """)
            )
        ).scalars()
    )
    if not REQUIRED_COLUMNS <= columns:
        raise ServiceUnavailable(
            "FloatLib schema requires its owner migration 0016 or later.",
            reason_code="COMPETITION_SCHEMA_UNAVAILABLE",
        )


async def queue_depth(session):
    """Queued plus verifying, across every slot: how long a new submission waits."""
    return (
        await session.execute(
            text("SELECT count(*) FROM submissions WHERE state IN ('queued','verifying')")
        )
    ).scalar_one()


async def promotions(session, slot):
    """Every promotion in `slot` with its submission, newest first."""
    rows = await session.execute(
        text("""
        SELECT p.id AS promotion_id, p.promoted_at, p.demoted_at,
               p.champion_sha256 AS promoted_sha256, s.id, s.hotkey, s.ratio,
               s.champion_sha256 AS timed_against, s.report
        FROM promotions p JOIN submissions s ON s.id = p.submission_id
        WHERE p.slot = :slot ORDER BY p.id DESC
    """),
        {"slot": slot},
    )
    history = []
    for row in rows.mappings():
        promotion = dict(row)
        promotion["benchmark"] = benchmark_of(promotion.pop("report"))
        history.append(promotion)
    return history


# The fixed twelve classes FloatLib scored on before its suites: what a verdict without a suite
# was timed on.
LEGACY = "legacy"

# Measured speed-ups over the shipped kernels, by benchmark and the promoted Kernel.lean's sha256.
# A benchmark change breaks the chain of ratios (each link is timed on one benchmark); an anchor
# restarts it on the new one: that promotion's speed-up is the measured value, and later
# promotions on the same benchmark chain from it. Each is one operator bench run of the kernel
# against the shipped one on that benchmark, recorded here with how it was measured.
SPEEDUP_ANCHORS: dict[tuple[str, str], float] = {
    # #265, the first champion timed on this suite, against the shipped kernels on it: one PROD
    # gate --bench run per seed on 2026-10-22, ratios 0.4615 (11), 0.4459 (23), 0.4506 (37);
    # 1 / their geometric mean.
    (
        "scoring-v2-2026-10",
        "494ebfa20c8d8f0f6b93eac4684579f6382f8ee21b767c787b9da924d15fc5fe",
    ): 2.209,
}


def benchmark_of(report):
    """The benchmark a verdict was timed on: its suite's version, or `LEGACY` without one."""
    try:
        suite = json.loads(report).get("suite") if report else None
    except (ValueError, AttributeError):
        suite = None
    if isinstance(suite, dict) and suite.get("version"):
        return str(suite["version"])
    return LEGACY


def speedups(history, anchors=None):
    """Each promotion's speed-up over the shipped kernels, by promotion id; None where unknown.

    `ratio` is the promoted submission's total time over the code it was timed against
    (`timed_against`), lower being faster. Chaining ratios in promotion order is wrong once a
    champion has been demoted: the board falls back to an older champion, and the next challenger
    is timed against that one. So the chain follows code hashes instead — a promotion's parent is
    the latest earlier promotion of the code it was timed against, and the chain ends at the
    shipped code, which the first promotion was timed against. The speed-up is 1 / the product of
    the ratios along it.

    Every link must be timed on the promotion's own benchmark (`benchmark`): a ratio from a
    suite times nothing on the legacy classes, so a chain does not cross a benchmark change.
    There, an anchor in `anchors` (default `SPEEDUP_ANCHORS`) restarts it with a measured value;
    without one the promotion and those chained from it have none.

    None when the chain cannot be followed: a missing ratio, a reference that is neither shipped
    nor an earlier promotion, or a parent on another benchmark with no anchor. Each link is a
    separate bench run, so the product is an estimate whose noise compounds with the chain's length.
    """
    if not history:
        return {}
    anchors = SPEEDUP_ANCHORS if anchors is None else anchors
    ordered = sorted(history, key=lambda p: p["promotion_id"])
    shipped = ordered[0]["timed_against"]
    result = {}
    for index, promotion in enumerate(ordered):
        benchmark = promotion.get("benchmark", LEGACY)
        anchored = anchors.get((benchmark, promotion["promoted_sha256"]))
        if anchored is not None:
            result[promotion["promotion_id"]] = anchored
            continue
        ratio = promotion["ratio"]
        reference = promotion["timed_against"]
        if ratio is None or ratio <= 0 or reference is None or shipped is None:
            result[promotion["promotion_id"]] = None
            continue
        if reference == shipped:
            base = 1.0
        else:
            parent = next(
                (p for p in reversed(ordered[:index]) if p["promoted_sha256"] == reference),
                None,
            )
            same = parent is not None and parent.get("benchmark", LEGACY) == benchmark
            base = result.get(parent["promotion_id"]) if same else None
        result[promotion["promotion_id"]] = None if base is None else base / ratio
    return result


def standing(history):
    """The current champion's promotion: the newest one not demoted, or None."""
    return next((p for p in history if p["demoted_at"] is None), None)


async def champion_sha256(session, slot, history):
    """The Kernel.lean the slot's board is measured against.

    The standing champion's; with every promotion demoted, the shipped one the first promoted
    submission was timed against; with no promotion at all, the latest verdict's.
    """
    if (current := standing(history)) is not None:
        return current["promoted_sha256"]
    query = (
        "SELECT s.champion_sha256 FROM promotions p JOIN submissions s ON s.id = p.submission_id "
        "WHERE p.slot = :slot ORDER BY p.id LIMIT 1"
        if history
        else "SELECT champion_sha256 FROM submissions WHERE slot = :slot "
        "AND champion_sha256 IS NOT NULL ORDER BY id DESC LIMIT 1"
    )
    return (await session.execute(text(query), {"slot": slot})).scalar_one_or_none()


async def board(session, slot, champion_sha, champion_id, *, historical=False):
    """Each hotkey's best comparable accepted submission, fastest first, the champion leading."""
    where = "slot = :slot AND state = 'accepted' AND ratio IS NOT NULL"
    params = {"slot": slot}
    if champion_sha is not None:
        where += " AND champion_sha256 = :champion"
        params["champion"] = champion_sha
    rows = [
        dict(row)
        for row in (
            await session.execute(
                text(
                    "SELECT DISTINCT ON (hotkey) "
                    + SUB_FIELDS
                    + " FROM submissions WHERE "
                    + where
                    + " ORDER BY hotkey, ratio, submitted_at, id"
                ),
                params,
            )
        ).mappings()
    ]
    rows.sort(key=lambda r: (r["ratio"], r["submitted_at"], r["id"]))
    if champion_id is None:
        return rows
    # The champion leads its board, standing in for its hotkey's line. Its own ratio is against
    # the champion it took over from, so it is read by id rather than from the comparable rows.
    champion = await get_submission(session, champion_id)
    if champion is None:
        return rows
    return [
        champion,
        *(
            r
            for r in rows
            if r["id"] != champion_id and (historical or r["hotkey"] != champion["hotkey"])
        ),
    ]


async def get_submission(session, sid):
    row = (
        (
            await session.execute(
                text("SELECT " + SUB_FIELDS + " FROM submissions WHERE id = :id"), {"id": sid}
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row is not None else None


async def report(session, sid):
    return (
        await session.execute(text("SELECT report FROM submissions WHERE id = :id"), {"id": sid})
    ).scalar_one_or_none()


async def latest_incumbent(session, slot):
    """The latest recorded incumbent, for display only."""
    return (
        await session.execute(
            text(
                "SELECT incumbent_sha256 FROM submissions WHERE slot = :slot "
                "AND incumbent_sha256 IS NOT NULL ORDER BY id DESC LIMIT 1"
            ),
            {"slot": slot},
        )
    ).scalar_one_or_none()


async def latest_fingerprint(session):
    """The verifier the newest verdict came from: provenance, as the gate records it."""
    return (
        await session.execute(
            text(
                "SELECT verifier_fingerprint FROM submissions WHERE verifier_fingerprint "
                "IS NOT NULL ORDER BY finished_at DESC NULLS LAST, id DESC LIMIT 1"
            )
        )
    ).scalar_one_or_none()


async def publication(session):
    """The latest published scoring, or None before the first: (weight-set id, payload)."""
    row = (
        await session.execute(
            text("""
        SELECT id, api_snapshot FROM weight_sets
        WHERE api_snapshot IS NOT NULL AND jsonb_typeof(api_snapshot) = 'object'
        ORDER BY id DESC LIMIT 1
    """)
        )
    ).first()
    return (int(row[0]), row[1]) if row is not None else None
