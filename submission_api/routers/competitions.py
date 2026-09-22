"""The competition surface: submit two signed files, read a submission, read the board.

One router for every competition rather than one per competition. The slug is a path
parameter resolved against the registry, so onboarding the next gate is a registry entry and
a results adapter, not a second copy of these handlers with a different prefix -- which is
the thing that would quietly drift apart. See `submission_api/competitions.py` for why this
deployment serves exactly one today.

Two ways in, and the difference is the credential, not the capability:

* `POST .../submissions` is signed by a subnet hotkey and carries its scalars in
  `X-Conjectures-*` headers. Those headers are not in `CORS_REQUEST_HEADERS`, which is what
  keeps this endpoint unreachable from a browser -- deliberately, because a browser has no
  business holding a hotkey.
* `POST .../submissions/session` is authorised by a session cookie and refuses a bearer
  token, exactly as the privileged `/v1/me` writes do.

Everything else is public: a leaderboard nobody can read is not a leaderboard, and a miner
reads their own submission by id.

Checks run cheapest-first, as they did in the service this came from: shape, then the rate
limit, then freshness, then the signature, then the entitlement. A malformed request is
refused before it costs a curve operation, and an unregistered hotkey before it costs a
write.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from fastapi import APIRouter, Header, Path, Request

from conjectures_subnet import scoring
from conjectures_subnet.competition import iso, queries
from conjectures_subnet.competition import models as competition_models
from submission_api import competition_sig, schemas_competitions as schemas
from submission_api.competitions import Competition, UnknownCompetition
from submission_api.dependencies import (
    CompetitionSessionDep,
    CookieWriterDep,
    PrincipalDep,
    ServicesDep,
)
from submission_api.errors import (
    BadRequest,
    Forbidden,
    NotFound,
    PayloadTooLarge,
    PaymentRequired,
    TooManyRequests,
    Unauthorized,
)
from submission_api.login import verify_signature
from submission_api.settings import MAX_COMPETITION_FILE_BYTES

router = APIRouter(prefix="/v1/competitions", tags=["competitions"])

SlugPath = Path(description="The competition's slug", max_length=64)


def _competition(services: ServicesDep, slug: str) -> Competition:
    """Resolve a slug, or 404.

    404 rather than 400: an unknown slug is an address that does not exist here, and saying
    so is the same answer whether the caller typed it wrong or is probing for a competition
    this deployment does not serve.
    """
    try:
        return services.competitions.get(slug)
    except UnknownCompetition as exc:
        raise NotFound(f"no competition {slug!r} is served here") from exc


def _view(competition: Competition, row: competition_models.Submission) -> schemas.SubmissionView:
    return schemas.SubmissionView(
        competition=competition.slug,
        id=row.id,
        hotkey=row.hotkey,
        digest=row.digest,
        submitted_at=iso(row.submitted_at),
        state=row.state,
        exit_code=row.exit_code,
        bytes=row.bytes,
        incumbent_bytes=row.incumbent_bytes,
        time_ratio=row.time_ratio,
    )


async def _summary(
    competition: Competition, session: CompetitionSessionDep
) -> schemas.CompetitionSummary:
    return schemas.CompetitionSummary(
        slug=competition.slug,
        name=competition.name,
        speed_floor=competition.speed_floor,
        incumbent_bytes=await queries.latest_incumbent_bytes(session),
        queued=await queries.queue_depth(session),
    )


# ── reading ────────────────────────────────────────────────────────────────


@router.get("", response_model=schemas.CompetitionIndex, summary="Competitions served here")
async def index(
    services: ServicesDep, session: CompetitionSessionDep
) -> schemas.CompetitionIndex:
    return schemas.CompetitionIndex(
        competitions=[
            await _summary(competition, session)
            for competition in services.competitions.competitions
        ]
    )


@router.get("/{slug}", response_model=schemas.CompetitionSummary, summary="One competition")
async def detail(
    services: ServicesDep, session: CompetitionSessionDep, slug: str = SlugPath
) -> schemas.CompetitionSummary:
    return await _summary(_competition(services, slug), session)


@router.get(
    "/{slug}/leaderboard", response_model=schemas.Leaderboard, summary="Standings"
)
async def leaderboard(
    services: ServicesDep, session: CompetitionSessionDep, slug: str = SlugPath
) -> schemas.Leaderboard:
    """Every hotkey's best accepted submission, fewest bytes first."""
    competition = _competition(services, slug)
    rows = await queries.leaderboard(session)
    incumbent = await queries.latest_incumbent_bytes(session)
    return schemas.Leaderboard(
        competition=competition.slug,
        incumbent_bytes=incumbent,
        speed_floor=competition.speed_floor,
        ranking=[
            schemas.Ranking(
                rank=position,
                submission=row.id,
                hotkey=row.hotkey,
                # The query selects only accepted rows with bytes recorded, so this is
                # never None in practice; the fallback keeps the type honest.
                bytes=row.bytes or 0,
                vs_incumbent=(
                    round((row.bytes or 0) / row.incumbent_bytes, 5)
                    if row.incumbent_bytes
                    else None
                ),
                time_ratio=row.time_ratio,
                submitted_at=iso(row.submitted_at),
            )
            for position, row in enumerate(rows, start=1)
        ],
    )


@router.get(
    "/{slug}/submissions/{submission_id}",
    response_model=schemas.SubmissionView,
    summary="One submission",
)
async def submission(
    services: ServicesDep,
    session: CompetitionSessionDep,
    slug: str = SlugPath,
    submission_id: int = Path(ge=1),
) -> schemas.SubmissionView:
    competition = _competition(services, slug)
    row = await queries.get_submission(session, submission_id)
    if row is None:
        raise NotFound("no such submission")
    return _view(competition, row)


@router.get(
    "/{slug}/submissions/{submission_id}/report",
    response_model=schemas.SubmissionReport,
    summary="The gate's report",
)
async def report(
    services: ServicesDep,
    session: CompetitionSessionDep,
    slug: str = SlugPath,
    submission_id: int = Path(ge=1),
) -> schemas.SubmissionReport:
    """The stage-by-stage output: how a miner finds out which stage refused them.

    Public, like the submission itself. What it contains is the gate's verdict on the
    miner's own files, and a competition whose refusals cannot be read is one nobody can
    improve against.
    """
    competition = _competition(services, slug)
    row = await queries.get_submission(session, submission_id)
    if row is None:
        raise NotFound("no such submission")
    return schemas.SubmissionReport(
        competition=competition.slug,
        id=row.id,
        state=row.state,
        exit_code=row.exit_code,
        report=row.report,
    )


@router.get(
    "/{slug}/weights/current",
    response_model=schemas.WeightVector,
    summary="Current per-hotkey scores",
)
async def weights(
    services: ServicesDep, session: CompetitionSessionDep, slug: str = SlugPath
) -> schemas.WeightVector:
    """What this competition would pay, as scores rather than as a weight vector.

    Read by `emissions_worker` over HTTP rather than from the database, because that
    process holds the only key on the subnet that can set weights and deliberately has no
    database credential at all -- see `docker-compose.emissions.yml`. Giving it one to save
    this call would trade a real isolation property for a convenience.

    Public, like the leaderboard it restates: it is derived entirely from accepted
    submissions that are already public, and a subnet whose payout reasoning is secret is
    one nobody can check.
    """
    competition = _competition(services, slug)
    best = await queries.scorable_best_per_hotkey(session)
    history = await queries.scorable_history(session)
    result = scoring.score(best, history, scoring.ScoringConfig.from_env())
    return schemas.WeightVector(
        competition=competition.slug,
        computed_at=iso(datetime.now(UTC)),
        # `Scoring.weights` already drops the zeros, so a hotkey that scored nothing does
        # not travel as an explicit zero the reader would have to filter again.
        weights=result.weights,
        scored_submissions=len(best),
    )


# ── writing ────────────────────────────────────────────────────────────────


async def _read_pair(request: Request) -> tuple[bytes, bytes]:
    """The two submitted files, under a running cap.

    Parsed here rather than declared as `UploadFile` parameters, and that is an ordering
    property rather than a style choice. FastAPI resolves `File()` parameters during
    dependency resolution, which runs *before* the handler body -- so declaring them would
    put a form parser over untrusted bytes ahead of the rate limit and the signature check,
    and would make this endpoint's documented cheapest-first order untrue. Calling it here
    means nothing parses a body until the caller has been rate limited and their timestamp
    found fresh.

    Read one byte past the cap so an oversized upload is refused by what actually arrived
    rather than by a declared `Content-Length`, which the client controls.
    """
    try:
        form = await request.form(max_files=2, max_fields=0)
    except Exception as exc:  # noqa: BLE001 - a body that will not parse is a bad request
        raise BadRequest("the request body is not a valid multipart form") from exc
    try:
        rust_part = form["parse.rs"]
        lean_part = form["Parse.lean"]
        rust = await rust_part.read(MAX_COMPETITION_FILE_BYTES + 1)  # pyright: ignore[reportAttributeAccessIssue]
        lean = await lean_part.read(MAX_COMPETITION_FILE_BYTES + 1)  # pyright: ignore[reportAttributeAccessIssue]
    except (KeyError, AttributeError) as exc:
        raise BadRequest(
            "the body must carry exactly two file parts, parse.rs and Parse.lean"
        ) from exc
    finally:
        await form.close()
    if len(rust) > MAX_COMPETITION_FILE_BYTES or len(lean) > MAX_COMPETITION_FILE_BYTES:
        raise PayloadTooLarge(
            f"each file must be at most {MAX_COMPETITION_FILE_BYTES} bytes"
        )
    if not rust or not lean:
        raise BadRequest("both parse.rs and Parse.lean must be non-empty")
    return rust, lean


async def _queue(
    *,
    competition: Competition,
    session: CompetitionSessionDep,
    services: ServicesDep,
    hotkey: str,
    rust: bytes,
    lean: bytes,
    digest: str,
    account_id=None,
) -> schemas.SubmissionAccepted:
    """The half both write paths share: idempotency, entitlement, insert, commit.

    One registration buys one accepted submission. The slot is checked here and spent only
    when the gate accepts, so a submission the gate rejects costs the miner nothing -- which
    is what keeps a failed attempt at a hard problem from being punished.

    **The idempotency lookup comes first, and that ordering is a fix rather than a port.**
    The service this came from checked the entitlement before looking for an existing row,
    so a miner whose response was lost in transit and who retried the same two files was
    told they had no slots left -- while the submission they were retrying sat queued,
    holding the very slot being reported as spent. The same two files from the same hotkey
    are the same submission, so returning it is the honest answer and costs no entitlement.
    """
    existing = await queries.find_submission(session, hotkey=hotkey, digest=digest)
    if existing is not None:
        _, slots, pending = await queries.may_queue(session, hotkey)
        return schemas.SubmissionAccepted(
            competition=competition.slug,
            submission=existing.id,
            state=existing.state,
            digest=digest,
            slots_remaining=max(slots - pending, 0),
        )
    if not await queries.is_registered(session, hotkey):
        raise PaymentRequired(
            "this hotkey is not registered on the subnet",
            reason_code="NOT_REGISTERED",
        )
    allowed, slots, pending = await queries.may_queue(session, hotkey)
    if not allowed:
        raise PaymentRequired(
            f"{slots} unclaimed registration(s) and {pending} already in the queue: "
            "one registration buys one accepted submission, so register again to submit again",
            reason_code="NO_ENTITLEMENT",
        )
    # `add_submission` still resolves a conflict rather than raising, because two requests
    # carrying the same files can race past the lookup above; the unique index is what
    # actually decides, and the loser gets the winner's id.
    submission_id, _fresh = await queries.add_submission(
        session,
        hotkey=hotkey,
        digest=digest,
        parse_source=rust,
        proof_source=lean,
        account_id=account_id,
    )
    await session.commit()
    row = await queries.get_submission(session, submission_id)
    assert row is not None, "add_submission either inserted this row or found the existing one"
    return schemas.SubmissionAccepted(
        competition=competition.slug,
        submission=submission_id,
        state=row.state,
        digest=digest,
        # How many MORE this hotkey could queue right now, which is the question a miner is
        # actually asking. Not the count of unclaimed registrations: a queued submission has
        # not spent one yet -- only acceptance does -- so that number would stay flat while
        # the miner's ability to queue another had already gone. `pending` includes the row
        # just inserted.
        slots_remaining=max(slots - (pending + 1), 0),
    )


@router.post(
    "/{slug}/submissions",
    response_model=schemas.SubmissionAccepted,
    status_code=201,
    summary="Submit, signed by a subnet hotkey",
)
async def submit(
    request: Request,
    services: ServicesDep,
    session: CompetitionSessionDep,
    slug: str = SlugPath,
    hotkey: str = Header(..., alias="X-Conjectures-Hotkey"),
    timestamp: int = Header(..., alias="X-Conjectures-Timestamp"),
    signature: str = Header(..., alias="X-Conjectures-Signature"),
) -> schemas.SubmissionAccepted:
    """Accept two files signed by a registered hotkey and queue them for the gate.

    Submitting the same two files again returns the same id and does not re-queue them.

    The scalars are headers rather than form fields on purpose: `CORS_REQUEST_HEADERS`
    allowlists no `X-Conjectures-*` header, which is what keeps this endpoint unreachable
    from a browser even on an allowlisted origin. See `competition_sig`.
    """
    competition = _competition(services, slug)
    settings = services.settings

    allowed, _hits = await queries.hit_rate_limit(
        session,
        f"hotkey:{hotkey}",
        limit=settings.competition_rate_per_minute,
        window_seconds=60,
    )
    await session.commit()
    if not allowed:
        raise TooManyRequests(
            f"more than {settings.competition_rate_per_minute} submissions a minute "
            "from this hotkey",
            reason_code="RATE_LIMITED",
        )

    # Freshness before the signature: a stale request is refused without a curve operation.
    drift = abs(int(time.time()) - timestamp)
    if drift > settings.competition_signature_window_seconds:
        raise Unauthorized(
            "the signed timestamp is outside the freshness window",
            reason_code="SIGNATURE_EXPIRED",
        )

    rust, lean = await _read_pair(request)
    digest = competition_sig.digest_of(rust, lean)
    # Rebuilt from the bytes actually read and the slug actually resolved, never from what
    # the request claimed.
    verify_signature(
        address=hotkey,
        message=competition_sig.submit_message(
            competition=competition.slug,
            digest=digest,
            hotkey=hotkey,
            timestamp=timestamp,
        ),
        signature=bytes.fromhex(signature.removeprefix("0x"))
        if _is_hex(signature)
        else b"",
    )
    return await _queue(
        competition=competition,
        session=session,
        services=services,
        hotkey=hotkey,
        rust=rust,
        lean=lean,
        digest=digest,
    )


def _is_hex(value: str) -> bool:
    candidate = value.removeprefix("0x")
    return bool(candidate) and len(candidate) % 2 == 0 and all(
        c in "0123456789abcdefABCDEF" for c in candidate
    )


@router.post(
    "/{slug}/submissions/session",
    response_model=schemas.SubmissionAccepted,
    status_code=201,
    summary="Submit as a signed-in account",
)
async def submit_as_account(
    request: Request,
    services: ServicesDep,
    session: CompetitionSessionDep,
    principal: CookieWriterDep,
    slug: str = SlugPath,
    hotkey: str = Header(..., alias="X-Conjectures-Hotkey"),
) -> schemas.SubmissionAccepted:
    """Submit on behalf of a signed-in account, with no hotkey signature.

    `CookieWriterDep` refuses a bearer token: this is the browser's path, and a CLI holding
    a bearer should be using the signed one.

    The account still has to be entitled to the hotkey it names. It is not asked to sign
    with it -- a browser has no business holding a hotkey -- so entitlement is established
    the other way round: the account owns a `submission_coldkey`, and a registration records
    which coldkey put which hotkey on the subnet. If they agree, this account's own coldkey
    registered that hotkey.

    Two reads across two databases rather than a join, because there is no foreign key
    between them and no transaction spanning them. That is sound here only because both
    halves are immutable history -- a registration row is never rewritten, and changing an
    account's coldkey cannot retroactively un-register anything -- so there is no window in
    which the two reads could disagree about the past.
    """
    competition = _competition(services, slug)
    coldkey = principal.account.submission_coldkey
    if not coldkey:
        raise PaymentRequired(
            "this account has no submission coldkey: link one before submitting",
            reason_code="NO_SUBMISSION_COLDKEY",
        )
    if not await queries.registered_by(session, hotkey=hotkey, coldkey=coldkey):
        # Deliberately not "that hotkey belongs to someone else": the account learns only
        # that *its own* coldkey did not register it, which is the fact it can act on.
        raise Forbidden(
            "this account's submission coldkey did not register that hotkey",
            reason_code="HOTKEY_NOT_YOURS",
        )
    rust, lean = await _read_pair(request)
    digest = competition_sig.digest_of(rust, lean)
    return await _queue(
        competition=competition,
        session=session,
        services=services,
        hotkey=hotkey,
        rust=rust,
        lean=lean,
        digest=digest,
        account_id=principal.account.id,
    )


# ── the account's own ──────────────────────────────────────────────────────

# A second router rather than routes on `me.router`, so every competition handler stays in
# one module: the path belongs under `/v1/me` (everything the signed-in account owns lives
# there) but the code belongs here. Registered next to `me.router` in `create_app`.
me_router = APIRouter(prefix="/v1/me/competitions", tags=["competitions"])


@me_router.get(
    "/submissions",
    response_model=schemas.AccountSubmissions,
    summary="Submissions made through this account",
)
async def my_submissions(
    services: ServicesDep,
    session: CompetitionSessionDep,
    principal: PrincipalDep,
) -> schemas.AccountSubmissions:
    """This account's own submissions, newest first.

    Only the ones made through a signed-in session: a hotkey-signed submit sets no
    `account_id`, so it never appears here even when the same person made it. That is the
    honest reading of the column -- it records which account authorised the write, and
    nothing about a signature says an account was involved at all.
    """
    competition = services.competitions.only()
    rows = await queries.submissions_for_account(session, principal.account.id)
    return schemas.AccountSubmissions(
        submissions=[_view(competition, row) for row in rows]
    )
