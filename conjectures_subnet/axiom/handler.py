"""The bridge: every `logging` call this codebase already makes, forwarded to Axiom.

The explicit `get_axiom().info(...)` calls in the API and the workers cover the moments worth
naming — a verdict recorded, a deposit credited, a request answered. This module covers everything
else, and "everything else" is the larger half: several dozen existing `logger.info`,
`logger.warning` and `logger.exception` call sites carrying the detail an incident actually turns
on, including the `logger.exception("verification pass failed; continuing")` that is the only
record of a worker swallowing a crash.

Rewriting those into explicit events would mean editing every one, choosing an event type for
each, and getting the same coverage a handler gives for free. So the records are forwarded as
they are, under one event type — `log_record` — with the two labels the vocabulary demands:

* **severity** from `record.levelno`, so a `logger.exception` arrives as `error` and a
  `logger.info` as `info`.
* **source** from `record.name`, via `LOGGER_SOURCES`. The loggers in this repository are already
  named after the area that owns them (`deposit_watcher`, `submission_api.mail`), so the mapping
  is mostly a translation into the `Source` spelling, with a longest-prefix walk so a new
  `submission_api.something` logger lands on `api` rather than nowhere.

Anything passed as `extra=` becomes a structured field, so a call site that wants one can add it
without leaving the logging API.

**Two traps this module exists to avoid.** First, recursion: a failing transport logs a warning,
and forwarding that warning would enqueue an event about failing to enqueue events. Records from
the transport's own logger are dropped — see `SELF_LOGGER`. Second, the root logger's level: a
handler never sees a record its logger already filtered, so `AXIOM_LOG_LEVEL=DEBUG` under
`--log-level INFO` still forwards only INFO and above. The handler's level can narrow what
reaches Axiom, never widen it.
"""

from __future__ import annotations

import logging
import os
import traceback
from collections.abc import Mapping
from typing import Any, Final

from conjectures_subnet.axiom.context import current_correlation_id
from conjectures_subnet.axiom.labels import Severity, Source
from conjectures_subnet.axiom.sink import Axiom, get_axiom

# The transport logs here. Records from this namespace are never forwarded; see the docstring.
SELF_LOGGER: Final = "conjectures_subnet.axiom"

DEFAULT_LOG_FORMAT: Final = "%(asctime)s %(levelname)s %(name)s %(message)s"

# Logger name to the area that owns it. Matched on dotted prefixes, longest first, so the entries
# below are a base and a logger named `submission_api.routers.intents` resolves through `api`
# without needing its own line.
LOGGER_SOURCES: Mapping[str, Source] = {
    "verification_worker": "verification-worker",
    "deposit_watcher": "deposit-watcher",
    "emissions_worker": "emissions-worker",
    "conjectures_subnet.transfers": "subnet-chain",
    "conjectures_subnet.chain": "subnet-chain",
    "conjectures_subnet.bounty": "subnet-chain",
    "conjectures_subnet.db": "database",
    "submission_api": "api",
    "submission_api.mail": "api-mail",
    "submission_api.chain_payments": "api-payments",
    "submission_api.payments": "api-payments",
    "submission_api.middleware": "api-middleware",
    "submission_api.ratelimit": "api-middleware",
    "submission_api.routers.auth": "api-auth",
    "submission_api.routers.catalog": "api-catalog",
    "submission_api.routers.intents": "api-intents",
    "submission_api.routers.me": "api-me",
    "submission_api.routers.results": "api-results",
    "submission_api.routers.submissions": "api-submissions",
    "submission_api.routers.system": "api-system",
    "submission_api.routers.tasks": "api-tasks",
    "verifier": "verifier",
    # Third-party loggers, mapped so their records are attributed rather than defaulted. uvicorn
    # is where an unhandled traceback from a route actually surfaces.
    "uvicorn": "api",
    "gunicorn": "api",
    "sqlalchemy": "database",
    "aiohttp": "subnet-chain",
    "websockets": "subnet-chain",
    "bittensor": "subnet-chain",
}

# `LogRecord` attributes the logging module owns. Everything else on the record was put there by a
# caller's `extra=` and is forwarded as a field. Derived from a real record rather than hardcoded,
# so a new attribute in a future Python does not start arriving as a bogus event field.
_RESERVED: Final = frozenset(
    vars(logging.LogRecord(name="", level=0, pathname="", lineno=0, msg="", args=(), exc_info=None))
) | {"message", "asctime", "taskName"}


def severity_for_level(levelno: int) -> Severity:
    """A stdlib level number as a `Severity`.

    Compared with `>=` rather than matched exactly, because custom levels exist: `logging.log(25,
    …)` is between INFO and WARNING and belongs with the one below it, not in a sixth bucket.
    """
    if levelno >= logging.CRITICAL:
        return Severity.CRITICAL
    if levelno >= logging.ERROR:
        return Severity.ERROR
    if levelno >= logging.WARNING:
        return Severity.WARNING
    if levelno >= logging.INFO:
        return Severity.INFO
    return Severity.DEBUG


def source_for_logger(name: str, *, default: Source) -> Source:
    """The area that owns a logger, by longest dotted-prefix match.

    `submission_api.routers.me` hits its own entry; `submission_api.routers.whatever_is_next`
    falls back through `submission_api` to `api`. The default is the entry point's own source, so
    a record from a library nobody mapped is attributed to the process that produced it rather
    than to a guess.
    """
    parts = name.split(".")
    while parts:
        found = LOGGER_SOURCES.get(".".join(parts))
        if found is not None:
            return found
        parts.pop()
    return default


class AxiomLogHandler(logging.Handler):
    """Forwards stdlib log records to Axiom as `log_record` events.

    Cheap enough to sit on the root logger: formatting is a `getMessage()` and a dict build, and
    the transport's `ingest` is a queue put.
    """

    def __init__(
        self,
        *,
        default_source: Source,
        level: int = logging.INFO,
        axiom: Axiom | None = None,
    ) -> None:
        super().__init__(level)
        self._default_source = default_source
        # Injectable for tests. Resolved lazily otherwise, so installing the handler does not
        # force the transport to be built before the process has finished reading its settings.
        self._axiom = axiom

    def emit(self, record: logging.LogRecord) -> None:
        if record.name == SELF_LOGGER or record.name.startswith(f"{SELF_LOGGER}."):
            # The transport reporting its own failure. Forwarding it would be the recursion this
            # guard exists for.
            return
        try:
            sink = self._axiom if self._axiom is not None else get_axiom()
            sink.emit(
                severity=severity_for_level(record.levelno),
                source=source_for_logger(record.name, default=self._default_source),
                event_type="log_record",
                **self._fields(record),
            )
        except Exception:  # noqa: BLE001 — logging.Handler's contract for a failing handler
            # Routes to stderr or is swallowed per `logging.raiseExceptions`. Never propagates
            # into the code that logged.
            self.handleError(record)

    def _fields(self, record: logging.LogRecord) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "message": record.getMessage(),
            "logger": record.name,
            "level": record.levelname,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info is not None:
            fields["exception"] = "".join(traceback.format_exception(*record.exc_info))
        elif record.exc_text:  # pragma: no cover — set by a formatter that already ran
            fields["exception"] = record.exc_text
        if record.stack_info:
            fields["stack"] = record.stack_info
        correlation = current_correlation_id()
        if correlation is not None:
            fields["request_id"] = correlation
        # `extra=` last: a call site that passed `line=` or `message=` deliberately meant it.
        fields.update(
            {
                key: value
                for key, value in vars(record).items()
                if key not in _RESERVED and not key.startswith("_")
            }
        )
        return fields


def _level_from_env(environ: Mapping[str, str], key: str, default: int) -> int:
    """A level name or number from the environment, or the default. Never raises.

    An unparseable `AXIOM_LOG_LEVEL` falls back rather than stopping the process, for the reason
    `env_factory` gives: a typo here must not take a validator down.
    """
    raw = environ.get(key, "").strip()
    if not raw:
        return default
    if raw.isdigit():
        return int(raw)
    resolved = logging.getLevelNamesMapping().get(raw.upper())
    if resolved is None:
        logging.getLogger(SELF_LOGGER).warning(
            "%s=%r is not a log level; using %s", key, raw, logging.getLevelName(default)
        )
        return default
    return resolved


def attach_axiom_handler(
    *,
    source: Source,
    logger: logging.Logger | None = None,
    environ: Mapping[str, str] | None = None,
    axiom: Axiom | None = None,
) -> AxiomLogHandler | None:
    """Install the bridge on the root logger. Idempotent; returns None when there is nothing to do.

    Skipped entirely when the process has no Axiom credentials — a handler that formats every
    record only to hand it to a no-op is pure cost — and skipped when one is already attached, so
    a second entry point in the same process (a test, a `--check` run) does not double every event.
    """
    sink = axiom if axiom is not None else get_axiom()
    if not sink.enabled:
        return None
    target = logger if logger is not None else logging.getLogger()
    existing = next(
        (item for item in target.handlers if isinstance(item, AxiomLogHandler)), None
    )
    if existing is not None:
        return existing
    handler = AxiomLogHandler(
        default_source=source,
        level=_level_from_env(
            os.environ if environ is None else environ, "AXIOM_LOG_LEVEL", logging.INFO
        ),
        axiom=axiom,
    )
    target.addHandler(handler)
    return handler


def configure_logging(
    *,
    source: Source,
    level: str | int = "INFO",
    log_format: str = DEFAULT_LOG_FORMAT,
    environ: Mapping[str, str] | None = None,
) -> None:
    """What an entry point calls instead of `logging.basicConfig`.

    Same stderr logging every entry point already had — the format string is unchanged, so
    existing log scraping keeps working — plus the Axiom bridge when credentials are configured.
    Axiom is additive here: nothing stops being written to stderr because it is also shipped.
    """
    logging.basicConfig(
        level=level.upper() if isinstance(level, str) else level,
        format=log_format,
    )
    attach_axiom_handler(source=source, environ=environ)


__all__ = [
    "DEFAULT_LOG_FORMAT",
    "LOGGER_SOURCES",
    "SELF_LOGGER",
    "AxiomLogHandler",
    "attach_axiom_handler",
    "configure_logging",
    "severity_for_level",
    "source_for_logger",
]
