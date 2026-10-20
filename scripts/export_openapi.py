#!/usr/bin/env python3
"""Write the API's OpenAPI document to `docs/openapi.json`, or check that it is current.

    python3 scripts/export_openapi.py           # rewrite docs/openapi.json
    python3 scripts/export_openapi.py --check   # exit 1 if docs/openapi.json is stale

Production serves no `/openapi.json` (`APP_MODE=PROD` turns the docs routes off), so a client
cannot diff the contract it is about to meet. This file is that contract, committed beside the code
that produces it: a change to a public response shape shows up in the same pull request as a diff
to it, and `tests/test_openapi_snapshot.py` fails when it is stale.

Built from the application object alone. Nothing is read from the environment and no database or
network is reached, so the output depends only on the code. `info.version` is left out: it moves on
every release without the contract moving, and the diff should be the contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DOCUMENT = ROOT / "docs" / "openapi.json"

# The smallest development configuration `Settings` accepts. None of it shapes the document; it
# only has to validate.
ENVIRON = {
    "APP_MODE": "DEV",
    "DATABASE_URL": "",
    "PAYMENT_RECIPIENT_SS58": "5C4hrfjw9DjXZTzV3MwzrrAr9P1MJhSrvWGWqi1eSuyUpnhM",
    "SUBMISSION_AUTHENTICATOR": "development-static-key",
    "SUBMISSION_PAYMENT_VERIFIER": "development",
    "SUBMISSION_DISPATCHER": "queue",
    "DEVELOPMENT_COLDKEYS": "5C4hrfjw9DjXZTzV3MwzrrAr9P1MJhSrvWGWqi1eSuyUpnhM",
    "CONTRIBUTIONS_ENABLED": "false",
}


def render() -> str:
    from submission_api.app import create_app
    from submission_api.settings import Settings

    document = create_app(Settings.from_env(ENVIRON)).openapi()
    document.get("info", {}).pop("version", None)
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the file is stale")
    args = parser.parse_args(argv)
    rendered = render()
    if args.check:
        current = DOCUMENT.read_text(encoding="utf-8") if DOCUMENT.exists() else ""
        if current != rendered:
            print(
                f"{DOCUMENT.relative_to(ROOT)} is stale; run python3 scripts/export_openapi.py",
                file=sys.stderr,
            )
            return 1
        return 0
    DOCUMENT.write_text(rendered, encoding="utf-8")
    print(f"wrote {DOCUMENT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
