"""Research pages retain the reviewed sources without manufacturing task admission."""

import hashlib
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from submission_api.formulations import published_formulations
from submission_api.routers.catalog import router


def test_all_four_hilbert_formulations_have_reviewed_source_bytes():
    records = published_formulations().items
    assert [item.number for item in records] == [6, 12, 13, 16]
    assert len({item.slug for item in records}) == 4
    root = Path(__file__).resolve().parents[1]
    for item in records:
        source = root / (
            "docs/review-decisions/2026-11-12-hilbert-formulations/"
            f"draft/HilbertDrafts/Hilbert{item.number}.lean"
        )
        raw = source.read_bytes()
        for declaration in item.sources:
            assert declaration.source_sha256 == hashlib.sha256(raw).hexdigest()
            assert declaration.lean in raw.decode()
            assert declaration.declaration.rsplit(".", 1)[-1] in declaration.lean
    assert records[0].display_status == "research"
    assert records[1].display_status == "research"
    assert records[2].active_target_slug == "hilbertcatalog-hilbert13-conjecture"
    assert records[3].display_status == "proof_claimed"
    assert [item.number for item in records if item.active_target_slug] == [13]


def test_formulations_are_public_without_a_database_session_or_task_contract():
    app = FastAPI()
    app.include_router(router)
    app.state.services = SimpleNamespace(settings=SimpleNamespace(public_cache_seconds=60))
    with TestClient(app) as client:
        response = client.get("/v1/catalog/formulations")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=60"
    assert len(response.json()["items"]) == 4
    for item in response.json()["items"]:
        assert "bounty" not in item
        assert "machine_contract" not in item
