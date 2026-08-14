"""The closed vocabulary every Axiom event is described by.

Three labels, and they are the reason the dataset is queryable at all:

* `Severity` — how bad it is. One ladder, shared by the explicit event helpers and by the
  stdlib-logging bridge, so `severity == "error"` means the same thing whichever produced it.
* `Source` — which area of this system spoke. One value per API router plus one per background
  service, because "the API is erroring" and "the intent endpoints are erroring" are different
  incidents and a dashboard has to be able to tell them apart.
* `EventType` — what happened, as a stable name a query and an alert can be written against.

All three are `Literal` aliases rather than free-form strings so a typo is a type error rather
than a silently unqueryable field, and `SEVERITIES`/`SOURCES`/`EVENT_TYPES` expose the same sets
at runtime for validation and for tests that assert an emitted label is a declared one.

Adding a value is a deliberate act: a new source or event type is a new thing a dashboard can be
built on, and one added in passing is one nobody knows to look at.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, TypeAlias, get_args


class Severity(StrEnum):
    """How bad the event is, in the vocabulary the logging module already taught everyone.

    Five levels rather than three, because the bridge in `handler.py` forwards existing
    `logger.*` calls and those already use all five. Collapsing `critical` into `error` or
    `debug` into `info` would throw away a distinction the call sites had already made.
    """

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# Which area of the system the event came from. The API is split per router rather than reported
# as one "api", because the routers have genuinely different audiences and failure modes: the
# public catalog is read by a browser, `/v1/submissions` is written by miner tooling with a
# hotkey signature, and `/v1/me` is a signed-in account surface. One label for all three would
# make every dashboard start with a path filter.
Source: TypeAlias = Literal[
    # --- submission API ---------------------------------------------------------------------
    # The process itself: startup, shutdown, and anything that is not attributable to a router.
    "api",
    "api-admin",
    "api-auth",
    "api-catalog",
    "api-health",
    "api-intents",
    "api-me",
    "api-results",
    "api-submissions",
    "api-system",
    "api-tasks",
    # The cross-cutting ASGI layers — rate limiting, CORS, CSRF, security headers.
    "api-middleware",
    # Outbound side effects the API owns, worth separating because they fail for reasons that
    # have nothing to do with the request that triggered them.
    "api-mail",
    "api-payments",
    # --- background services ----------------------------------------------------------------
    "verification-worker",
    "deposit-watcher",
    "payout-watcher",
    "emissions-worker",
    "autoreview",
    # Sweeps the TMC PAY orders no webhook resolved. Separate from `deposit-watcher` because it
    # watches a payment processor rather than a chain, and its failures are HTTP ones.
    "tmc-pay-reconciler",
    # --- shared infrastructure --------------------------------------------------------------
    "subnet-chain",
    "database",
    "verifier",
]


# What happened. Grouped by the area that raises it, and named after the fact rather than the
# call site, so renaming a function does not orphan a dashboard.
EventType: TypeAlias = Literal[
    # --- process lifecycle ------------------------------------------------------------------
    "service_started",
    "service_stopped",
    # A process that refused to start. Distinct from `unexpected_error`: nothing was retried,
    # and the fix is a configuration change rather than an investigation.
    "service_misconfigured",
    # --- HTTP -------------------------------------------------------------------------------
    # One per answered request, whatever the status. `severity` and `status` carry the outcome,
    # so a single event type keeps "how many requests, how fast, how many failed" one query.
    "request_completed",
    # A failing `/readyz`. Its own type because health probes are exempt from `request_completed`
    # — reporting one per interval would bury everything else — so this is the only thing that
    # says a replica has taken itself out of service.
    "readiness_degraded",
    # --- submission intake ------------------------------------------------------------------
    "submission_accepted",
    "submission_rejected",
    "payment_accepted",
    "payment_rejected",
    # --- credit intents ---------------------------------------------------------------------
    "intent_opened",
    "intent_bundle_stored",
    "intent_committed",
    # --- accounts ---------------------------------------------------------------------------
    "login_link_sent",
    # Both kinds of sign-in. `method` distinguishes them and `session_kind` says which credential
    # was handed out, so "how much of our traffic is the CLI" is one query rather than a guess.
    "login_completed",
    "logout",
    "wallet_linked",
    # A session ended by something other than its own holder logging out: the per-account CLI
    # ceiling evicting the oldest token, an owner killing a session from the listing, or an
    # operator cutting an account off. `reason` says which. Worth its own type because a
    # credential ceasing to exist is the thing someone asks about after a compromise.
    "session_revoked",
    # An account's roles were replaced. `accounts.roles` is overwritten in place, so this event
    # is the only record that the change happened — see `routers/admin.py`.
    "roles_changed",
    # --- verification worker ----------------------------------------------------------------
    "submission_claimed",
    "verdict_recorded",
    # The lease expired under us, so a verdict we may have computed cannot be written.
    "lease_lost",
    # An accept produced without the production sandbox. Loud on purpose.
    "insecure_sandbox_accept",
    # The row will not be claimed again and an operator has to decide what is owed.
    "attempts_exhausted",
    "unclassified_reason_code",
    # Our failure rather than the miner's: no verdict written, the row goes back.
    "verification_operator_failure",
    # --- deposit watcher --------------------------------------------------------------------
    "cursor_opened",
    "blocks_scanned",
    "transfer_credited",
    "transfer_unattributed",
    "transfer_ignored",
    "transfer_conflict",
    # --- payout watcher --------------------------------------------------------------------
    "payout_confirmed",
    "payout_reorged",
    "payout_unmatched",
    # --- TMC PAY credit purchases -----------------------------------------------------------
    # The processor-settled funding path. Separate from the deposit watcher's `transfer_*` types
    # because the evidence is different in kind — a signed webhook rather than finalized chain
    # state — and an operator auditing where credits came from has to be able to split the two.
    "tmc_pay_order_created",
    # Credits issued for a paid invoice. `applied_by` says whether a webhook or the reconciler got
    # there first, which is how "are our webhooks arriving at all" gets answered.
    "tmc_pay_order_credited",
    # A delivery whose HMAC did not verify. Either a secret rotation nobody coordinated or someone
    # probing the endpoint, and both need to be visible.
    "tmc_pay_webhook_rejected",
    # A correctly signed delivery for an invoice no order here claims.
    "tmc_pay_webhook_unmatched",
    # One reconciliation pass that found something: orders read, credited, unreadable.
    "tmc_pay_reconciled",
    # --- emissions worker -------------------------------------------------------------------
    "epoch_observed",
    "weights_set",
    "weights_failed",
    # --- catch-alls -------------------------------------------------------------------------
    # A stdlib `logging` record forwarded by `AxiomLogHandler`. Everything the codebase already
    # logged arrives under this type, carrying its logger name and severity.
    "log_record",
    "unexpected_error",
]


# The free-form half of an event: whatever fields the call site thought were worth recording.
Details: TypeAlias = dict[str, Any]

SEVERITIES: tuple[Severity, ...] = tuple(Severity)
SOURCES: tuple[str, ...] = get_args(Source)
EVENT_TYPES: tuple[str, ...] = get_args(EventType)


__all__ = [
    "EVENT_TYPES",
    "SEVERITIES",
    "SOURCES",
    "Details",
    "EventType",
    "Severity",
    "Source",
]
