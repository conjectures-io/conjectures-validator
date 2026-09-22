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
reads their own submission by id. The public reads are the whole of what a website is built
from -- the board, the feed of every attempt, one submission and its report, an accepted
entry's source, a competitor's standing, and the aggregate counts -- because a competition
whose record is only visible to whoever holds a database credential is one nobody outside can
check. The one surface that is not public is the operator's, in `competitions_admin.py`, and
what is behind that role is the validator's queue bookkeeping rather than anything about the
competition itself.

The feeds are keyset-paged with signed cursors, like `/v1/results`. Two cursor shapes, since
the board is ranked by bytes and the feeds by arrival, and one version tag each so a cursor
issued for one is refused by the other rather than reinterpreted into a plausible wrong page.

Checks run cheapest-first, as they did in the service this came from: the pause, then shape,
then the rate limit, then freshness, then the signature, then the entitlement. A paused
surface refuses before anything parses a body, a malformed request before it costs a curve
operation, and an unregistered hotkey before it costs a write.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Header, Path, Query, Request

from conjectures_subnet import scoring
from conjectures_subnet.competition import iso, queries
from conjectures_subnet.competition import models as competition_models
from conjectures_subnet.competition.status import STATE_VALUES, SubmissionState
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
    Conflict,
    Forbidden,
    NotFound,
    PayloadTooLarge,
    PaymentRequired,
    ServiceUnavailable,
    TooManyRequests,
    Unauthorized,
)
from submission_api.login import verify_signature
from submission_api.pagination import decode_parts, encode_parts
from submission_api.routers.submissions import REASON_SUBMISSIONS_PAUSED
from submission_api.settings import (
    DEFAULT_PAGE_SIZE,
    MAX_COMPETITION_FILE_BYTES,
    MAX_PAGE_SIZE,
)

router = APIRouter(prefix="/v1/competitions", tags=["competitions"])

SlugPath = Path(description="The competition's slug", max_length=64)
# An ss58 address is 47-48 base58 characters. Bounded rather than matched, because the address
# is only ever used as an equality predicate and a pattern here would be a second, weaker copy
# of the check `verify_signature` already makes on the addresses that matter.
HotkeyPath = Path(description="A subnet hotkey (ss58)", min_length=2, max_length=64)

LimitQuery = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)]
CursorQuery = Annotated[str | None, Query(max_length=256)]

# Two cursor shapes, because these feeds are ordered by two different keys, and one version
# string each so a cursor from one is refused by the other rather than reinterpreted into a
# coherent-looking wrong answer. Neither is the platform's `(created_at, uuid)` pair: a
# competition submission's id is a BIGINT.
CURSOR_FEED = "cs1"  # (submitted_at, id), newest first
CURSOR_RANK = "cr1"  # (bytes, submitted_at, id), best first


def _micros(moment: datetime) -> str:
    """A timestamp as an integer, so a cursor never depends on how a fraction is formatted."""
    return str(int(moment.astimezone(UTC).timestamp() * 1_000_000))


def _from_micros(value: str) -> datetime:
    return datetime.fromtimestamp(int(value) / 1_000_000, tz=UTC)


def feed_cursor(secret: str, row: competition_models.Submission) -> str:
    return encode_parts(
        secret, version=CURSOR_FEED, parts=(_micros(row.submitted_at), str(row.id))
    )


def feed_after(secret: str, cursor: str | None) -> tuple[datetime, int] | None:
    if not cursor:
        return None
    moment, sub_id = decode_parts(secret, cursor, version=CURSOR_FEED, count=2)
    return _from_micros(moment), int(sub_id)


def _rank_cursor(secret: str, row: competition_models.Submission) -> str:
    return encode_parts(
        secret,
        version=CURSOR_RANK,
        parts=(str(row.bytes), _micros(row.submitted_at), str(row.id)),
    )


def _rank_after(secret: str, cursor: str | None) -> tuple[int, datetime, int] | None:
    if not cursor:
        return None
    size, moment, sub_id = decode_parts(secret, cursor, version=CURSOR_RANK, count=3)
    return int(size), _from_micros(moment), int(sub_id)


def split_page(rows: list, limit: int) -> tuple[list, bool]:
    """One page, and whether another follows.

    The handlers read `limit + 1` rows and discard the extra. That is what makes
    `next_cursor` null exactly when the feed is exhausted, rather than handing back a cursor
    that turns out to address an empty page -- the same reasoning as `routers/results.py`,
    and the reason a client can loop until null instead of comparing counts.
    """
    return rows[:limit], len(rows) > limit


def resolve_competition(services: ServicesDep, slug: str) -> Competition:
    """Resolve a slug, or 404.

    404 rather than 400: an unknown slug is an address that does not exist here, and saying
    so is the same answer whether the caller typed it wrong or is probing for a competition
    this deployment does not serve.
    """
    try:
        return services.competitions.get(slug)
    except UnknownCompetition as exc:
        raise NotFound(f"no competition {slug!r} is served here") from exc


def refuse_if_paused(services: ServicesDep) -> None:
    """Refuse every write while `SUBMISSIONS_PAUSED` is set.

    `/v1/competitions/{slug}` publishes this as `submissions_open`, and a flag a client can
    read but an intake path ignores is worse than no flag at all, because the client would
    trust it. Every other write on this API refuses on it too -- `submissions.py`,
    `intents.py`, `web_submissions.py` and `_account.py` -- and the weekly pin-rotation
    drain depends on all of them doing so.

    Called first by both write handlers, before the rate limit and before anything parses a
    body: a paused surface takes no work at all, so refusing costs one boolean rather than a
    multipart parse and a curve operation. `web_submissions.py` places its check the same
    way, and for the same reason.
    """
    if services.settings.submissions_paused:
        raise ServiceUnavailable(
            "submissions are paused; see GET /v1/system/status",
            reason_code=REASON_SUBMISSIONS_PAUSED,
        )


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
    competition: Competition, session: CompetitionSessionDep, *, paused: bool
) -> schemas.CompetitionSummary:
    return schemas.CompetitionSummary(
        slug=competition.slug,
        name=competition.name,
        speed_floor=competition.speed_floor,
        incumbent_bytes=await queries.latest_incumbent_bytes(session),
        queued=await queries.queue_depth(session),
        submissions_open=not paused,
    )


def _ranking(position: int, row: competition_models.Submission) -> schemas.Ranking:
    return schemas.Ranking(
        rank=position,
        submission=row.id,
        hotkey=row.hotkey,
        # The ranking query selects only accepted rows with bytes recorded, so this is
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


# ── reading ────────────────────────────────────────────────────────────────


@router.get("", response_model=schemas.CompetitionIndex, summary="Competitions served here")
async def index(
    services: ServicesDep, session: CompetitionSessionDep
) -> schemas.CompetitionIndex:
    paused = services.settings.submissions_paused
    return schemas.CompetitionIndex(
        competitions=[
            await _summary(competition, session, paused=paused)
            for competition in services.competitions.competitions
        ]
    )


@router.get("/{slug}", response_model=schemas.CompetitionSummary, summary="One competition")
async def detail(
    services: ServicesDep, session: CompetitionSessionDep, slug: str = SlugPath
) -> schemas.CompetitionSummary:
    return await _summary(
        resolve_competition(services, slug), session, paused=services.settings.submissions_paused
    )


@router.get(
    "/{slug}/leaderboard", response_model=schemas.Leaderboard, summary="Standings"
)
async def leaderboard(
    services: ServicesDep,
    session: CompetitionSessionDep,
    slug: str = SlugPath,
    limit: LimitQuery = DEFAULT_PAGE_SIZE,
    cursor: CursorQuery = None,
) -> schemas.Leaderboard:
    """Every hotkey's best accepted submission, fewest bytes first.

    Paged, because one row per competing hotkey is a number that only grows and this is the
    competition's most-read endpoint. `rank` is absolute across pages rather than per page:
    the cursor carries the last row's `(bytes, submitted_at, id)`, which is the rank order
    itself, so the offset of a page is known without counting -- and a submission accepted
    between two page reads inserts itself at its own rank instead of shifting every page
    after it, which is what an OFFSET here would do.
    """
    competition = resolve_competition(services, slug)
    secret = services.settings.cursor_secret
    after = _rank_after(secret, cursor)
    rows = await queries.leaderboard(session, after=after, limit=limit + 1)
    page, more = split_page(rows, limit)
    incumbent = await queries.latest_incumbent_bytes(session)
    # The rank of the first row on this page. `after` is the previous page's last row, whose
    # rank the client has already been told, so counting from it needs no second query.
    offset = await queries.rank_before(session, after) if after else 0
    return schemas.Leaderboard(
        competition=competition.slug,
        incumbent_bytes=incumbent,
        speed_floor=competition.speed_floor,
        ranking=[
            _ranking(offset + position, row)
            for position, row in enumerate(page, start=1)
        ],
        next_cursor=_rank_cursor(secret, page[-1]) if more and page else None,
    )


@router.get(
    "/{slug}/stats",
    response_model=schemas.CompetitionStats,
    summary="Aggregate counts for a dashboard",
)
async def stats(
    services: ServicesDep, session: CompetitionSessionDep, slug: str = SlugPath
) -> schemas.CompetitionStats:
    """How much has been attempted, by how many, and how far it has got.

    Its own endpoint rather than fields on `/{slug}`, because the index renders every
    competition and these are four aggregates each. One competition makes that free today;
    the shape is what a second one inherits.
    """
    competition = resolve_competition(services, slug)
    counted = await queries.counts_by_state(session)
    # Every state the schema defines, including the ones nothing has reached. Filled from
    # the enum rather than from the query so a client renders a fixed set of counters
    # instead of discovering which keys happen to exist today.
    by_state = {state: counted.get(state, 0) for state in STATE_VALUES}
    last = await queries.last_accepted_at(session)
    return schemas.CompetitionStats(
        competition=competition.slug,
        submissions=by_state,
        total_submissions=sum(by_state.values()),
        competitors=await queries.competitor_count(session),
        best_bytes=await queries.best_bytes(session),
        last_accepted_at=iso(last) if last else None,
    )


# Declared before `/{slug}/submissions/{submission_id}`: Starlette matches in declaration
# order, and the two do not collide on segment count -- but keeping the literal feed above
# the typed detail path is the rule the rest of this API follows, and following it here means
# adding a literal sub-path later cannot silently be swallowed by the id parameter.
@router.get(
    "/{slug}/submissions",
    response_model=schemas.SubmissionPage,
    summary="Every submission, newest first",
)
async def submissions(
    services: ServicesDep,
    session: CompetitionSessionDep,
    slug: str = SlugPath,
    limit: LimitQuery = DEFAULT_PAGE_SIZE,
    cursor: CursorQuery = None,
    state: Annotated[str | None, Query(max_length=16)] = None,
    hotkey: Annotated[str | None, Query(max_length=64)] = None,
) -> schemas.SubmissionPage:
    """The whole history of attempts, filterable by state or by hotkey.

    Unfiltered by default, like `/v1/results/submissions` on the proofs side: a feed that
    dropped the rejections would show a reader only the successes and read as the complete
    record. What is published is that an attempt was made and how it went -- the same three
    facts the submission endpoint has always published, one row at a time.

    `hotkey` is the filter that does real work. A submission id is the only handle a miner
    receives, and until this existed a miner who lost one had no way to find their own
    submission again; the signed submit path sets no `account_id`, so `/v1/me/competitions`
    could not help them either.
    """
    competition = resolve_competition(services, slug)
    if state is not None and state not in STATE_VALUES:
        raise BadRequest(
            f"state must be one of {', '.join(STATE_VALUES)}",
            reason_code="INVALID_ARGUMENT",
        )
    secret = services.settings.cursor_secret
    rows = await queries.submissions_page(
        session,
        after=feed_after(secret, cursor),
        limit=limit + 1,
        state=state,
        hotkey=hotkey,
    )
    page, more = split_page(rows, limit)
    return schemas.SubmissionPage(
        items=tuple(_view(competition, row) for row in page),
        next_cursor=feed_cursor(secret, page[-1]) if more and page else None,
    )


@router.get(
    "/{slug}/competitors/{hotkey}",
    response_model=schemas.CompetitorView,
    summary="One hotkey's standing, and what it may still submit",
)
async def competitor(
    services: ServicesDep,
    session: CompetitionSessionDep,
    slug: str = SlugPath,
    hotkey: str = HotkeyPath,
) -> schemas.CompetitorView:
    """Where a hotkey stands and how many submissions it has left.

    `slots_remaining` is why this exists. The entitlement rule is that one registration buys
    one accepted submission, and before this the only way to find out how many remained was
    to submit and read the refusal -- which, on a surface whose scarcity model *is* that
    rule, is the one fact a miner most needs in advance.

    Public, like the leaderboard: every number here is derived from accepted submissions
    that are already published, plus registrations that are already on chain.
    """
    competition = resolve_competition(services, slug)
    registered = await queries.is_registered(session, hotkey)
    _allowed, slots, pending = await queries.may_queue(session, hotkey)
    counts = await queries.counts_by_state_for_hotkey(session, hotkey)
    best = await queries.best_for_hotkey(session, hotkey)
    return schemas.CompetitorView(
        competition=competition.slug,
        hotkey=hotkey,
        registered=registered,
        slots_remaining=max(slots - pending, 0),
        pending=pending,
        submissions=sum(counts.values()),
        accepted=counts.get(SubmissionState.ACCEPTED.value, 0),
        best=_ranking(await queries.rank_of(session, best) if best else 0, best)
        if best
        else None,
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
    competition = resolve_competition(services, slug)
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
    competition = resolve_competition(services, slug)
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
    "/{slug}/submissions/{submission_id}/source",
    response_model=schemas.SubmissionSource,
    summary="An accepted submission's two files",
)
async def source(
    services: ServicesDep,
    session: CompetitionSessionDep,
    slug: str = SlugPath,
    submission_id: int = Path(ge=1),
) -> schemas.SubmissionSource:
    """The `parse.rs` and `Parse.lean` behind an accepted submission.

    Accepted only, mirroring `/v1/results/{id}/solution` on the proofs side and for the same
    reason. What an accept means is that the gate proved this parser correct against the
    Lean specification and measured it, and a competition whose winning entries cannot be
    read is one nobody can build on -- the point of the exercise is a faster verified DEFLATE
    parser existing, not a leaderboard existing. A queued or rejected submission is the
    miner's unproven work and stays theirs: there is no verdict yet that the rest of the
    subnet has any claim on.

    `404` rather than `403` for a submission that is not accepted, matching how the proofs
    results feed answers for an unpublished result: the state is already public on the
    submission endpoint, so nothing is being concealed, and one answer for "not here" keeps
    this from being a second way to ask a question `/submissions/{id}` already answers.
    """
    competition = resolve_competition(services, slug)
    row = await queries.get_submission(session, submission_id)
    if row is None or row.state != SubmissionState.ACCEPTED.value:
        raise NotFound("no accepted submission with that id")
    pair = await queries.submission_sources(session, submission_id)
    if pair is None:
        # Rows written before the files moved into the database carry neither. Honest
        # rather than empty: a client that got two empty strings would render them as a
        # submission with no source, which is a different and wrong claim.
        raise NotFound("this submission predates stored sources")
    try:
        parse_rs, proof_lean = pair[0].decode("utf-8"), pair[1].decode("utf-8")
    except UnicodeDecodeError as exc:
        # Not reachable for a row the gate accepted -- it compiled both as source. Refused
        # rather than replaced, because mangling bytes into replacement characters would
        # publish something that is not what was submitted and would not compile.
        raise Conflict(
            "this submission's files are not valid UTF-8",
            reason_code="SOURCE_NOT_TEXT",
        ) from exc
    return schemas.SubmissionSource(
        competition=competition.slug,
        id=row.id,
        hotkey=row.hotkey,
        digest=row.digest,
        parse_rs=parse_rs,
        proof_lean=proof_lean,
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
    competition = resolve_competition(services, slug)
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
    # Both handlers have already refused a pause before reading a body. This is the
    # structural backstop: a third write path added later reaches the queue through here,
    # and a guard only in the callers is one a new caller can forget. Checking a boolean
    # twice costs nothing; discovering the omission in production would not.
    refuse_if_paused(services)

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
    refuse_if_paused(services)
    competition = resolve_competition(services, slug)
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
    refuse_if_paused(services)
    competition = resolve_competition(services, slug)
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
    response_model=schemas.SubmissionPage,
    summary="Submissions made through this account",
)
async def my_submissions(
    services: ServicesDep,
    session: CompetitionSessionDep,
    principal: PrincipalDep,
    limit: LimitQuery = DEFAULT_PAGE_SIZE,
    cursor: CursorQuery = None,
) -> schemas.SubmissionPage:
    """This account's own submissions, newest first.

    Only the ones made through a signed-in session: a hotkey-signed submit sets no
    `account_id`, so it never appears here even when the same person made it. That is the
    honest reading of the column -- it records which account authorised the write, and
    nothing about a signature says an account was involved at all. A miner looking for a
    hotkey's submissions wants `/v1/competitions/{slug}/submissions?hotkey=...`, which is
    keyed on the thing that actually signed them.

    Paged like every other feed, and with the same cursor shape as the public one: the rows
    are the same rows in the same order, and issuing a different cursor for them would be a
    second thing to keep in step for no gain.
    """
    competition = services.competitions.only()
    secret = services.settings.cursor_secret
    rows = await queries.submissions_for_account(
        session,
        principal.account.id,
        after=feed_after(secret, cursor),
        limit=limit + 1,
    )
    page, more = split_page(rows, limit)
    return schemas.SubmissionPage(
        items=tuple(_view(competition, row) for row in page),
        next_cursor=feed_cursor(secret, page[-1]) if more and page else None,
    )


# The operator router in `competitions_admin` shares the slug resolution and the cursor
# codec. Named here rather than duplicated there: two modules that page the same table must
# issue the same cursors, or an operator's page two would be a different page two.
__all__ = [
    "CursorQuery",
    "LimitQuery",
    "feed_after",
    "feed_cursor",
    "me_router",
    "refuse_if_paused",
    "resolve_competition",
    "router",
    "split_page",
]
