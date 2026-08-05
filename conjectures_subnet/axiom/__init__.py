"""Structured observability for the validator: severity-tagged events, attributed to an area.

Every event carries the same three labels, which is what makes the dataset worth having:

    severity    debug | info | warning | error | critical
    source      which area of the system spoke — `api-submissions`, `deposit-watcher`, …
    event_type  what happened — `verdict_recorded`, `transfer_credited`, `request_completed`, …

plus whatever fields the call site added, an `environ` deployment tag, and a `request_id` when one
is in scope. `labels.py` holds the closed vocabulary; adding to it is deliberate.

Two ways in, and a deployment wants both:

**Explicit events**, at the moments worth naming.

    from conjectures_subnet.axiom import get_axiom

    get_axiom().info(
        source="verification-worker",
        event_type="verdict_recorded",
        submission_id=str(submission_id),
        accepted=accepted,
    )

**The stdlib bridge**, which forwards the `logger.*` calls already scattered through the API and
the workers, taking severity from the level and source from the logger name. Entry points get it
by calling `configure_logging` in place of `logging.basicConfig`:

    from conjectures_subnet.axiom import configure_logging

    configure_logging(source="deposit-watcher", level=args.log_level)

Nothing here is required for the validator to run. With `AXIOM_TOKEN` and `AXIOM_DATASET` unset
the sink is a no-op, ingestion never happens, and stderr logging is unchanged — see
`env_factory.py` on why observability is the one setting in this repository that is not
fail-closed. Ingestion is off the request path and never raises; see `client.py`.
"""

from conjectures_subnet.axiom.client import (
    AxiomClient,
    AxiomClientInterface,
    AxiomClientNoop,
)
from conjectures_subnet.axiom.context import (
    current_correlation_id,
    new_correlation_id,
    request_id,
    work_context,
)
from conjectures_subnet.axiom.env_factory import create_axiom_client_from_env
from conjectures_subnet.axiom.handler import (
    AxiomLogHandler,
    attach_axiom_handler,
    configure_logging,
    severity_for_level,
    source_for_logger,
)
from conjectures_subnet.axiom.labels import (
    EVENT_TYPES,
    SEVERITIES,
    SOURCES,
    Details,
    EventType,
    Severity,
    Source,
)
from conjectures_subnet.axiom.sink import Axiom, get_axiom, reset_axiom

__all__ = [
    "EVENT_TYPES",
    "SEVERITIES",
    "SOURCES",
    "Axiom",
    "AxiomClient",
    "AxiomClientInterface",
    "AxiomClientNoop",
    "AxiomLogHandler",
    "Details",
    "EventType",
    "Severity",
    "Source",
    "attach_axiom_handler",
    "configure_logging",
    "create_axiom_client_from_env",
    "current_correlation_id",
    "get_axiom",
    "new_correlation_id",
    "request_id",
    "reset_axiom",
    "severity_for_level",
    "source_for_logger",
    "work_context",
]
