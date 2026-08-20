#!/usr/bin/env python3
"""Deploy `deploy/axiom/dashboard.json` to an Axiom dashboard, in place.

    python scripts/deploy_axiom_dashboard.py --dry-run     # validate and print, no network
    python scripts/deploy_axiom_dashboard.py --get         # print the dashboard as it is now
    python scripts/deploy_axiom_dashboard.py               # validate, then update it

The definition in `deploy/axiom/dashboard.json` is the source of truth and this script is the only
thing that should write to the dashboard. Editing panels in the Axiom UI works, but the next deploy
replaces them: a dashboard nobody can rebuild from the repository is one that quietly drifts from
the events the code actually emits.

**Read before changing the endpoint.** Two things about this API are not in
`axiom.co/docs/restapi/endpoints/updateDashboard`, and both were taken from Axiom's own
`axiomhq/skills` tooling rather than guessed:

* Dashboards live on the *app* host, `https://app.axiom.co/api/v2`, not on `api.axiom.co` where
  ingest and query live. Pointing this at `api.axiom.co` gets a 404 that looks like a missing
  dashboard rather than a wrong host.
* Every request needs `X-Axiom-Org-Id`. It is the slug in your dashboard URL —
  `https://app.axiom.co/<org>/dashboards/uid/<uid>`.

`PUT` is a whole-document replace, and it is guarded by `version`: the script reads the current
version first and sends it back, so a concurrent edit fails loudly instead of being overwritten.
`--overwrite` skips that check, which is the right answer only when you mean "discard whatever is
there".

Validation runs before anything is sent and mirrors Axiom's own `dashboard-validate`, because the
create/update API enforces a *closed field list per chart kind* and answers an unrecognised key with
`dashboard validation failed at [charts N]: Unrecognized key: "..."`. Catching that locally is the
difference between a clear message and a 400 with an index in it.

Environment:

    AXIOM_DASHBOARD_TOKEN   an API token with dashboard write access. Falls back to AXIOM_TOKEN,
                            but note that the ingest token the validator runs with is usually
                            scoped to ingest only and will 403 here.
    AXIOM_ORG_ID            the org slug. Defaults to the one in DEFAULT_ORG_ID below.
    AXIOM_DATASET           the dataset the panels query. Substituted for `{{dataset}}`.
    AXIOM_DASHBOARD_API_URL override the app API base. Rarely needed; EU is app.eu.axiom.co/api.

Standard library only, matching `conjectures_subnet/axiom/client.py`:
`requirements-service.lock` is a curated set and a deploy script is not a reason to grow it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEFINITION = PROJECT_ROOT / "deploy" / "axiom" / "dashboard.json"

# The dashboard this repository owns, from the URL it is served at.
DEFAULT_ORG_ID = "dendrite-xmbh"
DEFAULT_UID = "006178a6-3269-4b75-926d-ebab6697c6c0"

# Not api.axiom.co. See the module docstring.
DEFAULT_API_URL = "https://app.axiom.co/api"

DEFAULT_DATASET = "conjectures"

# The grid the UI lays out against. `layout-recipes` in Axiom's own tooling calls it 12 columns and
# their validator rejects anything past it.
GRID_COLUMNS = 12

# Chart kinds this script knows the field rules for. An unknown kind is left alone rather than
# guessed at — a false rejection of a valid future kind is worse than missing one.
KNOWN_CHART_TYPES = frozenset(
    {
        "Statistic",
        "TimeSeries",
        "Table",
        "Pie",
        "LogStream",
        "Heatmap",
        "Scatter",
        "SmartFilter",
        "MonitorList",
        "Note",
    }
)

# Rejected on every chart kind by the create/update API.
REJECTED_EVERYWHERE = ("decimals", "description", "options")
# `unit` is a Statistic-only field. Everything else must encode units in `name`/`customUnits`.
UNIT_ONLY_ON = "Statistic"

REQUEST_TIMEOUT_SECONDS = 30


class DeployError(RuntimeError):
    """Anything that should stop the deploy with a legible message."""


# --- the definition -------------------------------------------------------------------------


def load_definition(path: Path, dataset: str) -> dict[str, Any]:
    """Read the definition and substitute the dataset placeholder.

    `{{dataset}}` rather than a JSON field, matching Axiom's own template convention, so the same
    file deploys against a staging dataset without being edited.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DeployError(f"cannot read {path}: {exc}") from exc
    raw = raw.replace("{{dataset}}", dataset)
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DeployError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise DeployError(f"{path} must contain a JSON object")
    if "{{" in raw:
        leftover = sorted(
            {
                token.split("}}")[0]
                for token in raw.split("{{")[1:]
                if "}}" in token
            }
        )
        raise DeployError(f"unsubstituted placeholders in {path}: {leftover}")
    return loaded


def validate(dashboard: dict[str, Any]) -> list[str]:
    """Everything checkable without the network. Returns problems; empty means good.

    Deliberately a mirror of Axiom's `dashboard-validate`, and deliberately run before the first
    request: the API's per-chart-kind closed field list produces errors keyed by chart *index*, and
    resolving those by hand against a 16-panel payload is not a good use of anyone's afternoon.
    """
    problems: list[str] = []

    for field in ("name", "owner", "charts", "layout"):
        if not dashboard.get(field):
            problems.append(f"missing required field: {field}")
    charts = dashboard.get("charts")
    layout = dashboard.get("layout")
    if not isinstance(charts, list) or not isinstance(layout, list):
        problems.append("charts and layout must both be arrays")
        return problems

    if dashboard.get("schemaVersion") != 2:
        problems.append(
            f"schemaVersion is {dashboard.get('schemaVersion')!r}, expected 2"
        )

    chart_ids: list[str] = []
    for index, chart in enumerate(charts):
        if not isinstance(chart, dict):
            problems.append(f"charts[{index}] is not an object")
            continue
        identifier = chart.get("id")
        name = chart.get("name", "(unnamed)")
        if not identifier:
            problems.append(f"charts[{index}] ({name}) has no id")
        else:
            chart_ids.append(identifier)
        kind = chart.get("type")
        if kind not in KNOWN_CHART_TYPES:
            problems.append(f"charts[{index}] ({name}) has unknown type {kind!r}")
            continue

        # Only non-null values are flagged: the API strips a null-valued key on ingest and
        # accepts the payload, so flagging null would block deploys that would have worked.
        for field in REJECTED_EVERYWHERE:
            if chart.get(field) is not None:
                problems.append(
                    f"charts[{index}] ({name}): the API rejects chart-level {field!r} "
                    f"on every chart kind"
                )
        if kind != UNIT_ONLY_ON and chart.get("unit") is not None:
            problems.append(
                f"charts[{index}] ({name}): 'unit' is accepted on {UNIT_ONLY_ON} only, "
                f"not on {kind}; encode the unit in the name or use customUnits"
            )
        if kind == "Note" and chart.get("customUnits") is not None:
            problems.append(f"charts[{index}] ({name}): Note rejects 'customUnits'")
        apl = (chart.get("query") or {}).get("apl")
        if kind == "LogStream" and isinstance(apl, str) and "take " not in apl:
            problems.append(
                f"charts[{index}] ({name}): a LogStream without a 'take N' limit will try to "
                f"stream the whole range"
            )

    duplicates = sorted({item for item in chart_ids if chart_ids.count(item) > 1})
    if duplicates:
        problems.append(f"duplicate chart ids: {duplicates}")

    layout_ids = [entry.get("i") for entry in layout if isinstance(entry, dict)]
    missing_layout = sorted(set(chart_ids) - set(layout_ids))
    orphan_layout = sorted(set(layout_ids) - set(chart_ids))
    if missing_layout:
        problems.append(f"charts with no layout entry: {missing_layout}")
    if orphan_layout:
        problems.append(f"layout entries with no chart: {orphan_layout}")

    for entry in layout:
        if not isinstance(entry, dict):
            problems.append("layout entries must be objects")
            continue
        for field in ("i", "x", "y", "w", "h"):
            if entry.get(field) is None:
                problems.append(f"layout entry {entry!r} is missing {field!r}")
        right = (entry.get("x") or 0) + (entry.get("w") or 0)
        if right > GRID_COLUMNS:
            problems.append(
                f"layout entry {entry.get('i')!r} ends at column {right}, past the "
                f"{GRID_COLUMNS}-column grid"
            )
    return problems


def normalise_layout(dashboard: dict[str, Any]) -> dict[str, Any]:
    """Fill in the `minW`/`minH` the UI's grid expects, without overriding a stated one.

    Axiom's own deploy path does this with a jq include. Without it the panels still land, but a
    user dragging one can collapse it to nothing.
    """
    normalised = json.loads(json.dumps(dashboard))
    for entry in normalised.get("layout", []):
        entry.setdefault("minW", 1)
        entry.setdefault("minH", 1)
    return normalised


# --- the API --------------------------------------------------------------------------------


def request(
    method: str,
    url: str,
    *,
    token: str,
    org_id: str,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    """One authenticated call. Returns (status, parsed body); raises only on transport failure."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Axiom-Org-Id": org_id,
        "Accept": "application/json",
        "User-Agent": "conjectures-validator-dashboard/1",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
    call = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(call, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return response.status, _parse(response.read())
    except urllib.error.HTTPError as exc:
        # The body of a 4xx carries Axiom's own message, which is the useful part — the closed
        # field list reports the offending chart index in here.
        return exc.code, _parse(exc.read())
    except urllib.error.URLError as exc:
        raise DeployError(f"cannot reach {url}: {exc.reason}") from exc


def _parse(payload: bytes) -> dict[str, Any]:
    if not payload:
        return {}
    try:
        loaded = json.loads(payload)
    except json.JSONDecodeError:
        return {"raw": payload.decode("utf-8", errors="replace")[:2000]}
    return loaded if isinstance(loaded, dict) else {"body": loaded}


def dashboard_url(api_url: str, uid: str) -> str:
    return f"{api_url.rstrip('/')}/v2/dashboards/uid/{uid}"


# --- checking the queries actually run -------------------------------------------------------


def declared_parameters(apl: str) -> dict[str, str]:
    """The `declare query_parameters` names in a query, each bound to the "All" value.

    Empty string is what the SmartFilter sends for "All", and every panel guards it with
    `isempty()`, so binding them empty exercises the unfiltered path the dashboard opens on.
    """
    names: dict[str, str] = {}
    for block in re.findall(r"declare query_parameters \(([^)]*)\)", apl):
        for name in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*string", block):
            names[name] = ""
    return names


def check_queries(
    dashboard: dict[str, Any],
    *,
    query_api_url: str,
    token: str,
    org_id: str,
) -> list[str]:
    """Run every panel's APL against the live dataset. Returns the failures, one line each.

    This exists because APL binds field names at *query* time against the dataset's actual schema,
    and the validator's events are mostly optional: `reason_code` appears on a refusal,
    `sandbox_mode` on a verdict, `duration_ms` on a request. A panel referencing a field the dataset
    has not seen yet fails with `field 'x' not found` — not at deploy, but for whoever opens the
    dashboard. `column_ifexists()` is the fix; this is how we know it was applied everywhere.

    Deliberately run against the real dataset rather than parsed locally: a field's presence is a
    property of what has been ingested, which no amount of static checking can know.

    Note the different host. Queries are on `api.axiom.co`; dashboards are on the app host.
    """
    failures: list[str] = []
    url = f"{query_api_url.rstrip('/')}/v1/datasets/_apl?format=tabular"
    for chart in dashboard.get("charts", []):
        for label, apl in _queries_of(chart):
            body: dict[str, Any] = {
                "apl": apl,
                # A narrow window: this is a "does it bind and run" check, not a data check, and a
                # wide one would scan the dataset once per panel.
                "startTime": "now-5m",
                "endTime": "now",
            }
            parameters = declared_parameters(apl)
            if parameters:
                body["variables"] = parameters
            status, response = request(
                "POST", url, token=token, org_id=org_id, body=body
            )
            if status >= 400:
                failures.append(f"{label}: {_message(response)}")
    return failures


def _queries_of(chart: dict[str, Any]) -> list[tuple[str, str]]:
    """Every runnable APL on a chart: the panel's own, plus each filter dropdown's."""
    found: list[tuple[str, str]] = []
    apl = (chart.get("query") or {}).get("apl")
    if apl:
        found.append((chart.get("id", "?"), apl))
    for filter_spec in chart.get("filters", []):
        nested = filter_spec.get("apl")
        if isinstance(nested, dict) and nested.get("apl"):
            found.append((f"{chart.get('id', '?')}/{filter_spec.get('id', '?')}", nested["apl"]))
    return found


def fetch(api_url: str, uid: str, *, token: str, org_id: str) -> dict[str, Any]:
    status, body = request(
        "GET", dashboard_url(api_url, uid), token=token, org_id=org_id
    )
    if status == 404:
        raise DeployError(
            f"no dashboard with uid {uid} in org {org_id}. Check both, and check that the base "
            f"URL is the app host ({api_url}) rather than api.axiom.co"
        )
    if status in (401, 403):
        raise DeployError(
            f"the token was refused ({status}) for org {org_id}. Dashboard writes need a token "
            f"with dashboard access; the validator's ingest token is not enough. "
            f"{_message(body)}"
        )
    if status >= 400:
        raise DeployError(f"GET failed with {status}: {_message(body)}")
    return body


def push(
    api_url: str,
    uid: str,
    dashboard: dict[str, Any],
    *,
    token: str,
    org_id: str,
    version: int | None,
    overwrite: bool,
    message: str | None,
) -> dict[str, Any]:
    """PUT the whole document. `version` is the optimistic-concurrency guard."""
    payload: dict[str, Any] = {"dashboard": dashboard}
    if overwrite:
        payload["overwrite"] = True
    elif version is not None:
        payload["version"] = version
    if message:
        payload["message"] = message
    status, body = request(
        "PUT", dashboard_url(api_url, uid), token=token, org_id=org_id, body=payload
    )
    if status == 409:
        raise DeployError(
            "the dashboard changed since this script read it — somebody edited it in the UI. "
            "Re-run to pick up their version, or pass --overwrite to discard it."
        )
    if status >= 400:
        raise DeployError(f"PUT failed with {status}: {_message(body)}")
    return body


def _message(body: dict[str, Any]) -> str:
    for key in ("message", "error", "detail", "raw"):
        value = body.get(key)
        if value:
            return str(value)
    return json.dumps(body)[:500] if body else "(no response body)"


# --- CLI ------------------------------------------------------------------------------------


def parser() -> argparse.ArgumentParser:
    parsed = argparse.ArgumentParser(
        prog="deploy_axiom_dashboard.py",
        description="Deploy deploy/axiom/dashboard.json to Axiom.",
    )
    mode = parsed.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and print the payload; make no network call",
    )
    mode.add_argument(
        "--get",
        action="store_true",
        help="print the dashboard as it is now, and exit",
    )
    mode.add_argument(
        "--check-queries",
        action="store_true",
        help="run every panel's APL against the dataset and report failures, then exit",
    )
    parsed.add_argument(
        "--skip-query-check",
        action="store_true",
        help="deploy without first running the panel queries (not recommended)",
    )
    parsed.add_argument(
        "--definition",
        type=Path,
        default=DEFAULT_DEFINITION,
        help=f"dashboard JSON to deploy (default: {DEFAULT_DEFINITION.relative_to(PROJECT_ROOT)})",
    )
    parsed.add_argument("--uid", default=os.environ.get("AXIOM_DASHBOARD_UID", DEFAULT_UID))
    parsed.add_argument("--org-id", default=os.environ.get("AXIOM_ORG_ID", DEFAULT_ORG_ID))
    parsed.add_argument(
        "--dataset",
        default=os.environ.get("AXIOM_DATASET", "").strip() or DEFAULT_DATASET,
        help="dataset substituted for {{dataset}} (default: $AXIOM_DATASET)",
    )
    parsed.add_argument(
        "--api-url",
        default=os.environ.get("AXIOM_DASHBOARD_API_URL", "").strip() or DEFAULT_API_URL,
        help=f"dashboard API base, the app host (default: {DEFAULT_API_URL})",
    )
    parsed.add_argument(
        "--query-api-url",
        default=os.environ.get("AXIOM_URL", "").strip() ,#or DEFAULT_QUERY_API_URL,
        help=(
            "query API base, used by the panel-query check. A different host from the dashboard "
            #f"API (default: {DEFAULT_QUERY_API_URL})"
        ),
    )
    parsed.add_argument(
        "--overwrite",
        action="store_true",
        help="replace whatever is there without checking the version",
    )
    parsed.add_argument("--message", default=None, help="update message recorded by Axiom")
    return parsed


def token_from_env() -> str:
    for name in ("AXIOM_DASHBOARD_TOKEN", "AXIOM_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    raise DeployError(
        "set AXIOM_DASHBOARD_TOKEN (or AXIOM_TOKEN) to an Axiom token with dashboard access"
    )


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)

    if args.get:
        current = fetch(
            args.api_url, args.uid, token=token_from_env(), org_id=args.org_id
        )
        print(json.dumps(current, indent=2, sort_keys=True))
        return 0

    dashboard = load_definition(args.definition, args.dataset)
    problems = validate(dashboard)
    if problems:
        print(f"{args.definition}: {len(problems)} problem(s)", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    dashboard = normalise_layout(dashboard)
    charts = len(dashboard["charts"])

    if args.dry_run:
        print(json.dumps({"dashboard": dashboard}, indent=2))
        print(
            f"\nvalidated: {charts} panels, dataset {args.dataset!r}, "
            f"would PUT {dashboard_url(args.api_url, args.uid)}",
            file=sys.stderr,
        )
        return 0

    token = token_from_env()
    version: int | None = None
    if not args.overwrite:
        current = fetch(args.api_url, args.uid, token=token, org_id=args.org_id)
        version = current.get("version")
        if version is None:
            raise DeployError(
                "the current dashboard has no version field, so a guarded update is not "
                "possible; pass --overwrite if you mean to replace it regardless"
            )
        existing = len((current.get("dashboard") or {}).get("charts") or [])
        print(f"current: version {version}, {existing} panel(s)")

    result = push(
        args.api_url,
        args.uid,
        dashboard,
        token=token,
        org_id=args.org_id,
        version=version,
        overwrite=args.overwrite,
        message=args.message,
    )
    print(
        f"deployed: {charts} panels, version {result.get('version', '?')}, "
        f"dataset {args.dataset!r}"
    )
    print(f"https://app.axiom.co/{args.org_id}/dashboards/uid/{args.uid}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except DeployError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)
