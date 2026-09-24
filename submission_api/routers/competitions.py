"""Authenticated competition intake. Read endpoints live in competition_reads."""

from __future__ import annotations

import time
from fastapi import APIRouter, Header, Path, Request, Response
from submission_api import compression_store as queries
from submission_api import competition_sig, schemas_compression as schemas
from submission_api.competitions import Competition, UnknownCompetition
from submission_api.dependencies import CompetitionSessionDep, CookieWriterDep, ServicesDep
from submission_api.errors import (
    REASON_SUBMISSIONS_PAUSED,
    BadRequest,
    Forbidden,
    NotFound,
    PayloadTooLarge,
    PaymentRequired,
    ServiceUnavailable,
    TooManyRequests,
    Unauthorized,
)
from submission_api.login import verify_signature
from submission_api.settings import MAX_COMPETITION_FILE_BYTES

router = APIRouter(prefix="/v1/competitions", tags=["competitions"])

SlugPath = Path(description="The competition's slug", max_length=64)
# An ss58 address is 47-48 base58 characters. Bounded rather than matched, because the address
# is only ever used as an equality predicate and a pattern here would be a second, weaker copy
# of the check `verify_signature` already makes on the addresses that matter.
HotkeyPath = Path(description="A subnet hotkey (ss58)", min_length=2, max_length=64)


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


UPLOAD_SCHEMA = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["parse.rs", "Parse.lean"],
                    "additionalProperties": False,
                    "properties": {
                        name: {"type": "string", "format": "binary"}
                        for name in ("parse.rs", "Parse.lean")
                    },
                }
            }
        },
    }
}


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
        if len(form.multi_items()) != 2 or set(form) != {"parse.rs", "Parse.lean"}:
            raise BadRequest("Expected exactly parse.rs and Parse.lean")
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
        raise PayloadTooLarge(f"each file must be at most {MAX_COMPETITION_FILE_BYTES} bytes")
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
) -> schemas.Accepted:
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

    await queries.compatible(session)
    await queries.lock_hotkey(session, hotkey)
    existing = await queries.find_submission(session, hotkey=hotkey, digest=digest)
    if existing is not None:
        _, slots, pending = await queries.may_queue(session, hotkey)
        return schemas.Accepted(
            competition=competition.slug,
            submission=str(existing.id),
            created=False,
            status_url=f"/v1/competitions/{competition.slug}/submissions/{existing.id}",
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
    return schemas.Accepted(
        competition=competition.slug,
        submission=str(submission_id),
        created=_fresh,
        status_url=f"/v1/competitions/{competition.slug}/submissions/{submission_id}",
        state=row["state"],
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
    response_model=schemas.Accepted,
    status_code=201,
    summary="Submit, signed by a subnet hotkey",
    openapi_extra=UPLOAD_SCHEMA,
)
async def submit(
    request: Request,
    response: Response,
    services: ServicesDep,
    session: CompetitionSessionDep,
    slug: str = SlugPath,
    hotkey: str = Header(..., alias="X-Conjectures-Hotkey"),
    timestamp: int = Header(..., alias="X-Conjectures-Timestamp"),
    signature: str = Header(..., alias="X-Conjectures-Signature"),
) -> schemas.Accepted:
    """Accept two files signed by a registered hotkey and queue them for the gate.

    Submitting the same two files again returns the same id and does not re-queue them.

    The scalars are headers rather than form fields on purpose: `CORS_REQUEST_HEADERS`
    allowlists no `X-Conjectures-*` header, which is what keeps this endpoint unreachable
    from a browser even on an allowlisted origin. See `competition_sig`.
    """
    refuse_if_paused(services)
    competition = resolve_competition(services, slug)
    settings = services.settings

    await queries.compatible(session)
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
        signature=bytes.fromhex(signature.removeprefix("0x")) if _is_hex(signature) else b"",
    )
    result = await _queue(
        competition=competition,
        session=session,
        services=services,
        hotkey=hotkey,
        rust=rust,
        lean=lean,
        digest=digest,
    )
    response.status_code = 201 if result.created else 200
    return result


def _is_hex(value: str) -> bool:
    candidate = value.removeprefix("0x")
    return (
        bool(candidate)
        and len(candidate) % 2 == 0
        and all(c in "0123456789abcdefABCDEF" for c in candidate)
    )


@router.post(
    "/{slug}/submissions/session",
    response_model=schemas.Accepted,
    status_code=201,
    summary="Submit as a signed-in account",
    openapi_extra=UPLOAD_SCHEMA,
)
async def submit_as_account(
    request: Request,
    response: Response,
    services: ServicesDep,
    session: CompetitionSessionDep,
    principal: CookieWriterDep,
    slug: str = SlugPath,
    hotkey: str = Header(..., alias="X-Conjectures-Hotkey"),
) -> schemas.Accepted:
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
    await queries.compatible(session)
    allowed, _ = await queries.hit_rate_limit(
        session,
        f"hotkey:{hotkey}",
        limit=services.settings.competition_rate_per_minute,
        window_seconds=60,
    )
    await session.commit()
    if not allowed:
        raise TooManyRequests("Submission rate limit exceeded", reason_code="RATE_LIMITED")
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
    result = await _queue(
        competition=competition,
        session=session,
        services=services,
        hotkey=hotkey,
        rust=rust,
        lean=lean,
        digest=digest,
        account_id=principal.account.id,
    )
    response.status_code = 201 if result.created else 200
    return result
