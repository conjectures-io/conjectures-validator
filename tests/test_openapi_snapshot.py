"""`docs/openapi.json` is the committed contract, and it must match the code."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="the OpenAPI document needs the service extra")

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "export_openapi.py"


def test_the_committed_openapi_document_is_current():
    spec = importlib.util.spec_from_file_location("export_openapi", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    committed = module.DOCUMENT.read_text(encoding="utf-8")
    assert committed == module.render(), (
        "docs/openapi.json is stale; run python3 scripts/export_openapi.py and commit the diff"
    )
