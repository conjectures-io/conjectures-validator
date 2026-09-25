"""Where the competition weight vector comes from: the platform's own API, over HTTP.

Not the database, and that is deliberate. `docker-compose.emissions.yml` gives this
container `read_only: true`, `cap_drop: ALL`, no `DATABASE_URL` and one mount -- the wallet.
It is the only process on the subnet holding a key that can set weights, and it touches no
database today. Granting it one to save an HTTP call would trade a real isolation property
for a convenience.

Everything here fails toward the treasury. A stale vector, an unreachable API, a body that
does not parse: all of them return `None`, and the caller submits the treasury-only vector.
Never a skipped epoch -- an epoch's emissions cannot be set retroactively, so declining to
submit is a decision to pay nobody, which is worse than paying the treasury.

Staleness is checked rather than trusted. A scorer that died an hour ago leaves an endpoint
that still answers, with a vector describing a leaderboard that has moved; paying that is
worse than paying nothing, because it looks like it worked.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol, cast

from conjectures_subnet.axiom import get_axiom

logger = logging.getLogger("emissions_worker")

DEFAULT_MAX_AGE_SECONDS = 1200.0
DEFAULT_TIMEOUT_SECONDS = 15.0


class VectorSource(Protocol):
    def scores(self) -> dict[str, dict[str, float]]:
        """Per-competition, per-hotkey scores. Empty means "pay the treasury"."""
        ...


@dataclass(frozen=True, slots=True)
class NoVectorSource:
    """What an unconfigured deployment gets: today's behaviour, bit for bit."""

    def scores(self) -> dict[str, dict[str, float]]:
        return {}


@dataclass(frozen=True, slots=True)
class HttpVectorSource:
    """Reads `GET /v1/competitions/{slug}/weights/current` from the platform."""

    url: str
    slug: str
    token: str = ""
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    def scores(self) -> dict[str, dict[str, float]]:
        try:
            payload = self._fetch()
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            self._unavailable("competition_vector_unavailable", str(exc))
            return {}
        except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            self._unavailable("competition_vector_malformed", str(exc))
            return {}

        age = self._age(payload)
        if age is None:
            self._unavailable("competition_vector_malformed", "no usable computed_at")
            return {}
        if age > self.max_age_seconds:
            self._unavailable(
                "competition_vector_stale",
                f"{age:.0f}s old, limit {self.max_age_seconds:.0f}s",
            )
            return {}

        weights = payload.get("weights")
        if not isinstance(weights, dict):
            self._unavailable("competition_vector_malformed", "weights is not an object")
            return {}
        scores = {
            str(hotkey): float(value)
            for hotkey, value in cast("dict[str, Any]", weights).items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        logger.info(
            "competition %s vector: %d hotkey(s), %.0fs old", self.slug, len(scores), age
        )
        return {self.slug: scores}

    def _fetch(self) -> dict[str, Any]:
        request = urllib.request.Request(self.url, method="GET")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            return cast("dict[str, Any]", json.loads(response.read().decode()))

    def _age(self, payload: dict[str, Any]) -> float | None:
        stamp = payload.get("computed_at")
        if not isinstance(stamp, str):
            return None
        try:
            computed = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            return None
        if computed.tzinfo is None:
            computed = computed.replace(tzinfo=dt.UTC)
        return (dt.datetime.now(dt.UTC) - computed).total_seconds()

    def _unavailable(self, event: str, detail: str) -> None:
        # `error`, not warning: this epoch pays no competitor. That is the safe outcome and
        # still one somebody should be told about, because a run of them means a competition
        # is earning nothing while appearing to run.
        logger.error("competition vector unusable (%s): %s", event, detail)
        # `.error` sets the severity itself; passing it again is a duplicate keyword.
        get_axiom().error(
            source="emissions-worker",
            event_type=event,
            competition=self.slug,
            detail=detail[:500],
        )


def from_env(environ: dict[str, str]) -> VectorSource:
    """Build the source this deployment configured, or the no-op that preserves today."""
    url = environ.get("EMISSIONS_COMPETITION_WEIGHTS_URL", "").strip()
    if not url:
        return NoVectorSource()
    return HttpVectorSource(
        url=url,
        slug=environ.get("EMISSIONS_COMPETITION_SLUG", "").strip() or "lz77",
        token=environ.get("EMISSIONS_COMPETITION_TOKEN", "").strip(),
        max_age_seconds=float(
            environ.get("EMISSIONS_VECTOR_MAX_AGE_SECONDS", DEFAULT_MAX_AGE_SECONDS)
        ),
    )
