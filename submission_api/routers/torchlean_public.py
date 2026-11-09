"""Public dashboard reads of a kernel competition's persisted decisions and timing evidence.

`build_router(slug, session)` serves them for one competition from its own database; TorchLean
and Tensor kernels each get a copy (`routers/torchlean.py`).
"""

from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from starlette.responses import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from submission_api import schemas_compression as catalog
from submission_api import schemas_torchlean as s
from submission_api import torchlean_reads as store
from submission_api.competitions import TENSOR_KERNELS, KERNEL_COMPETITIONS, TORCHLEAN, Competition
from submission_api.dependencies import ServicesDep
from submission_api.errors import Forbidden
from submission_api.kernel_upload import MAX_FILE_BYTES
from submission_api.routers._account import decode_id_cursor, encode_id_cursor
from submission_api.routers.competitions import resolve_competition
from submission_api.routers.torchlean import competition_session, intake_setting
from submission_api.settings import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

BoardQuery = Annotated[str, Query(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]
SubmissionId = Annotated[int, Path(ge=1)]


async def prepare(session):
    # Pin policy, references, reward ranks and jobs to one snapshot even during promotion.
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    from submission_api.torchlean_store import compatible

    await compatible(session)
    return session


DESCRIPTIONS = {
    TORCHLEAN: "Proved exact tensor kernels, evaluated on model-family boards",
    TENSOR_KERNELS: "Proved exact tensor kernels for CPU inference, evaluated on model-family boards",
}


class TorchLeanOverview(catalog.Competition):
    boards: list[str]
    max_file_bytes: int = MAX_FILE_BYTES


async def overview(competition, services, session):
    snapshot = await store.state(session)
    config = snapshot["config_evidence"]
    counts = await store.counts(session)
    return TorchLeanOverview(
        slug=competition.slug,
        name=competition.name,
        description=DESCRIPTIONS[competition.slug],
        submissions_open=intake_setting(services.settings, competition.slug, "submissions_enabled")
        and not services.settings.submissions_paused,
        queue_depth=sum(counts.get(k, 0) for k in ("queued", "proving", "waiting", "benchmarking")),
        current_snapshot_id=snapshot["publication_hash"],
        policy=(snapshot["publication"] or {}).get("rewards"),
        execution_limits=(config or {}).get("timing", {}),
        files=[{"name": "Kernel.lean", "max_bytes": MAX_FILE_BYTES}],
        metric_definitions=[
            {"key": "ratio", "label": "Time / reference", "unit": "ratio", "better": "lower"}
        ],
        policy_status="ready" if config and snapshot["publication"] else "unknown",
        context=catalog.Context(
            snapshot_id=snapshot["publication_hash"],
            status="ready" if config and snapshot["publication"] else "not_ready",
            freshness="unknown",
        ),
        boards=list((config or {}).get("boards", {})),
    )


async def listed(competition: Competition, services):
    # Same unreachable/schema translation as all other requests to this competition.
    async with asynccontextmanager(competition_session(competition.slug))(services) as session:
        await prepare(session)
        return await overview(competition, services, session)


def build_router(slug: str, session_dependency) -> APIRouter:
    """The public reads of the kernel competition `slug`, relative to its base path."""
    if slug not in KERNEL_COMPETITIONS:
        raise ValueError(f"not a kernel competition: {slug!r}")

    async def readonly(session: Annotated[AsyncSession, Depends(session_dependency)]):
        return await prepare(session)

    ReadSession = Annotated[AsyncSession, Depends(readonly)]
    router = APIRouter()

    @router.get("", response_model=TorchLeanOverview)
    async def detail(services: ServicesDep, session: ReadSession):
        return await overview(resolve_competition(services, slug, TORCHLEAN), services, session)

    @router.get("/health", response_model=s.TorchLeanHealth)
    async def health(session: ReadSession):
        snapshot = await store.state(session)
        counts = await store.counts(session)
        publication = snapshot["publication"] or {}
        policy = publication.get("rewards", {})
        return s.TorchLeanHealth(
            ok=True,
            policy_ready=bool(snapshot["config_evidence"] and publication),
            queued=counts.get("queued", 0),
            proving=counts.get("proving", 0),
            waiting=counts.get("waiting", 0),
            benchmarking=counts.get("benchmarking", 0),
            rank_shares=policy.get("rank_shares"),
            paused_policy=policy.get("paused_policy"),
            publication_hash=snapshot["publication_hash"],
        )

    @router.get("/boards", response_model=s.TorchLeanBoards)
    async def boards(session: ReadSession):
        return await store.boards(session, await store.state(session))

    @router.get("/leaderboard", response_model=s.TorchLeanLeaderboard)
    async def leaderboard(
        session: ReadSession,
        board: BoardQuery = "overall",
        promotion_id: Annotated[int | None, Query(ge=1)] = None,
        config_hash: Annotated[str | None, Query(pattern=r"^[0-9a-f]{64}$")] = None,
    ):
        """One reference/configuration only. Older groups can be selected by their config hash."""
        return await store.leaderboard(
            session, await store.state(session), board, promotion_id, config_hash
        )

    @router.get("/champions", response_model=s.TorchLeanChampions)
    async def champions(session: ReadSession, board: BoardQuery = "overall"):
        return await store.champions(session, await store.state(session), board)

    @router.get("/submissions", response_model=s.TorchLeanSubmissionPage)
    async def submissions(
        services: ServicesDep,
        session: ReadSession,
        state: Annotated[s.TorchLeanProcessingState | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
        cursor: Annotated[str | None, Query(max_length=256)] = None,
    ):
        """One page, newest first; pass `next_cursor` back as `cursor` with the same `state`."""
        before = decode_id_cursor(services.settings, cursor)
        page = await store.submissions(session, processing=state, before=before, limit=limit + 1)
        more = len(page) > limit
        page = page[:limit]
        return s.TorchLeanSubmissionPage(
            submissions=page,
            next_cursor=encode_id_cursor(services.settings, page[-1].id) if more else None,
        )

    @router.get("/submissions/{submission_id}", response_model=s.TorchLeanSubmission)
    async def submission(submission_id: SubmissionId, session: ReadSession):
        return (await store.submissions(session, sid=submission_id))[0]

    @router.get("/submissions/{submission_id}/report", response_model=s.TorchLeanReport)
    async def report(submission_id: SubmissionId, session: ReadSession):
        return await store.report(session, submission_id)

    @router.get(
        "/submissions/{submission_id}/source",
        response_class=Response,
        responses={200: {"content": {"text/plain": {"schema": {"type": "string"}}}}},
    )
    async def source(submission_id: SubmissionId, session: ReadSession):
        """Only former champions whose source is no longer current on any board are public."""
        sub = (await store.submissions(session, sid=submission_id))[0]
        if not sub.source_available:
            raise Forbidden(
                "Source is published only for former champions that hold no board.",
                reason_code="SOURCE_WITHHELD",
            )
        content = await session.scalar(
            text("SELECT content FROM sources WHERE sha256=:sha"), {"sha": sub.source_sha256}
        )
        return Response(
            bytes(content),
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="Kernel.lean"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return router
