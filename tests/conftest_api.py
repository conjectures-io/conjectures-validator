"""Shared construction helpers for the submission API tests.

Plain factory functions rather than fixtures, matching tests/conftest.py.

These tests need a real PostgreSQL server. The schema uses domains, native enums, JSONB, INET,
partial indexes and a plpgsql trigger, so there is no portable subset to fall back to and a
SQLite run would prove nothing about the database the service actually uses. Start the fixed
test stack and they run:

    docker compose -f docker-compose.pytest-db.yml up -d

`FC_POSTGRES_DSN` still overrides it, for pointing the suite at some other server.

The schema is built with `Base.metadata.create_all`, which is the mirror rather than the source
of truth; `scripts/check_schema_drift.py` is what proves the mirror still matches
`deploy/migrate/sql`.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, replace
from datetime import date
from functools import cache
from pathlib import Path

from conftest import PYTEST_DSN, declaration, postgres_dsn
from conftest import manifest as task_manifest
from sqlalchemy.ext.asyncio import AsyncEngine
from test_bundle import HOTKEY, TASK_DIGEST, VALID_PROOF, manifest_json, valid_bundle

from conjectures_subnet.bounty import DynamicBountyPricer, StaticBalanceReader
from conjectures_subnet.db import submissions as store
from conjectures_subnet.db.engine import async_session_factory, create_async_db_engine
from conjectures_subnet.db.models import Base
from submission_api.app import create_app
from submission_api.auth import build_authenticator, development_signature
from submission_api.dependencies import Services
from submission_api.credits import SubmissionTerms, parse_packages
from submission_api.mail import ConsoleSender
from submission_api.payments import build_payment_verifier
from submission_api.pins import PinSet
from submission_api.retired import RetiredIndex
from submission_api.settings import Settings
from submission_api.taskpool import TaskEntry, catalog_from_entries
from submission_api.rates import UnavailableTaoUsdPriceReader
from submission_api.taostats import UnavailableAlphaUsdPriceReader
from submission_api.tmc_pay import UnavailableGateway
from submission_api.verification import QueueDispatcher
from verifier.models import CatalogDeclaration, Classification
from verifier.task_pool import reward_target_identity

TASK_ID = "fixture"
TIER = "tier-1"
PROBLEM_ID = "fixture-problem"
RECIPIENT = "5C4hrfjw9DjXZTzV3MwzrrAr9P1MJhSrvWGWqi1eSuyUpnhM"
OTHER_HOTKEY = "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"
COLDKEY = "5DAAnrj7VHTznn2AWBemMuyBwZWs6FNFjdyVXUeYum3PTXFy"
REPOSITORY_COMMIT = "e923379e609b9d5987011a1d1f06ec22ea25cd20"

# What a task entry carries as its Lean challenge. Short, and structurally the real thing: the
# public detail endpoint serves these bytes verbatim.
CHALLENGE_LEAN = (
    "import FormalConjectures.TestFixtures\n"
    "import TaskSupport\n"
    "\n"
    "namespace Bounty\n"
    "\n"
    'theorem target : fcTypeOfName% "VerifierFixtures.direct" := by\n'
    "  sorry\n"
    "\n"
    "end Bounty\n"
)

# A pin lock shaped like the real one, pinning the same source revision the fixture catalog
# claims, so `assert_agrees_with_catalog` holds for tests that exercise it.
PINS_JSON = (
    '{"schema_version": 1,'
    ' "formal_conjectures": {"repository": "https://example.invalid/fc.git",'
    f' "commit": "{REPOSITORY_COMMIT}"}},'
    ' "mathlib": {"repository": "https://example.invalid/mathlib4.git",'
    ' "commit": "a3a10db0e9d66acbebf76c5e6a135066525ac900"},'
    ' "lean": {"toolchain": "leanprover/lean4:v4.27.0",'
    ' "commit": "db93fe1608548721853390a10cd40580fe7d22ae"},'
    ' "nanoda": {"repository": "https://example.invalid/nanoda.git",'
    ' "commit": "f58f2f6d535e189a40fcb02ede8eb95f97a92d37", "enabled": false},'
    ' "elan": {"version": "4.2.3", "commit": "b6cec7e10fe4965a605aaf60d1cb4a5837f0462b",'
    ' "assets": {"x86_64-unknown-linux-gnu": "df0b2b3a"}}}'
).encode("utf-8")

__all__ = [
    "CHALLENGE_LEAN",
    "COLDKEY",
    "HOTKEY",
    "OTHER_HOTKEY",
    "PINS_JSON",
    "PYTEST_DSN",
    "RECIPIENT",
    "REPOSITORY_COMMIT",
    "TASK_DIGEST",
    "TASK_ID",
    "VALID_PROOF",
    "Harness",
    "build_settings",
    "distinct_bundle",
    "harness",
    "manifest_json",
    "new_key",
    "pin_set",
    "terms",
    "postgres_dsn",
    "read_headers",
    "submission_headers",
    "task_entry",
    "valid_bundle",
]


def pin_set() -> PinSet:
    return PinSet.from_bytes(PINS_JSON)


def terms() -> SubmissionTerms:
    """The real terms document, read from the repository.

    Not a fixture string: the endpoint serves these bytes, and a test that asserted on invented
    prose would pass while the shipped document was empty or missing.
    """
    return SubmissionTerms.load(
        ROOT / "docs" / "SUBMISSION_TERMS.md",
        version="v3",
        effective_from=date(2026, 8, 7),
    )


def distinct_bundle(marker: str, *, hotkey: str = HOTKEY) -> tuple[bytes, str]:
    """A bundle whose proof bytes are unique to `marker`, and that proof's digest.

    `submissions.proof_digest` is globally UNIQUE, so two submissions can never carry the same
    proof — the second is refused as a duplicate. Any test with more than one submission in it
    therefore needs distinct proof bytes, and the bundle manifest has to agree with them.
    """
    from verifier.hashing import sha256_bytes

    proof = VALID_PROOF + f"-- {marker}\n".encode("utf-8")
    digest = sha256_bytes(proof)
    bundle = valid_bundle(
        proof=proof,
        manifest=manifest_json(
            proof_sha256=digest, proof_bytes=len(proof), miner_hotkey=hotkey
        ),
    )
    return bundle, digest


# The stack in docker-compose.pytest-db.yml, credentials and all. Duplicated there rather than
# read from a file so neither side can drift into pointing somewhere else, and separate from the
# development database so a suite that drops and recreates the schema cannot reach real data.
ROOT = Path(__file__).resolve().parents[1]

PYTEST_DSN = (
    "postgresql+psycopg://conjectures-pytest:conjectures-pytest-pw"
    "@127.0.0.1:5440/conjectures-pytest"
)


def _reachable(dsn: str) -> bool:
    """Whether a server is actually answering on `dsn`.

    Probed rather than assumed: the alternative to skipping is every database test failing with
    a connection error, which reads like a broken suite rather than a stack that is not up.
    """
    try:
        import psycopg
    except ModuleNotFoundError:  # pragma: no cover - psycopg is a test dependency
        return False
    # psycopg wants a libpq DSN; the SQLAlchemy driver suffix is not part of one.
    libpq = dsn.replace("postgresql+psycopg://", "postgresql://", 1)
    try:
        with psycopg.connect(libpq, connect_timeout=2):
            return True
    except (psycopg.Error, OSError):
        return False


@cache
def postgres_dsn() -> str | None:
    """The database tests' DSN, or None to skip them.

    Cached because this opens a connection and is called once per harness. `FC_POSTGRES_DSN`
    wins when set, so pointing the suite at another server stays possible; otherwise the fixed
    pytest stack is used if it is up, which is what makes the tests need no configuration.
    """
    explicit = os.environ.get("FC_POSTGRES_DSN", "").strip()
    if explicit:
        return explicit
    return PYTEST_DSN if _reachable(PYTEST_DSN) else None


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


def task_entry(
    *,
    task_id: str = TASK_ID,
    digest: str = TASK_DIGEST,
    tier: str = TIER,
    source: CatalogDeclaration | None = None,
    challenge_lean: str = CHALLENGE_LEAN,
    classification: Classification | None = None,
    task_mode: str | None = None,
    problem_id: str = PROBLEM_ID,
    reward_target_id: str | None = None,
    mode: str = "formalized",
    **manifest_kwargs,
) -> TaskEntry:
    """One catalog entry.

    `classification` and `task_mode` patch the *manifest*, not the source declaration. The
    public catalog facets read the manifest, because what a solver filters on is the task's
    contract rather than the upstream declaration's own label, and `conftest.manifest()` has
    neither as a parameter.

    `reward_target_id` defaults to the identity of the source theorem, exactly as
    `verifier.task_registry` requires of a real allowlist. Two entries built from one declaration
    therefore group into one conjecture and share a slug — which is what the two attack directions
    of a conjecture do — and entries built from different declarations do not.
    """
    resolved_source = source if source is not None else declaration()
    if reward_target_id is None:
        reward_target_id = reward_target_identity(resolved_source.theorem)
    manifest = task_manifest(**manifest_kwargs)
    patch = {}
    if classification is not None:
        patch["classification"] = classification
    if task_mode is not None:
        patch["task_mode"] = task_mode
    if patch:
        manifest = replace(manifest, **patch)
    return TaskEntry(
        task_id=task_id,
        tier=tier,
        problem_id=problem_id,
        reward_target_id=reward_target_id,
        mode=mode,
        task_bundle_sha256=digest,
        target_type_sha256s=("sha256:" + "11" * 32,),
        # The pool is tiered, so bytes live under the tier, not directly under the root.
        task_dir=Path("/external-task-pool") / tier / task_id,
        manifest=manifest,
        source=resolved_source,
        challenge_lean=challenge_lean,
    )


@dataclass
class Harness:
    app: object
    services: Services
    engine: AsyncEngine
    settings: Settings

    async def setup(self) -> Harness:
        async with self.engine.begin() as connection:
            # The server is reused across tests, so start from a clean schema each time.
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        return self

    async def teardown(self) -> None:
        await self.engine.dispose()

    def session(self):
        return self.services.sessions()


def harness(
    *,
    entries=None,
    dispatcher=None,
    payments=None,
    bounty_usd=None,
    retired=None,
    tmc_pay=None,
    tao_usd=None,
    **overrides: str,
) -> Harness:
    """The API under test.

    `payments` injects a payment verifier. Needed for the chain verifier, because
    `build_payment_verifier` would otherwise construct a real Subtensor reader and the test would
    reach the live network.

    `retired` injects a `RetiredIndex`. Empty unless a test asks for one, which is the point:
    every other test in the suite then proves that adding retired conjectures changed nothing
    about the live pool.

    `tmc_pay` and `tao_usd` inject the credit-purchase gateway and its rate source, for the same
    reason as `payments`: the real ones talk to a payment processor and to TaoStats. Both default
    to the unavailable implementations, so every test that does not name them proves the purchase
    endpoints refuse rather than reach a network.
    """
    settings = build_settings(**overrides)
    engine = create_async_db_engine(settings.database_url)
    catalog = catalog_from_entries(
        repository_commit=REPOSITORY_COMMIT,
        entries=entries if entries is not None else (task_entry(),),
    )
    services = Services(
        settings=settings,
        engine=engine,
        sessions=async_session_factory(engine),
        catalog=catalog,
        authenticator=build_authenticator(settings),
        payments=payments or build_payment_verifier(settings),
        dispatcher=dispatcher or QueueDispatcher(),
        pricing=DynamicBountyPricer(
            balance_reader=StaticBalanceReader(settings.bounty_pool_balance_rao),
            balance_coldkey=settings.bounty_wallet_coldkey,
            balance_hotkey=settings.bounty_wallet_hotkey,
            balance_netuid=settings.bounty_netuid,
            reward_target_ids=tuple(
                sorted({entry.reward_target_id for entry in catalog.entries.values()})
            ),
            policy_version=settings.bounty_policy_version,
            constant_numerator=settings.bounty_constant_numerator,
            constant_denominator=settings.bounty_constant_denominator,
            age_period_seconds=settings.bounty_age_period_seconds,
        ),
        pins=pin_set(),
        mail=ConsoleSender(),
        packages=parse_packages(
            settings.credit_packages, credit_price_rao=settings.payment_amount_rao
        ),
        terms=terms(),
        bounty_usd=(
            bounty_usd if bounty_usd is not None else UnavailableAlphaUsdPriceReader()
        ),
        retired=retired if retired is not None else RetiredIndex.empty(),
        tmc_pay=tmc_pay if tmc_pay is not None else UnavailableGateway(),
        tao_usd=tao_usd if tao_usd is not None else UnavailableTaoUsdPriceReader(),
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
