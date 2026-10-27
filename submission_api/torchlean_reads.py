"""Read-only projections of competition-owned evidence. No scoring or bootstrap here."""

from sqlalchemy import text

from submission_api import schemas_torchlean as s
from submission_api.errors import NotFound, ServiceUnavailable

# A proved entry can await planning, not just await an already-created job. Completed means
# every eligible active board has a completed evaluation; it does not mean promoted.
PROCESSING = """
CASE WHEN EXISTS (SELECT 1 FROM source_disqualifications d
                  WHERE d.source_hash=s.source_sha256 AND d.active) THEN 'disqualified'
 WHEN s.state = 'queued' THEN 'queued'
 WHEN s.state = 'running' THEN 'proving'
 WHEN s.state = 'rejected' THEN 'rejected'
 WHEN EXISTS (SELECT 1 FROM evaluations e
              WHERE e.submission_id=s.id AND e.state='pending') THEN 'benchmarking'
 WHEN NOT EXISTS (SELECT 1 FROM coordinator_state WHERE id=1 AND config_evidence IS NOT NULL)
   OR EXISTS (
     SELECT 1 FROM board_progress bp WHERE s.id > bp.activation_boundary
       AND (SELECT config_evidence->'boards' FROM coordinator_state WHERE id=1) ? bp.board
       AND NOT EXISTS (SELECT 1 FROM evaluations e WHERE e.submission_id=s.id
                       AND e.board=bp.board AND e.state='completed')
   ) THEN 'waiting'
 ELSE 'completed' END
"""

ELIGIBLE = """p.disqualified_at IS NULL AND NOT EXISTS (
 SELECT 1 FROM source_disqualifications d WHERE d.source_hash=s.source_sha256 AND d.active)
"""


async def rows(session, query, **params):
    return [dict(r) for r in (await session.execute(text(query), params)).mappings()]


async def state(session):
    result = await rows(
        session,
        "SELECT config_hash,config_evidence,publication,publication_hash "
        "FROM coordinator_state WHERE id=1",
    )
    return (
        result[0]
        if result
        else {
            "config_hash": None,
            "config_evidence": None,
            "publication": None,
            "publication_hash": None,
        }
    )


async def counts(session):
    result = await rows(
        session,
        f"SELECT processing_state,count(*) AS n FROM "
        f"(SELECT {PROCESSING} AS processing_state FROM submissions s) q "
        "GROUP BY processing_state",
    )
    return {r["processing_state"]: r["n"] for r in result}


async def visible_sources(session, hashes):
    if not hashes:
        return set()
    # Compare source hashes, not submission IDs; another hotkey cannot disclose a current king.
    result = await rows(
        session,
        f"""
        WITH current AS (
          SELECT DISTINCT ON (p.board) p.board,s.source_sha256
          FROM promotions p JOIN evaluations e ON e.id=p.evaluation_id
          JOIN submissions s ON s.id=e.submission_id
          WHERE {ELIGIBLE} ORDER BY p.board,p.id DESC
        )
        SELECT DISTINCT s.source_sha256 FROM promotions p
        JOIN evaluations e ON e.id=p.evaluation_id JOIN submissions s ON s.id=e.submission_id
        WHERE s.source_sha256=ANY(:hashes)
        AND NOT EXISTS (SELECT 1 FROM current c WHERE c.source_sha256=s.source_sha256)
    """,
        hashes=list(hashes),
    )
    return {r["source_sha256"] for r in result}


async def history(session, board):
    return await rows(
        session,
        f"""
        SELECT p.id AS promotion_id,p.promoted_at,p.disqualified_at,
          e.id AS evaluation_id,e.generation,e.config_hash,e.decision,
          s.id AS submission,s.hotkey,s.source_sha256,
          NOT ({ELIGIBLE}) AS disqualified,
          EXISTS (SELECT 1 FROM source_disqualifications d
                  WHERE d.source_hash=s.source_sha256 AND d.active) AS source_disqualified
        FROM promotions p JOIN evaluations e ON e.id=p.evaluation_id
        JOIN submissions s ON s.id=e.submission_id
        WHERE p.board=:board ORDER BY p.id DESC
    """,
        board=board,
    )


def current(history):
    return next((p for p in history if not p["disqualified"]), None)


def admission(value):
    return s.TorchLeanAdmission(**{k: value.get(k) for k in s.TorchLeanAdmission.model_fields}) if value else None


def evaluation(row):
    return s.TorchLeanEvaluation(
        **{k: row[k] for k in s.TorchLeanEvaluation.model_fields if k not in ("decision", "jobs")},
        decision=admission(row["decision"]),
        jobs=s.TorchLeanJobCounts(**row["jobs"]),
    )


async def evaluations(session, ids):
    if not ids:
        return []
    return await rows(
        session,
        """
        SELECT e.id,e.submission_id,e.board,e.generation,e.state,e.reference_promotion_id,
          e.reference_hash,e.config_hash,e.decision,e.created_at,e.completed_at,
          jsonb_build_object(
            'queued',count(j.id) FILTER (WHERE j.state='queued'),
            'running',count(j.id) FILTER (WHERE j.state='running'),
            'completed',count(j.id) FILTER (WHERE j.state='completed'),
            'failed',count(j.id) FILTER (WHERE j.state='failed'),
            'cancelled',count(j.id) FILTER (WHERE j.state='cancelled')) AS jobs
        FROM evaluations e LEFT JOIN evaluation_jobs ej ON ej.evaluation_id=e.id
        LEFT JOIN benchmark_jobs j ON j.id=ej.job_id
        WHERE e.submission_id=ANY(:ids) GROUP BY e.id ORDER BY e.id
    """,
        ids=ids,
    )


async def submissions(session, *, sid=None, processing=None):
    data = await rows(
        session,
        f"""
        SELECT * FROM (
          SELECT s.id,s.hotkey,s.source_sha256,s.submitted_at,s.state AS proof_state,
            s.stage,s.reason,s.intake_mode,{PROCESSING} AS processing_state
          FROM submissions s WHERE (CAST(:sid AS bigint) IS NULL OR s.id=CAST(:sid AS bigint))
        ) q WHERE (CAST(:processing AS text) IS NULL OR processing_state=CAST(:processing AS text))
        ORDER BY submitted_at DESC,id DESC
    """,
        sid=sid,
        processing=processing,
    )
    if sid is not None and not data:
        raise NotFound("No such TorchLean submission")
    evidence = await evaluations(session, [r["id"] for r in data])
    visible = await visible_sources(session, {r["source_sha256"] for r in data})
    grouped = {}
    for e in evidence:
        grouped.setdefault(e["submission_id"], []).append(evaluation(e))
    return [
        s.TorchLeanSubmission(
            **r,
            evaluations=grouped.get(r["id"], []),
            source_available=r["source_sha256"] in visible,
        )
        for r in data
    ]


def board_data(snapshot, board):
    config = snapshot["config_evidence"] or {}
    defined = config.get("boards", {}).get(board)
    if defined is None:
        raise NotFound("No such active TorchLean board")
    published = (snapshot["publication"] or {}).get("boards", {}).get(board, {})
    return config, defined, published


async def boards(session, snapshot):
    config = snapshot["config_evidence"] or {}
    publication = snapshot["publication"] or {}
    policy = publication.get("rewards", {})
    result = []
    # One query for pause diagnostics, no raw error payloads exposed.
    paused = await rows(
        session,
        """
        SELECT DISTINCT e.board,j.error->>'kind' AS kind FROM evaluations e
        JOIN evaluation_jobs ej ON ej.evaluation_id=e.id JOIN benchmark_jobs j ON j.id=ej.job_id
        WHERE e.state='pending' AND j.state IN ('queued','running')
          AND j.error->>'kind' IN ('reference','checksum')
    """,
    )
    pauses = {r["board"]: r["kind"] for r in paused}
    for name, board in config.get("boards", {}).items():
        published = publication.get("boards", {}).get(name, {})
        envs = config.get("environments", {})
        weights = board.get("environments", {k: v["weight"] for k, v in envs.items()})
        kings = published.get("kings", [])
        result.append(
            s.TorchLeanBoard(
                id=name,
                config_hash=published.get("config_hash"),
                models=board["models"],
                reward_share=policy.get("board_weights", {}).get(name),
                promotion_bar=config.get("admission", {}).get("bar"),
                guard_limit=config.get("admission", {}).get("guard"),
                environments=[
                    s.TorchLeanEnvironment(id=k, weight=w, identity=envs.get(k, {}).get("identity"))
                    for k, w in weights.items()
                    if w > 0
                ],
                current_promotion_id=kings[0]["promotion_id"] if kings else None,
                paused=published.get("paused"),
                pause_reason=pauses.get(name),
            )
        )
    return s.TorchLeanBoards(
        configuration_hash=snapshot["config_hash"],
        publication_hash=snapshot["publication_hash"],
        boards=result,
    )


async def leaderboard(session, snapshot, board, promotion_id=None, config_hash=None):
    hist = await history(session, board)
    live = current(hist)
    selected = live
    if promotion_id is not None:
        selected = next((p for p in hist if p["promotion_id"] == promotion_id), None)
        if selected is None:
            raise NotFound("No such champion on this board")
    active = board in (snapshot["config_evidence"] or {}).get("boards", {})
    if promotion_id is None and not active:
        raise NotFound("No such active TorchLean board")
    config = None
    defined = None
    if active:
        _, defined, published = board_data(snapshot, board)
        if promotion_id is None or (
            selected and live and selected["promotion_id"] == live["promotion_id"]
        ):
            config = published.get("config_hash")
            if config is None and config_hash is None:
                raise ServiceUnavailable(
                    "The coordinator must publish board configuration hashes before current reads.",
                    reason_code="COMPETITION_POLICY_UNAVAILABLE",
                )
    reference_id = selected["promotion_id"] if selected else None
    if config_hash is not None:
        config = config_hash
    elif config is None and selected:
        groups = await rows(
            session,
            """
          SELECT config_hash FROM evaluations WHERE board=:board AND reference_promotion_id=:pid
          ORDER BY id DESC LIMIT 1
        """,
            board=board,
            pid=reference_id,
        )
        config = groups[0]["config_hash"] if groups else selected["config_hash"]
    refhash = selected["source_sha256"] if selected else defined["reference"]["sha256"]
    data = await rows(
        session,
        """
        SELECT e.id AS evaluation_id,e.generation,e.state,e.completed_at,e.decision,
          s.id AS submission,s.hotkey,s.source_sha256,p.id AS promotion_id
        FROM evaluations e JOIN submissions s ON s.id=e.submission_id
        LEFT JOIN promotions p ON p.evaluation_id=e.id
        WHERE e.board=:board AND e.reference_promotion_id IS NOT DISTINCT FROM CAST(:pid AS bigint)
          AND e.reference_hash=:refhash AND e.config_hash=:config
          AND e.state != 'superseded'
        ORDER BY e.id
    """,
        board=board,
        pid=reference_id,
        refhash=refhash,
        config=config,
    )
    return s.TorchLeanLeaderboard(
        board=board,
        config_hash=config,
        configuration_hash=snapshot["config_hash"] if promotion_id is None else None,
        reference=s.TorchLeanReference(
            promotion_id=reference_id,
            submission=selected["submission"] if selected else None,
            source_sha256=refhash,
            shipped=selected is None,
        ),
        challengers=[
            s.TorchLeanChallenger(
                **{k: r[k] for k in s.TorchLeanChallenger.model_fields if k not in ("ratio", "admission")},
                ratio=(r["decision"] or {}).get("score"),
                admission=(r["decision"] or {}).get("outcome"),
            )
            for r in data
        ],
    )


async def champions(session, snapshot, board):
    hist = await history(session, board)
    active = board in (snapshot["config_evidence"] or {}).get("boards", {})
    if not hist and not active:
        raise NotFound("No such TorchLean board")
    live = current(hist)
    publication = snapshot["publication"] or {}
    policy = publication.get("rewards", {})
    board_state = publication.get("boards", {}).get(board, {})
    ranks = {k["promotion_id"]: i for i, k in enumerate(board_state.get("kings", []))}
    shares = policy.get("rank_shares", [])
    visible = await visible_sources(session, {p["source_sha256"] for p in hist})
    result = []
    for p in hist:
        index = ranks.get(p["promotion_id"])
        paid = not p["disqualified"] and index is not None and index < len(shares)
        share = shares[index] if paid else None
        fraction = share * policy["board_weights"][board] if paid else None
        if (
            fraction is not None
            and board_state.get("paused")
            and policy.get("paused_policy") == "treasury"
        ):
            fraction = 0.0
        result.append(
            s.TorchLeanChampion(
                **{
                    k: p[k]
                    for k in (
                        "promotion_id",
                        "generation",
                        "config_hash",
                        "submission",
                        "hotkey",
                        "source_sha256",
                        "promoted_at",
                        "disqualified_at",
                        "source_disqualified",
                    )
                },
                ratio=(p["decision"] or {}).get("score"),
                current=live is not None and p["promotion_id"] == live["promotion_id"],
                reward_rank=index + 1 if paid else None,
                rank_share=share,
                competition_fraction=fraction,
                source_available=p["source_sha256"] in visible,
            )
        )
    return s.TorchLeanChampions(
        board=board, active=active, publication_hash=snapshot["publication_hash"], champions=result
    )


async def report(session, sid):
    sub = (await submissions(session, sid=sid))[0]
    plans = await rows(
        session, "SELECT id,plan,decision FROM evaluations WHERE submission_id=:sid", sid=sid
    )
    data = await rows(
        session,
        """
       SELECT ej.evaluation_id,ej.environment,ej.seed,j.state,j.attempts,j.started_at,j.completed_at,
         j.result,j.error->>'kind' AS error_kind FROM evaluation_jobs ej
       JOIN benchmark_jobs j ON j.id=ej.job_id WHERE ej.evaluation_id IN
         (SELECT id FROM evaluations WHERE submission_id=:sid)
       ORDER BY ej.evaluation_id,ej.environment,ej.seed
    """,
        sid=sid,
    )
    result = []
    for e in sub.evaluations:
        saved = next(p for p in plans if p["id"] == e.id)
        plan = saved["plan"]
        measurements = []
        for j in data:
            if j["evaluation_id"] != e.id:
                continue
            value = j["result"] or {}
            # A stale payload on a failed job remains diagnostic evidence, never a fresh score.
            diagnostic = next(
                (
                    d
                    for d in (saved["decision"] or {}).get("strata", [])
                    if d.get("environment") == j["environment"] and d.get("seed") == j["seed"]
                ),
                {},
            )
            measurements.append(
                s.TorchLeanMeasurement(
                    **{
                        k: j[k]
                        for k in (
                            "environment",
                            "seed",
                            "state",
                            "attempts",
                            "started_at",
                            "completed_at",
                            "error_kind",
                        )
                    },
                    identity=value.get("provenance", {}).get("identity"),
                    repetitions=value.get("repetitions"),
                    rounds=[
                        s.TorchLeanRound(
                            reference_first=r["reference_first"],
                            cells={
                                name: s.TorchLeanObservation(
                                    **{k: obs[k] for k in s.TorchLeanObservation.model_fields}
                                )
                                for name, obs in r["cells"].items()
                            },
                        )
                        for r in value["rounds"]
                    ]
                    if value.get("rounds") is not None
                    else None,
                    score=diagnostic.get("score"),
                    guards=diagnostic.get("guards"),
                )
            )
        result.append(
            s.TorchLeanEvaluationReport(
                **e.model_dump(),
                cells=plan.get("cells", {}),
                timing=plan.get("timing", {}),
                admission_policy=plan.get("admission", {}),
                measurements=measurements,
            )
        )
    return s.TorchLeanReport(submission=sub, evaluations=result)
