"""The reviewer's queue: submissions awaiting a reward decision, and what the models said.

Read-only, and role-gated at the router rather than per route. Every path here answers with model
prose about a named miner's submission, so there is no mixed surface to reason about: if the caller
does not hold `REVIEWER`, none of it is served.

**Separate from `routers/admin.py`, which shares the `/v1/admin` prefix.** That module is the
operator surface — who holds which role, and cutting an account off — and every one of its routes
acts *against another account* under `ADMIN`. This one only reads submissions, under `REVIEWER`.
Same prefix because both are the console's, different modules because they are different jobs with
different roles, and neither path is a prefix of the other (`/accounts` and `/reviews`).

**Why these live in this API rather than beside the panel.** The review panel is a page in the
public site's Next.js app, which reaches exactly one API base and forwards the caller's session
cookie on server-side fetches. Serving the queue from a second service would mean a second base URL,
a second credential and a second database login for data the session already authorises. The role
seam this uses was written for this queue — see `dependencies.require_role`.

**Composed, not re-implemented.** The submission half of every response comes from
`conjectures_subnet.db.public`, the same queries `/v1/results/in-review` reads, and the conjecture
is named by `results.named_of`, the same function the public feed uses. Only the advisory half is
new. A reviewer and a visitor therefore cannot be shown different answers to "which submissions are
awaiting review" or "what is this conjecture called".

**One route writes, and it is the one the queue exists for.**
`POST /reviews/{submission_id}/decision` records the binding decision and advances
`reward_status`, which makes it the only action on this surface that spends money. It lives here
rather than in a second service for the same reason the reads do — the panel reaches one API base
with one session — and the concurrency argument it needs is answered rather than assumed:
`submissions.record_human_decision` takes `FOR UPDATE` on the submission row before it reads the
status it is about to overwrite, so two reviewers deciding the same submission are serialised and
the second one is refused rather than overwriting the first. Nothing else here writes.

The write is gated more tightly than the reads. `require_role_writer` adds the cross-site write
guard to the role check, so a decision cannot be driven by another origin holding the reviewer's
ambient cookie, and the CLI exclusion the reads inherit matters most here: this is the request
that turns a proof into a payout.

The queue lists `UNREVIEWED` submissions. The detail route serves any Lean-verified one, decided or
not, so a reviewer can reread the advisory record behind a decision already taken — the same
asymmetry `public_result` has, and for the same reason: a wider list would change what the queue
means, while reading one row by id changes nothing.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from conjectures_subnet.axiom import get_axiom
from conjectures_subnet.db import autoreview as autoreview_store
from conjectures_subnet.db import digests
from conjectures_subnet.db import public as public_store
from conjectures_subnet.db import submissions as submission_store
from conjectures_subnet.db.models import REVIEWER_ROLE, ReviewOutcome, Submission
from submission_api import schemas_admin as admin
from submission_api.conjectures import ConjectureIndex
from submission_api.credits import APPROVAL_CODES, DISQUALIFICATION_CODES
from submission_api.dependencies import (
    ServicesDep,
    SessionDep,
    require_role,
    require_role_writer,
)
from submission_api.errors import BadRequest, NotFound
from submission_api.pagination import decode_cursor, encode_cursor
from submission_api.routers.results import named_of, slug_of
from submission_api.schemas_public import CursorPage
from submission_api.sessions import Principal
from submission_api.settings import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, Settings

router = APIRouter(
    prefix="/v1/admin",
    tags=["admin"],
    # On the router, so a route added here later is gated by default rather than by remembering
    # to repeat the dependency. `routers/admin.py` annotates per route instead because its
    # writes need `require_role_writer` and its reads do not; this router has no writes, so one
    # dependency covers it and there is nothing an anonymous caller may read.
    #
    # `require_role` also refuses to exercise REVIEWER from a CLI bearer token — see
    # `dependencies.BEARER_ROLES`. That is inherited rather than asked for, and it is right: a
    # hotkey-minted token in a file on a mining box should not open the queue that decides
    # whether a proof earns money.
    dependencies=[Depends(require_role(REVIEWER_ROLE))],
)


# Built once at module scope, for the reason `routers/admin.py` gives: `require_role_writer`
# returns a fresh closure per call, and FastAPI caches a resolved dependency per request by
# function identity, so a factory invoked inline in a signature resolves twice.
#
# The router-level `require_role` still runs for this route as well. That is a duplicated role
# lookup on one request and it is worth keeping: the router-level declaration is what makes a
# route added here later gated by default, and this annotation is what says *in the signature*
# that this particular route writes.
ReviewerWriter = Annotated[Principal, Depends(require_role_writer(REVIEWER_ROLE))]

# What a reviewer's published explanation may be. The column permits 100 000 characters
# (`review_notes_public_length`); this is deliberately far below it, because the field is the
# concise miner-visible explanation the policy asks for and the full rationale is published as a
# document under `docs/review-decisions/` with its citations. A cap the panel's textarea cannot
# reach by accident also means a paste of the whole rationale is refused rather than half-stored.
NOTES_PUBLIC_MAX = 4_000
NOTES_MAX = 20_000

REASON_EXPLANATION_REQUIRED = "REVIEW_EXPLANATION_REQUIRED"
REASON_CODE_NOT_ALLOWED = "REVIEW_REASON_NOT_ALLOWED"


class DecisionRequest(BaseModel):
    """One binding decision, as the panel's modal sends it.

    `extra="forbid"`, like every other request body on this API: a misspelled field name is a
    reviewer's decision going somewhere other than where they think it is, and on this endpoint
    that means the wrong reason code attached to a payout.

    Two text fields, and the difference between them is the whole reason the column pair exists.
    `notes_public` is published — it reaches the miner on `GET /v1/results/{id}` and is the
    "concise miner-visible explanation" the review policy requires of every binding decision, so
    it is required here rather than optional. `notes` is the internal audit trail and never
    crosses an API boundary again, in either direction: nothing reads it back out to a client.

    `decision` is spelled as the two outcomes rather than as `approve: bool`, so the request says
    what it does and matches `review_decisions.decision` and every status this API serves.
    """

    model_config = ConfigDict(extra="forbid")

    decision: Literal["APPROVED", "REJECTED"]
    reason_code: str = Field(
        min_length=1,
        max_length=64,
        pattern="^[A-Z][A-Z0-9_]*$",
        description=(
            "One published policy code: an approval code with APPROVED, a disqualification "
            "code with REJECTED. Refused with REVIEW_REASON_NOT_ALLOWED otherwise."
        ),
    )
    notes_public: str = Field(
        min_length=1,
        max_length=NOTES_PUBLIC_MAX,
        description="The explanation shown to the miner on the public record.",
    )
    notes: str | None = Field(
        default=None,
        max_length=NOTES_MAX,
        description="Internal audit trail. Never served back by any endpoint.",
    )


def _codes_for(decision: str) -> frozenset[str]:
    """The reason codes that decision may carry.

    `submission_api.credits` is the single source of these two sets, and it is the same list
    `GET /v1/system/terms` publishes to a miner before they spend a credit. So a reviewer cannot
    reject under a code the miner was never shown, and the panel's local copy of the list — see
    `lib/admin/reasons.ts` — is checked against the published one rather than trusted.
    """
    return APPROVAL_CODES if decision == "APPROVED" else DISQUALIFICATION_CODES


def _no_store(response: Response) -> None:
    """Never cached, anywhere.

    The public feeds set `public, max-age=...` because their bodies are the same for every caller.
    These bodies are not: they are authorised by a session cookie and carry unpublished review
    material, so a shared cache in front of this API must not keep one reviewer's page to answer the
    next request. `no-store` rather than `private` because the deployment puts nginx in front of the
    site, and `private` still permits the browser disk cache to keep it.

    `Vary` alongside it, matching `routers/admin.py`: `no-store` already forbids reuse, but two
    credentials can now authorise these bodies, and a cache that ignored the directive would
    otherwise have nothing telling it the response is per-caller.
    """
    response.headers["Cache-Control"] = "no-store"
    response.headers["Vary"] = "Authorization, Cookie"


def _search(raw: dict[str, Any] | None) -> admin.AdminSearch | None:
    if raw is None:
        return None
    return admin.AdminSearch(
        id=raw.get("id"), engine=raw.get("engine"), max_results=raw.get("max_results")
    )


def _verdict(raw: dict[str, Any] | None) -> admin.AdminVerdict | None:
    """The archived verdict, field by named field.

    Named rather than splatted: `**raw` would forward whatever `conjectures-autoreview` writes into
    the column next, and `extra="forbid"` would then turn a new field upstream into a 500 here. This
    way a new field is invisible until someone adds a line, which is the failure mode worth having.
    """
    if raw is None:
        return None
    return admin.AdminVerdict(
        reason_code=raw.get("reason_code"),
        confidence=raw.get("confidence"),
        summary=raw.get("summary"),
        findings=tuple(
            admin.AdminFinding(
                claim=item.get("claim"),
                quote=item.get("quote"),
                where=item.get("where"),
                severity=item.get("severity"),
            )
            for item in raw.get("findings") or ()
        ),
        input_attempted_to_instruct=raw.get("input_attempted_to_instruct"),
        informal_reading=raw.get("informal_reading"),
        formal_reading=raw.get("formal_reading"),
        settled_portion=raw.get("settled_portion"),
        definitions_not_shown=tuple(raw.get("definitions_not_shown") or ()),
        target_reading=raw.get("target_reading"),
        searched_for=tuple(raw.get("searched_for") or ()),
        prior_sources=tuple(
            admin.AdminPriorSource(
                url=item.get("url"),
                title=item.get("title"),
                published=item.get("published"),
                date_evidence=item.get("date_evidence"),
                establishes=item.get("establishes"),
                resolves_this_target=item.get("resolves_this_target"),
                correspondence=item.get("correspondence"),
            )
            for item in raw.get("prior_sources") or ()
        ),
        catalogue_signal=raw.get("catalogue_signal"),
    )


def _attempt(row: autoreview_store.AttemptRow) -> admin.AdminStageAttempt:
    return admin.AdminStageAttempt(
        key=row.key,
        submission_id=row.submission_id,
        attempt=row.attempt,
        stage=row.stage,
        stage_version=row.stage_version,
        # The enum's value, matching how every other status on this API serialises.
        status=str(row.status),
        detail=row.detail,
        outcome=None if row.outcome is None else str(row.outcome),
        model_requested=row.model_requested,
        model_served=row.model_served,
        provider=row.provider,
        search=_search(dict(row.search) if row.search is not None else None),
        usage=admin.AdminUsage(
            prompt_tokens=row.prompt_tokens, completion_tokens=row.completion_tokens
        ),
        # `str`, not `float`: six decimal places of a NUMERIC column survive a string and do not
        # survive a float, and this is what an operator reconciles a provider invoice against.
        cost_usd=None if row.cost_usd is None else str(row.cost_usd),
        citations=tuple(
            admin.AdminCitation(
                url=item.get("url"),
                title=item.get("title"),
                retrieved_at=item.get("retrieved_at"),
            )
            for item in row.citations
        ),
        verdict=_verdict(dict(row.verdict) if row.verdict is not None else None),
        review_policy_version=row.review_policy_version,
        started_at=row.started_at,
        finished_at=row.finished_at,
    )


def _review(
    row: public_store.ResultRow,
    index: ConjectureIndex,
    attempts: tuple[autoreview_store.AttemptRow, ...],
) -> admin.AdminReview:
    named = named_of(index, row)
    return admin.AdminReview(
        submission_id=row.id,
        slug=slug_of(row),
        display_title=named.display_title,
        task_id=row.task_id,
        hotkey=row.hotkey,
        statement=named.statement,
        task_bundle_sha256=digests.to_prefixed(row.task_bundle_sha256),
        verified_at=row.verified_at,
        review_policy_version=row.review_policy_version,
        report_available=row.report_available,
        manual_review_status=str(row.manual_review_status),
        attempts=tuple(_attempt(attempt) for attempt in attempts),
    )


@router.get(
    "/reviews",
    response_model=CursorPage[admin.AdminReview],
    summary="Submissions awaiting a reward decision, with every advisory assessment recorded",
)
async def list_reviews(
    response: Response,
    services: ServicesDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
) -> CursorPage[admin.AdminReview]:
    """Lean-verified submissions with no binding decision yet, newest first.

    The assessments are embedded rather than fetched per row. The panel renders a verdict cell per
    stage on the queue itself and expands the full attempt inline, so a list of submissions alone
    would be followed immediately by one request per row — and the whole page costs one extra query
    here, keyed by the ids already read.

    Ordered newest first, like every other feed in this API, and paged by the same signed keyset
    cursor. Oldest-first would be the fairer queue order, and the panel sorts by its own attention
    score anyway; matching the rest of the API is worth more than a default the client overrides.

    A submission the advisory service has not reached carries `attempts: []`. It belongs on the
    queue regardless: review is required for it, and a reviewer may decide it unaided.
    """
    settings: Settings = services.settings
    after = None
    if cursor:
        position = decode_cursor(settings.cursor_secret, cursor)
        after = (position.created_at, position.id)

    # `limit + 1`, so `next_cursor` is null exactly when the feed is exhausted rather than
    # addressing an empty page — the same trick `results._feed` uses.
    rows = await public_store.in_review_page(session, limit=limit + 1, after=after)
    page = rows[:limit]
    attempts = await autoreview_store.attempts_for(session, [row.id for row in page])

    next_cursor = None
    if len(rows) > limit and page:
        last = page[-1]
        next_cursor = encode_cursor(
            settings.cursor_secret, created_at=last.created_at, id=last.id
        )

    _no_store(response)
    return CursorPage[admin.AdminReview](
        items=tuple(
            _review(row, services.index, attempts.get(row.id, ())) for row in page
        ),
        next_cursor=next_cursor,
    )


@router.get(
    "/reviews/{submission_id}",
    response_model=admin.AdminReview,
    summary="One submission's full advisory record, whether or not it is still awaiting review",
)
async def read_review(
    response: Response,
    services: ServicesDep,
    session: SessionDep,
    submission_id: Annotated[uuid.UUID, Path()],
) -> admin.AdminReview:
    """One submission and every assessment recorded against it.

    Serves any Lean-verified submission, not only the ones still on the queue: the advisory record
    behind a decision already taken is exactly what somebody asks for when a decision is questioned,
    and withholding it after the fact would make the queue the only place it could ever be read.

    A submission Lean has not verified answers `404` rather than `403`, matching
    `GET /v1/results/{id}`. There is no advisory record to serve for unverified work — the service
    does not assess it — so the distinction would only tell a caller that an id exists.
    """
    row = await public_store.public_result(session, submission_id)
    if row is None:
        raise NotFound("no such submission")
    attempts = await autoreview_store.attempts_for(session, [row.id])

    _no_store(response)
    return _review(row, services.index, attempts.get(row.id, ()))


@router.post(
    "/reviews/{submission_id}/decision",
    response_model=admin.AdminDecision,
    status_code=status.HTTP_201_CREATED,
    summary="Record the binding review decision for one submission",
)
async def record_decision(
    response: Response,
    session: SessionDep,
    principal: ReviewerWriter,
    payload: DecisionRequest,
    submission_id: Annotated[uuid.UUID, Path()],
) -> admin.AdminDecision:
    """Decide one submission: approve it for its reward, or refuse it under a published code.

    `201`, and a new `review_decisions` row every time — the table is append-only, so this creates
    a record rather than editing the submission's review state, even though it also advances the
    two summary columns the rest of the system reads.

    Three refusals, and which one a caller gets is the useful part:

    * `404` for a submission that does not exist *or* that Lean has not verified, matching
      `GET /v1/admin/reviews/{id}`. Nothing here can make an unverified proof payable, so the
      distinction would only tell a caller that an id exists;
    * `400 REVIEW_REASON_NOT_ALLOWED` for a code outside the published allowlist for that
      outcome, with the permitted codes in the body. A rejection code on an approval is the
      mistake this catches, and it is a policy error rather than a malformed request, so the
      permitted set is served rather than left to be guessed;
    * `409` for state: `REVIEW_ALREADY_DECIDED` if another reviewer got there first — the row
      lock makes that answer authoritative rather than a race — `REWARD_TARGET_ALREADY_HELD` if
      an earlier submission already holds this target's single reward, and
      `REWARD_ALREADY_IN_FLIGHT` for the anomaly of an undecided submission whose reward is
      already moving.

    Retrying is safe in the sense that matters: a repeat of a decision that landed is refused with
    `REVIEW_ALREADY_DECIDED` rather than recording a second one, so a double-click cannot write
    two decisions or two payouts. It is not idempotent — the second call is an error, not a
    replay of the first — and the panel reads the state off the `409` instead of a `201`.
    """
    if payload.reason_code not in _codes_for(payload.decision):
        raise BadRequest(
            f"{payload.reason_code} is not a published reason code for a "
            f"{payload.decision} decision",
            reason_code=REASON_CODE_NOT_ALLOWED,
            extra={"allowed_reason_codes": sorted(_codes_for(payload.decision))},
        )

    explanation = payload.notes_public.strip()
    if not explanation:
        # Pydantic's `min_length` counts characters, so a body of spaces reaches here. The column
        # would refuse it too (`review_notes_public_length`); this answers with the reason a
        # reviewer can act on rather than a constraint name.
        raise BadRequest(
            "a binding decision must carry the explanation shown to the miner",
            reason_code=REASON_EXPLANATION_REQUIRED,
        )
    internal = (payload.notes or "").strip() or None

    # Raises RecordNotFound (404) or RecordConflict (409); both are mapped by
    # `errors.from_database_error`, so the state rules live with the write that enforces them
    # rather than being re-checked here against a row this request has not locked.
    recorded = await submission_store.record_human_decision(
        session,
        submission_id,
        decision=ReviewOutcome(payload.decision),
        reason_code=payload.reason_code,
        # The account id, never the email address: the privacy rule `routers/admin.py` sets out
        # for events applies to a stored column with more force, because this one is permanent.
        # `reviewer` is `TEXT` for the model ids ADVISORY rows carry.
        reviewer=str(principal.account.id),
        notes=internal,
        notes_public=explanation,
    )
    # The row the call above locked and updated, from the identity map rather than the database.
    # Read before the commit and kept as plain strings: what the panel is told the decision did
    # must be the state this transaction wrote, not a re-read that a later one could have moved.
    submission = await session.get(Submission, submission_id)
    if submission is None:  # pragma: no cover - the write above holds the row
        raise NotFound("no such submission")
    review_status = str(submission.manual_review_status)
    reward_status = str(submission.reward_status)

    await session.commit()

    # After the commit, and naming both the submission and the reviewer. The decision row is the
    # durable record; this is what makes the *act* queryable next to every other privileged write,
    # which is what an operator asks for when a payout is questioned. The explanation itself is
    # not in the event: it is published on the public record, and duplicating it here would put
    # miner-visible prose in the log without anyone deciding to.
    get_axiom().info(
        source="api-review",
        event_type="review_decision_recorded",
        submission_id=str(submission_id),
        review_decision_id=recorded.id,
        actor_account_id=str(principal.account.id),
        decision=str(recorded.decision),
        reason_code=recorded.reason_code,
        policy_version=recorded.policy_version,
        manual_review_status=review_status,
        reward_status=reward_status,
    )

    _no_store(response)
    return admin.AdminDecision(
        submission_id=submission_id,
        decision=str(recorded.decision),
        reason_code=recorded.reason_code,
        notes_public=recorded.notes_public,
        policy_version=recorded.policy_version,
        decided_at=recorded.created_at,
        manual_review_status=review_status,
        reward_status=reward_status,
    )
