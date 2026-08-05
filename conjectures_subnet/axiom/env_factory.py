"""Build the transport from the environment, and never refuse to start over it.

Unlike `submission_api.settings`, this module is deliberately not fail-closed. Every other
setting in this repository decides whether the validator is *correct* — which chain it reads,
which addresses it trusts, whether a proof was checked under real isolation — and a bad value
there must stop the process. Observability decides whether anyone is *watching*. A malformed
`AXIOM_URL` should cost the operator their dashboard, not the subnet its verification worker.

So every path out of here returns a client. What varies is whether it ships anything.

Environment:

* `AXIOM_TOKEN`    — an Axiom API token (`xaat-…`). Absent disables ingestion.
* `AXIOM_DATASET`  — the dataset events land in. Absent disables ingestion.
* `AXIOM_ENVIRON`  — a free-form deployment tag written onto every event (`prod`, `staging`, a
                     host name). Defaults to `default`, which is a legible "nobody said".
* `AXIOM_URL`      — the API base, for the EU region (`https://api.eu.axiom.co`) or a proxy.
* `AXIOM_LOG_LEVEL` — the severity floor for the stdlib-logging bridge. Read by `handler.py`.
* `AXIOM_REQUEST_EVENTS` — how much of the HTTP surface to report. Read by the API middleware.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping

from conjectures_subnet.axiom.client import (
    AXIOM_API_URL,
    AxiomClient,
    AxiomClientInterface,
    AxiomClientNoop,
)

log = logging.getLogger("conjectures_subnet.axiom")

DEFAULT_ENVIRON = "default"


def create_axiom_client_from_env(
    environ: Mapping[str, str] | None = None,
) -> AxiomClientInterface:
    """An `AxiomClient` when both credentials are present, otherwise `AxiomClientNoop`.

    Both are required together on purpose. A token with no dataset and a dataset with no token are
    each a half-finished configuration, and guessing a dataset name would send a deployment's
    events somewhere nobody chose.
    """
    env = os.environ if environ is None else environ
    token = env.get("AXIOM_TOKEN", "").strip()
    dataset = env.get("AXIOM_DATASET", "").strip()
    deployment = env.get("AXIOM_ENVIRON", "").strip() or DEFAULT_ENVIRON
    api_url = env.get("AXIOM_URL", "").strip() or AXIOM_API_URL

    if not token:
        log.debug("AXIOM_TOKEN is not set; Axiom ingestion is disabled")
        return AxiomClientNoop()
    if not dataset:
        log.debug("AXIOM_DATASET is not set; Axiom ingestion is disabled")
        return AxiomClientNoop()

    try:
        client = AxiomClient(
            dataset=dataset,
            token=token,
            environ=deployment,
            api_url=api_url,
        )
    except Exception:  # noqa: BLE001 — see the module docstring on why this is not fatal
        log.warning(
            "Axiom client could not be built; ingestion is disabled", exc_info=True
        )
        return AxiomClientNoop()
    # No token, no URL: the dataset and the deployment tag are what an operator needs to confirm
    # they are looking at the right stream.
    log.info(
        "Axiom ingestion enabled (dataset=%s environ=%s)",
        dataset,
        deployment,
    )
    return client


__all__ = ["DEFAULT_ENVIRON", "create_axiom_client_from_env"]
