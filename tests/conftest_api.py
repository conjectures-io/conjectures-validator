"""Shared construction helpers for the submission API tests.

Plain factory functions rather than fixtures, matching tests/conftest.py.

These tests need a real PostgreSQL server. The schema uses domains, native enums, JSONB, INET,
partial indexes and a plpgsql trigger, so there is no portable subset to fall back to and a
SQLite run would prove nothing about the database the service actually uses. Set
`FC_POSTGRES_DSN` to enable them:

    FC_POSTGRES_DSN=postgresql+psycopg://conjectures:pw@127.0.0.1:55432/conjectures pytest

The schema is built with `Base.metadata.create_all`, which is the mirror rather than the source
of truth; `scripts/check_schema_drift.py` is what proves the mirror still matches
`deploy/migrate/sql`.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine

from conftest import manifest as task_manifest
from conjectures_subnet.db import submissions as store
from conjectures_subnet.db.engine import async_session_factory, create_async_db_engine
from conjectures_subnet.db.models import Base
from submission_api.app import create_app
from submission_api.auth import build_authenticator, development_signature
from submission_api.dependencies import Services
from submission_api.payments import build_payment_verifier
from submission_api.settings import Settings
from submission_api.taskpool import TaskEntry, catalog_from_entries
from submission_api.verification import QueueDispatcher
from test_bundle import HOTKEY, TASK_DIGEST, VALID_PROOF, manifest_json, valid_bundle


TASK_ID = "fixture"
RECIPIENT = "5C4hrfjw9DjXZTzV3MwzrrAr9P1MJhSrvWGWqi1eSuyUpnhM"
OTHER_HOTKEY = "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"
COLDKEY = "5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy"

__all__ = [
    "COLDKEY",
    "HOTKEY",
    "OTHER_HOTKEY",
    "RECIPIENT",
    "TASK_DIGEST",
    "TASK_ID",
    "VALID_PROOF",
    "Harness",
    "build_settings",
    "harness",
    "manifest_json",
    "new_key",
    "read_headers",
    "postgres_dsn",
    "submission_headers",
    "valid_bundle",
]


def postgres_dsn() -> str | None:
    return os.environ.get("FC_POSTGRES_DSN", "").strip() or None


def new_key() -> str:
    """A fresh idempotency key. The schema requires a UUID."""
    return str(uuid.uuid4())


def build_settings(**overrides: str) -> Settings:
    environ = {
        "APP_MODE": "DEV",
        "DATABASE_URL": postgres_dsn() or "",
        "PAYMENT_RECIPIENT_SS58": RECIPIENT,
        "SUBMISSION_AUTHENTICATOR": "development-static-key",
        "SUBMISSION_PAYMENT_VERIFIER": "development",
        "SUBMISSION_DISPATCHER": "queue",
        "DEVELOPMENT_HOTKEYS": f"{HOTKEY},{OTHER_HOTKEY}",
        "DEVELOPMENT_COLDKEY": COLDKEY,
        "MANUAL_REWARD_REVIEW_ENABLED": "true",
    }
    environ.update(overrides)
    return Settings.from_env(environ)


def task_entry(*, task_id: str = TASK_ID, digest: str = TASK_DIGEST, **manifest_kwargs) -> TaskEntry:
    task = task_manifest(**manifest_kwargs)
    return TaskEntry(
        task_id=task_id,
        problem_id="fc-test-fixture-problem",
        mode="formalized" if task.task_mode == "positive" else task.task_mode,
        tier="tier-1",
        source_theorems=(task.source_theorem,),
        task_bundle_sha256=digest,
        target_type_sha256s=("sha256:" + "11" * 32,),
        task_dir=Path("tasks/pool/tier-1") / task_id,
        manifest=task,
    )


@dataclass
class Harness:
    app: object
    services: Services
    engine: AsyncEngine
    settings: Settings

    async def setup(self) -> "Harness":
        async with self.engine.begin() as connection:
            # The server is reused across tests, so start from a clean schema each time.
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        return self

    async def teardown(self) -> None:
        await self.engine.dispose()

    def session(self):
        return self.services.sessions()


def harness(*, entries=None, dispatcher=None, **overrides: str) -> Harness:
    settings = build_settings(**overrides)
    engine = create_async_db_engine(settings.database_url)
    catalog = catalog_from_entries(
        repository_commit="e923379e609b9d5987011a1d1f06ec22ea25cd20",
        entries=entries if entries is not None else (task_entry(),),
    )
    services = Services(
        settings=settings,
        engine=engine,
        sessions=async_session_factory(engine),
        catalog=catalog,
        authenticator=build_authenticator(settings),
        payments=build_payment_verifier(settings),
        dispatcher=dispatcher or QueueDispatcher(),
    )
    return Harness(
        app=create_app(services=services), services=services, engine=engine, settings=settings
    )


def request_digest(
    *,
    hotkey: str = HOTKEY,
    task_id: str = TASK_ID,
    task_digest: str = TASK_DIGEST,
    proof_digest: str,
    payment_reference: str,
    idempotency_key: str,
) -> str:
    return store.canonical_request_digest(
        hotkey=hotkey,
        task_id=task_id,
        task_bundle_sha256=task_digest,
        proof_sha256=proof_digest,
        payment_reference=payment_reference,
        idempotency_key=idempotency_key,
    )


def submission_headers(
    bundle: bytes,
    *,
    hotkey: str = HOTKEY,
    task_id: str = TASK_ID,
    task_digest: str = TASK_DIGEST,
    idempotency_key: str | None = None,
    payment_reference: str = "0xpayment-0001",
    timestamp_ms: int | None = None,
    signature: str | None = None,
    proof_digest: str | None = None,
    content_type: str = "application/zip",
) -> dict[str, str]:
    from verifier.hashing import sha256_bytes

    del bundle  # the proof digest, not the archive digest, is what the request binds
    key = idempotency_key if idempotency_key is not None else new_key()
    proof = proof_digest or sha256_bytes(VALID_PROOF)
    return {
        "Content-Type": content_type,
        "Idempotency-Key": key,
        "X-Conjectures-Hotkey": hotkey,
        "X-Conjectures-Timestamp": str(
            int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
        ),
        "X-Conjectures-Signature": signature or development_signature(),
        "X-Conjectures-Task-Id": task_id,
        "X-Conjectures-Task-Sha256": task_digest,
        "X-Conjectures-Proof-Sha256": proof,
        "X-Conjectures-Payment-Ref": payment_reference,
    }


def read_headers(hotkey: str = HOTKEY, *, signature: str | None = None) -> dict[str, str]:
    return {
        "X-Conjectures-Hotkey": hotkey,
        "X-Conjectures-Timestamp": str(int(time.time() * 1000)),
        "X-Conjectures-Signature": signature or development_signature(),
    }
