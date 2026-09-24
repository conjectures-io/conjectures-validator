"""The competition surface: every competition, one router.

The slug in the path selects a competition from the registry, and with it the competition's
adapter and the engine on its own database. Nothing below knows what any competition measures
or how it stores anything: that is the adapter's (`submission_api/competitions/base.py`). So
onboarding a competition is an adapter and a database URL, and removing one is deleting its
catalog line -- this file does not change either way.

Two ways to submit, and the difference is the credential, not the capability:

* `POST .../submissions` is signed by a subnet hotkey and carries its scalars in
  `X-Conjectures-*` headers, which `CORS_REQUEST_HEADERS` does not allowlist -- so no browser
  can send it. A browser has no business holding a hotkey.
* `POST .../submissions/session` is authorised by a session cookie and refuses a bearer token,
  like the privileged `/v1/me` writes. It is the one handler that uses both databases: the
  account's submission coldkey comes from the proofs database, and the competition's
  registrations say whether that coldkey registered the hotkey. Two reads, no join, no shared
  transaction -- sound because both halves are immutable history.

Everything else is public. A leaderboard nobody can read is not one, and a competition whose
record is visible only to whoever holds a database credential is one nobody outside can check.

Checks run cheapest first: the pause, then the rate limit, then freshness, then the body, then
the signature, then the competition's own entitlement rule.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Path, Query, Request
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from conjectures_subnet.axiom import get_axiom
from submission_api import schemas_competitions as schemas
from submission_api.competitions import Competition, UnknownCompetition, Unsupported
from submission_api.competitions import base
from submission_api.competitions.pagination import (
    CursorQuery,
    LimitQuery,
    decode,
    encode,
    split_page,
)
from submission_api.competitions.signature import submit_message
from submission_api.dependencies import CookieWriterDep, PrincipalDep, ServicesDep
from submission_api.errors import (
    REASON_SUBMISSIONS_PAUSED,
    ApiError,
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
from submission_api.pagination import REASON_INVALID_CURSOR
from submission_api.settings import DEFAULT_PAGE_SIZE

router = APIRouter(prefix="/v1/competitions", tags=["competitions"])

SlugPath = Path(description="The competition's slug", max_length=64)
SubmissionIdPath = Path(description="A submission id, as the competition issued it", max_length=64)
# An ss58 address is 47-48 characters. Bounded rather than matched: it is only ever an equality
# predicate, and a pattern here would be a second, weaker copy of `verify_signature`'s check.
HotkeyPath = Path(description="A subnet hotkey (ss58)", min_length=2, max_length=64)


# ── resolving a competition and its database ──────────────────────────────────────────────


def get_competition(services: ServicesDep, slug: str = SlugPath) -> Competition:
    """The competition a slug names, or 404 -- the same answer for a typo and for a probe."""
    try:
        return services.competitions.get(slug)
    except UnknownCompetition as exc:
        raise NotFound(f"no competition {slug!r} is served here") from exc


CompetitionDep = Annotated[Competition, Depends(get_competition)]


def _unavailable(competition: Competition) -> ServiceUnavailable:
    return ServiceUnavailable(
        f"competition {competition.slug!r} is unavailable right now",
        reason_code="COMPETITION_UNAVAILABLE",
    )


async def get_competition_session(competition: CompetitionDep) -> AsyncIterator[AsyncSession]:
    """One session on *this competition's* database per request, always closed.

    Named separately from `SessionDep`, which is the proofs database's, so a handler's
    signature says which database it touches. A competition's database being unreachable is an
    outage of that competition's routes -- a 503 -- and nothing else's.
    """
    async with competition.sessions() as session:
        try:
            yield session
        except (OperationalError, InterfaceError) as exc:
            get_axiom().error(
                source="api-competitions",
                event_type="competition_database_unreachable",
                competition=competition.slug,
                database_error=f"{type(exc).__name__}: {exc}",
            )
            raise _unavailable(competition) from exc


CompetitionSessionDep = Annotated[AsyncSession, Depends(get_competition_session)]


def refuse_if_paused(services: ServicesDep) -> None:
    """Refuse every write while `SUBMISSIONS_PAUSED` is set.

    Published as `submissions_open`, and a flag a client can read but an intake path ignores is
    worse than none. Called first by both write handlers, before anything parses a body.
    """
    if services.settings.submissions_paused:
        raise ServiceUnavailable(
            "submissions are paused; see GET /v1/system/status",
            reason_code=REASON_SUBMISSIONS_PAUSED,
        )


def _invalid_cursor() -> BadRequest:
    return BadRequest("cursor is not one this API issued", reason_code=REASON_INVALID_CURSOR)


def _submission_id(competition: Competition, raw: str) -> str:
    parsed = competition.adapter.parse_id(raw)
    if parsed is None:
        raise NotFound("no such submission")
    return parsed


# ── shaping ───────────────────────────────────────────────────────────────────────────────


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _metrics(values: Mapping[str, base.MetricValue]) -> dict[str, base.MetricValue]:
    return dict(values)


def _view(competition: Competition, row: base.Submission) -> schemas.SubmissionView:
    return schemas.SubmissionView(
        competition=competition.slug,
        id=row.id,
        hotkey=row.hotkey,
        digest=row.digest,
        state=row.state.value,
        submitted_at=_iso(row.submitted_at),
        finished_at=_iso(row.finished_at) if row.finished_at else None,
        metrics=_metrics(row.metrics),
    )


def _ranking(rank: int, row: base.Standing) -> schemas.Ranking:
    return schemas.Ranking(
        rank=rank,
        submission=row.submission_id,
        hotkey=row.hotkey,
        submitted_at=_iso(row.submitted_at),
        metrics=_metrics(row.metrics),
    )


def _describe(
    competition: Competition,
    *,
    headline: Mapping[str, base.MetricValue],
    queued: int,
    paused: bool,
) -> schemas.CompetitionSummary:
    info = competition.info
    return schemas.CompetitionSummary(
        slug=info.slug,
        name=info.name,
        description=info.description,
        files=[
            schemas.FileSpec(name=f.name, max_bytes=f.max_bytes, description=f.description)
            for f in info.files
        ],
        metrics=[
            schemas.Metric(key=m.key, label=m.label, unit=m.unit, better=m.better)
            for m in info.metrics
        ],
        ranked_by=info.ranked_by,
        headline=_metrics(headline),
        queued=queued,
        submissions_open=not paused,
    )


# ── reading ───────────────────────────────────────────────────────────────────────────────


@router.get("", response_model=schemas.CompetitionIndex, summary="Competitions served here")
async def index(services: ServicesDep) -> schemas.CompetitionIndex:
    """Every competition this deployment serves, each read from its own database.

    Read one by one with a session each, since each lives in its own database. One whose
    database is down is left out rather than failing the whole index, and says so in the
    readiness probe; its own routes answer 503.
    """
    paused = services.settings.submissions_paused
    described = []
    for competition in services.competitions:
        try:
            async with competition.sessions() as session:
                headline = await competition.adapter.headline(session)
                queued = await competition.adapter.queue_depth(session)
        except (OperationalError, InterfaceError) as exc:
            get_axiom().error(
                source="api-competitions",
                event_type="competition_database_unreachable",
                competition=competition.slug,
                database_error=f"{type(exc).__name__}: {exc}",
            )
            continue
        described.append(_describe(competition, headline=headline, queued=queued, paused=paused))
    return schemas.CompetitionIndex(competitions=described)


@router.get("/{slug}", response_model=schemas.CompetitionSummary, summary="One competition")
async def detail(
    services: ServicesDep, competition: CompetitionDep, session: CompetitionSessionDep
) -> schemas.CompetitionSummary:
    return _describe(
        competition,
        headline=await competition.adapter.headline(session),
        queued=await competition.adapter.queue_depth(session),
        paused=services.settings.submissions_paused,
    )


@router.get("/{slug}/leaderboard", response_model=schemas.Leaderboard, summary="Standings")
async def leaderboard(
    services: ServicesDep,
    competition: CompetitionDep,
    session: CompetitionSessionDep,
    limit: LimitQuery = DEFAULT_PAGE_SIZE,
    cursor: CursorQuery = None,
) -> schemas.Leaderboard:
    """Every hotkey's best accepted submission, in the competition's own order.

    Keyset-paged, and `rank` is absolute across pages: the cursor carries the last row's
    position in the ranking itself, so a page's offset is one count rather than an OFFSET scan,
    and a submission accepted between two reads takes its own rank instead of shifting every
    later page.
    """
    secret = services.settings.cursor_secret
    after = decode(secret, "rank", competition.slug, cursor)
    try:
        rows = list(await competition.adapter.leaderboard(session, after=after, limit=limit + 1))
        offset = await competition.adapter.rank_before(session, after) if after else 0
    except (ValueError, TypeError) as exc:
        # A cursor this deployment signed, but for a position this adapter no longer parses.
        raise _invalid_cursor() from exc
    page, more = split_page(rows, limit)
    return schemas.Leaderboard(
        competition=competition.slug,
        ranked_by=competition.info.ranked_by,
        headline=_metrics(await competition.adapter.headline(session)),
        ranking=[_ranking(offset + n, row) for n, row in enumerate(page, start=1)],
        next_cursor=encode(secret, "rank", competition.slug, page[-1].position)
        if more and page
        else None,
    )


@router.get(
    "/{slug}/stats", response_model=schemas.CompetitionStats, summary="Aggregate counts"
)
async def stats(
    competition: CompetitionDep, session: CompetitionSessionDep
) -> schemas.CompetitionStats:
    """How much has been attempted, by how many, and how far it has got."""
    counted = await competition.adapter.stats(session)
    by_state = {state: counted.by_state.get(state, 0) for state in base.STATE_VALUES}
    return schemas.CompetitionStats(
        competition=competition.slug,
        submissions=by_state,
        total_submissions=sum(by_state.values()),
        competitors=counted.competitors,
        best=_metrics(counted.best),
        last_accepted_at=_iso(counted.last_accepted_at) if counted.last_accepted_at else None,
    )


@router.get("/{slug}/scores", response_model=schemas.Scores, summary="The latest scoring pass")
async def scores(competition: CompetitionDep, session: CompetitionSessionDep) -> schemas.Scores:
    """What the competition last decided to pay, per hotkey, and why -- as it recorded it.

    Public: it is derived from accepted submissions that are already public, and a subnet
    whose payout reasoning is secret is one nobody can check.
    """
    try:
        latest = await competition.adapter.scores(session)
    except Unsupported as exc:
        raise NotFound(str(exc), reason_code="NOT_SUPPORTED") from exc
    if latest is None:
        raise NotFound("this competition has not scored anything yet")
    return schemas.Scores(
        competition=competition.slug,
        computed_at=_iso(latest.computed_at),
        block=latest.block,
        dry_run=latest.dry_run,
        accepted=latest.accepted,
        summary=latest.summary,
        entries=[
            schemas.ScoreEntry(
                hotkey=entry.hotkey,
                submission=entry.submission_id,
                weight=entry.weight,
                metrics=_metrics(entry.metrics),
                note=entry.note,
            )
            for entry in latest.entries
        ],
    )


# Literal sub-paths before the typed `{submission_id}` one, the rule the rest of this API keeps.
@router.get(
    "/{slug}/submissions",
    response_model=schemas.SubmissionPage,
    summary="Every submission, newest first",
)
async def submissions(
    services: ServicesDep,
    competition: CompetitionDep,
    session: CompetitionSessionDep,
    limit: LimitQuery = DEFAULT_PAGE_SIZE,
    cursor: CursorQuery = None,
    state: Annotated[str | None, Query(max_length=16)] = None,
    hotkey: Annotated[str | None, Query(max_length=64)] = None,
) -> schemas.SubmissionPage:
    """The whole history of attempts, filterable by state or by hotkey.

    Unfiltered by default: a feed that dropped the rejections would read as the complete record
    while showing only the successes. `hotkey` is how a miner who lost a submission id finds
    their submission again.
    """
    if state is not None and state not in base.STATE_VALUES:
        raise BadRequest(
            f"state must be one of {', '.join(base.STATE_VALUES)}",
            reason_code="INVALID_ARGUMENT",
        )
    return await _page(
        services,
        competition,
        session,
        cursor=cursor,
        limit=limit,
        state=base.SubmissionState(state) if state else None,
        hotkeys=[hotkey] if hotkey else None,
    )


async def _page(
    services: ServicesDep,
    competition: Competition,
    session: AsyncSession,
    *,
    cursor: str | None,
    limit: int,
    state: base.SubmissionState | None = None,
    hotkeys: list[str] | None = None,
) -> schemas.SubmissionPage:
    secret = services.settings.cursor_secret
    after = decode(secret, "feed", competition.slug, cursor)
    try:
        rows = list(
            await competition.adapter.submissions(
                session, after=after, limit=limit + 1, state=state, hotkeys=hotkeys
            )
        )
    except (ValueError, TypeError) as exc:
        raise _invalid_cursor() from exc
    page, more = split_page(rows, limit)
    return schemas.SubmissionPage(
        items=tuple(_view(competition, row) for row in page),
        next_cursor=encode(secret, "feed", competition.slug, page[-1].position)
        if more and page
        else None,
    )


@router.get(
    "/{slug}/competitors/{hotkey}",
    response_model=schemas.CompetitorView,
    summary="One hotkey's standing, and what it may still submit",
)
async def competitor(
    competition: CompetitionDep,
    session: CompetitionSessionDep,
    hotkey: str = HotkeyPath,
) -> schemas.CompetitorView:
    """Public, like the board: every number is derived from published submissions and on-chain
    registrations."""
    eligibility = await competition.adapter.eligibility(session, hotkey)
    best = await competition.adapter.best_for(session, hotkey)
    rank = (
        await competition.adapter.rank_before(session, best.position) if best is not None else 0
    )
    return schemas.CompetitorView(
        competition=competition.slug,
        hotkey=hotkey,
        registered=eligibility.registered,
        slots_remaining=eligibility.slots_remaining,
        pending=eligibility.pending,
        best=_ranking(rank, best) if best is not None else None,
    )


@router.get(
    "/{slug}/submissions/{submission_id}",
    response_model=schemas.SubmissionView,
    summary="One submission",
)
async def submission(
    competition: CompetitionDep,
    session: CompetitionSessionDep,
    submission_id: str = SubmissionIdPath,
) -> schemas.SubmissionView:
    row = await competition.adapter.submission(
        session, _submission_id(competition, submission_id)
    )
    if row is None:
        raise NotFound("no such submission")
    return _view(competition, row)


@router.get(
    "/{slug}/submissions/{submission_id}/report",
    response_model=schemas.SubmissionReport,
    summary="The gate's report",
)
async def report(
    competition: CompetitionDep,
    session: CompetitionSessionDep,
    submission_id: str = SubmissionIdPath,
) -> schemas.SubmissionReport:
    """Public, like the submission: a competition whose refusals cannot be read is one nobody
    can improve against."""
    found = await competition.adapter.report(session, _submission_id(competition, submission_id))
    if found is None:
        raise NotFound("no such submission")
    return schemas.SubmissionReport(
        competition=competition.slug,
        id=found.id,
        state=found.state.value,
        exit_code=found.exit_code,
        report=found.text,
    )


@router.get(
    "/{slug}/submissions/{submission_id}/source",
    response_model=schemas.SubmissionSource,
    summary="An accepted submission's files",
)
async def source(
    competition: CompetitionDep,
    session: CompetitionSessionDep,
    submission_id: str = SubmissionIdPath,
) -> schemas.SubmissionSource:
    """Accepted only, and 404 otherwise -- the state is public already, so one answer for "not
    here" keeps this from being a second way to ask what `/submissions/{id}` answers."""
    parsed = _submission_id(competition, submission_id)
    row = await competition.adapter.submission(session, parsed)
    if row is None or row.state is not base.SubmissionState.ACCEPTED:
        raise NotFound("no accepted submission with that id")
    try:
        files = await competition.adapter.files(session, parsed)
    except Unsupported as exc:
        raise NotFound(str(exc), reason_code="NOT_SUPPORTED") from exc
    except LookupError as exc:
        raise NotFound("this submission's files are not held by the API's database") from exc
    try:
        decoded = {name: content.decode("utf-8") for name, content in files.items()}
    except UnicodeDecodeError as exc:
        # Refused rather than mangled: replacement characters would publish something that is
        # not what was submitted.
        raise Conflict(
            "this submission's files are not valid UTF-8", reason_code="SOURCE_NOT_TEXT"
        ) from exc
    return schemas.SubmissionSource(
        competition=competition.slug,
        id=row.id,
        hotkey=row.hotkey,
        digest=row.digest,
        files=decoded,
    )


# ── writing ───────────────────────────────────────────────────────────────────────────────


async def _read_files(request: Request, competition: Competition) -> dict[str, bytes]:
    """The declared files, each under its declared cap.

    Parsed here rather than declared as `UploadFile` parameters, and that is an ordering
    property: FastAPI resolves `File()` parameters before the handler body, which would put a
    form parser over untrusted bytes ahead of the pause, the rate limit and the freshness check.

    Each part is read one byte past its cap, so an oversized upload is refused by what arrived
    rather than by a `Content-Length` the client controls.
    """
    specs = competition.info.files
    try:
        form = await request.form(max_files=len(specs), max_fields=0)
    except Exception as exc:  # noqa: BLE001 - a body that will not parse is a bad request
        raise BadRequest("the request body is not a valid multipart form") from exc
    names = ", ".join(spec.name for spec in specs)
    try:
        if set(form.keys()) != {spec.name for spec in specs}:
            raise BadRequest(f"the body must carry exactly these file parts: {names}")
        files: dict[str, bytes] = {}
        for spec in specs:
            part = form[spec.name]
            if isinstance(part, str):
                raise BadRequest(f"{spec.name} must be a file part")
            content = await part.read(spec.max_bytes + 1)
            if len(content) > spec.max_bytes:
                raise PayloadTooLarge(f"{spec.name} must be at most {spec.max_bytes} bytes")
            if not content:
                raise BadRequest(f"{spec.name} must not be empty")
            files[spec.name] = content
    finally:
        await form.close()
    return files


async def _queue(
    *,
    services: ServicesDep,
    competition: Competition,
    session: AsyncSession,
    hotkey: str,
    files: dict[str, bytes],
    digest: str,
    account_id: str | None = None,
) -> schemas.SubmissionAccepted:
    # The structural backstop: both handlers refused a pause before reading a body, and a third
    # write path added later reaches the queue through here.
    refuse_if_paused(services)
    try:
        queued = await competition.adapter.queue(
            session, hotkey=hotkey, digest=digest, files=files
        )
    except base.NotRegistered as exc:
        raise PaymentRequired(
            "this hotkey is not registered on the subnet", reason_code="NOT_REGISTERED"
        ) from exc
    except base.NoEntitlement as exc:
        raise PaymentRequired(exc.message, reason_code="NO_ENTITLEMENT") from exc
    if queued.created:
        # Ids, never files. The account is recorded here, and only here, for a session submit:
        # competitions know nothing about accounts, and the proofs database holds no record of
        # a competition's submissions.
        get_axiom().info(
            source="api-competitions",
            event_type="competition_submission_queued",
            competition=competition.slug,
            submission_id=queued.submission.id,
            hotkey=hotkey,
            account_id=account_id or "",
            path="session" if account_id else "signed",
        )
    return schemas.SubmissionAccepted(
        competition=competition.slug,
        submission=queued.submission.id,
        state=queued.submission.state.value,
        digest=digest,
        created=queued.created,
        slots_remaining=queued.eligibility.slots_remaining,
    )


@contextmanager
def _recording_refusals(
    competition: Competition, *, hotkey: str, account_id: str | None = None
) -> Iterator[None]:
    """Emit `competition_submission_refused` for a refusal raised inside a submit handler.

    `request_completed` already has the status and reason code, but not which competition or
    which hotkey, and "why can this miner not submit" is asked with a hotkey in hand. Records and
    re-raises, never swallows: the response is still shaped by the exception handlers.

    Covers what the handler body raises: the pause, the rate limit, freshness, the files, the
    signature, the coldkey and entitlement checks, and a competition database that fails
    mid-request (answered 503 `COMPETITION_UNAVAILABLE` by `get_competition_session`, which
    emits its own `competition_database_unreachable` too). A refusal FastAPI raises while it
    resolves dependencies, before the body runs, is `request_completed`'s alone: a missing
    header, an unknown slug, a session path without a session cookie.

    On the signed path `hotkey` is the header's claim, and for a refusal raised before the
    signature check nothing has proved the caller holds it. It is recorded anyway, since the
    miner asking is the one who sent it.
    """
    try:
        yield
    except ApiError as exc:
        _refused(competition, exc.reason_code, exc.status_code, hotkey, account_id)
        raise
    except (OperationalError, InterfaceError):
        _refused(competition, "COMPETITION_UNAVAILABLE", 503, hotkey, account_id)
        raise


def _refused(
    competition: Competition,
    reason_code: str,
    status_code: int,
    hotkey: str,
    account_id: str | None,
) -> None:
    # `warning` whatever the status, like `submission_rejected`: a refused submit is usually the
    # miner's to fix, and the one that is the operator's, an unreachable database, already has
    # an `error` of its own.
    get_axiom().warn(
        source="api-competitions",
        event_type="competition_submission_refused",
        competition=competition.slug,
        reason_code=reason_code,
        http_status=status_code,
        hotkey=hotkey,
        account_id=account_id or "",
        path="session" if account_id else "signed",
    )


def _signature_bytes(value: str) -> bytes:
    candidate = value.removeprefix("0x")
    try:
        return bytes.fromhex(candidate) if candidate else b""
    except ValueError:
        # An undecodable signature is the same refusal as a wrong one; see verify_signature.
        return b""


@router.post(
    "/{slug}/submissions",
    response_model=schemas.SubmissionAccepted,
    status_code=201,
    summary="Submit, signed by a subnet hotkey",
)
async def submit(
    request: Request,
    services: ServicesDep,
    competition: CompetitionDep,
    session: CompetitionSessionDep,
    hotkey: Annotated[str, Header(alias="X-Conjectures-Hotkey", max_length=64)],
    timestamp: Annotated[int, Header(alias="X-Conjectures-Timestamp")],
    signature: Annotated[str, Header(alias="X-Conjectures-Signature", max_length=256)],
) -> schemas.SubmissionAccepted:
    """Queue the competition's declared files, signed by a registered hotkey.

    The signed message is `competitions.signature.submit_message` over the adapter's digest of
    the files. Submitting the same files again returns the same submission.
    """
    with _recording_refusals(competition, hotkey=hotkey):
        refuse_if_paused(services)
        settings = services.settings
        decision = services.competition_submits.check(
            f"{competition.slug}:{hotkey}", time.monotonic()
        )
        if not decision.allowed:
            raise TooManyRequests(
                f"more than {settings.competition_rate_per_minute} submissions a minute "
                "from this hotkey",
                reason_code="RATE_LIMITED",
            )
        # Freshness before the body and the signature: a stale request costs neither.
        if abs(int(time.time()) - timestamp) > settings.competition_signature_window_seconds:
            raise Unauthorized(
                "the signed timestamp is outside the freshness window",
                reason_code="SIGNATURE_EXPIRED",
            )
        files = await _read_files(request, competition)
        digest = competition.adapter.digest(files)
        # Rebuilt from the bytes read and the slug resolved, never from what the request claimed.
        verify_signature(
            address=hotkey,
            message=submit_message(
                competition=competition.slug, digest=digest, hotkey=hotkey, timestamp=timestamp
            ),
            signature=_signature_bytes(signature),
        )
        return await _queue(
            services=services,
            competition=competition,
            session=session,
            hotkey=hotkey,
            files=files,
            digest=digest,
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
    competition: CompetitionDep,
    session: CompetitionSessionDep,
    principal: CookieWriterDep,
    hotkey: Annotated[str, Header(alias="X-Conjectures-Hotkey", max_length=64)],
) -> schemas.SubmissionAccepted:
    """Submit for a hotkey the account's own submission coldkey registered, with no signature.

    `CookieWriterDep` refuses a bearer token: this is the browser's path. The account is not
    asked to sign with the hotkey; entitlement runs the other way round, through the coldkey the
    account proved it controls and the registration that coldkey made.
    """
    account_id = str(principal.account.id)
    with _recording_refusals(competition, hotkey=hotkey, account_id=account_id):
        refuse_if_paused(services)
        coldkey = principal.account.submission_coldkey
        if not coldkey:
            raise PaymentRequired(
                "this account has no submission coldkey: link one before submitting",
                reason_code="NO_SUBMISSION_COLDKEY",
            )
        if not await competition.adapter.registered_by(session, hotkey=hotkey, coldkey=coldkey):
            # Not "that hotkey is someone else's": the account learns only that its own coldkey
            # did not register it, which is the fact it can act on.
            raise Forbidden(
                "this account's submission coldkey did not register that hotkey",
                reason_code="HOTKEY_NOT_YOURS",
            )
        files = await _read_files(request, competition)
        return await _queue(
            services=services,
            competition=competition,
            session=session,
            hotkey=hotkey,
            files=files,
            digest=competition.adapter.digest(files),
            account_id=account_id,
        )


# ── the account's own ─────────────────────────────────────────────────────────────────────


@router.get(
    "/{slug}/me/submissions",
    response_model=schemas.SubmissionPage,
    summary="Submissions from hotkeys this account's coldkey registered",
)
async def my_submissions(
    services: ServicesDep,
    competition: CompetitionDep,
    session: CompetitionSessionDep,
    principal: PrincipalDep,
    limit: LimitQuery = DEFAULT_PAGE_SIZE,
    cursor: CursorQuery = None,
) -> schemas.SubmissionPage:
    """Every submission from a hotkey the account's submission coldkey registered.

    Derived, not recorded: the competition knows which coldkey registered which hotkey, and the
    account knows its coldkey, so the answer needs no link table in either database -- and it
    includes hotkey-signed submissions, which carry no account at all. Under the competition's
    prefix rather than `/v1/me`, because the slug is what says which competition is meant.
    """
    coldkey = principal.account.submission_coldkey
    hotkeys = list(await competition.adapter.hotkeys_of(session, coldkey)) if coldkey else []
    return await _page(
        services, competition, session, cursor=cursor, limit=limit, hotkeys=hotkeys
    )


__all__ = [
    "CompetitionDep",
    "CompetitionSessionDep",
    "get_competition",
    "refuse_if_paused",
    "router",
]
