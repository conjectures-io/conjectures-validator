"""Paid work is claimed only by its original verification environment, against a real database.

Every row here is synthetic. The registry and bundles are built in a temporary tasks checkout by
the same publication code a release uses; the worker under test is the production
`VerificationWorker` with a fake container runner. The properties:

* a worker for any other environment neither claims a retry-eligible historical row nor counts
  an attempt against it;
* the original environment's worker claims it and resolves exactly the original version,
  bundle and build provenance;
* the attempt cap still parks a row for every worker, including the original one;
* this holds across same-commit toolchain changes, admission-only publications, import-only
  changes that keep task IDs, and legacy versions bound to their own source commit.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from conftest import DATABASE_SKIP_REASON, postgres_dsn
from sqlalchemy import text

from version_fixtures import (
    COMMIT_A,
    COMMIT_B,
    MATHLIB_COMMIT,
    CountingValidator,
    declaration,
    release,
    standard,
)
from conjectures_subnet.db import submissions as store
from conjectures_subnet.db import verification as queue
from conjectures_subnet.db.engine import async_session_factory, create_async_db_engine
from conjectures_subnet.db.models import Base, Submission, VerificationState
from verification_worker.runner import VerifierRun
from verification_worker.settings import WorkerSettings
from verification_worker.tasks import (
    ResolvedTask,
    TaskNotAllowed,
    VersionedTaskResolver,
    resolver_from_tasks,
)
from verification_worker.worker import Outcome, VerificationWorker
from verifier.hashing import canonical_json_bytes, sha256_bytes
from verifier.models import Catalog
from verifier.task_generator import generate_task, problem_id
from verifier.task_store import TaskVersionStore
from verifier.task_versions import LEGACY_PROVENANCE
from verifier.version_registry import REGISTRY_NAME, Instance, RegistryBuilder, VersionRegistry

pytestmark = pytest.mark.skipif(postgres_dsn() is None, reason=DATABASE_SKIP_REASON)

MINER = "5FHneW46xGXgs5mUiveU4sbTyGBzmstUspZC92UhjJM694ty"
PAIR = "FormalConjectures.Problems.Pair"
SOLO = "FormalConjectures.Problems.Solo"
CAP = 3
LEGACY_COMMIT = "8432eac9" + "0" * 32


def run(coroutine):
    return asyncio.run(coroutine)


@dataclass
class FakeRunner:
    calls: list[dict] = field(default_factory=list)

    async def run(self, *, task_dir, proof, expected_task_sha256, timeout_seconds, expected_build_provenance_sha256=None):
        self.calls.append(
            {"task_dir": task_dir, "digest": expected_task_sha256, "provenance": expected_build_provenance_sha256}
        )
        payload = {"accepted": False, "reason_code": "SOLUTION_BUILD_FAILED", "stage": "BUILD_SOLUTION",
                   "checks": {}, "sandbox_mode": "landrun+seccomp"}
        return VerifierRun(payload, canonical_json_bytes(payload), "sha256:" + "cd" * 32, "test")


@dataclass
class Database:
    engine: object

    @classmethod
    async def setup(cls) -> "Database":
        engine = create_async_db_engine(postgres_dsn())
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
            await connection.run_sync(Base.metadata.create_all)
        return cls(engine)

    async def teardown(self) -> None:
        await self.engine.dispose()

    @property
    def sessions(self):
        return async_session_factory(self.engine)

    async def pending(self, *, task_id: str, digest: str, problem: str, theorem: str, attempts: int = 0) -> uuid.UUID:
        """A synthetic paid, unverified submission, optionally with attempts already spent."""
        content = f"theorem t : True := trivial -- {uuid.uuid4()}".encode()
        async with self.sessions() as session:
            view = await store.create_submission(
                session,
                store.NewSubmission(
                    signer_coldkey=MINER,
                    idempotency_key=uuid.uuid4(),
                    request_digest=sha256_bytes(content),
                    task_id=task_id,
                    task_bundle_sha256=digest,
                    problem_id=problem,
                    reward_target_id=f"fc-target:{theorem}",
                    task_mode=store.TaskMode.FORMALIZED if task_id.endswith(("formalized", "formalized-v1")) else store.TaskMode.COUNTEREXAMPLE,
                    proof_content=content,
                    proof_sha256=sha256_bytes(content),
                    payment_reference=f"ref-{uuid.uuid4()}",
                    payment_sender=MINER,
                    payment_amount_rao=500_000_000,
                    payment_block=1,
                    signer_signature=b"\x11" * 64,
                    manual_review_required=True,
                    review_policy_version="v1",
                    bounty_amount_rao=1_000_000_000,
                    bounty_policy_version="flat-v1",
                ),
            )
            await session.execute(
                text("UPDATE submissions SET verification_attempts = :attempts WHERE id = :id"),
                {"attempts": attempts, "id": view.submission.id},
            )
            await session.commit()
            return view.submission.id

    async def row(self, submission_id: uuid.UUID) -> Submission:
        async with self.sessions() as session:
            row = await session.get(Submission, submission_id)
            assert row is not None
            return row


def settings() -> WorkerSettings:
    return WorkerSettings.from_env({"VERIFICATION_MAX_ATTEMPTS": str(CAP)})


def decls():
    return [declaration("Pair.first", PAIR), declaration("Pair.second", PAIR), declaration("Solo.third", SOLO)]


@dataclass
class Tasks:
    """A temporary tasks checkout: `versions/` store plus `task-versions.json`."""

    root: Path
    env: object
    registry: VersionRegistry = field(default_factory=VersionRegistry.empty)
    indexes: list = field(default_factory=list)

    @property
    def store(self) -> TaskVersionStore:
        (self.root / "versions").mkdir(parents=True, exist_ok=True)
        return TaskVersionStore(self.root / "versions")

    def publish(self, selection, **options):
        result, validator, index = release(self.env, selection, store=self.store, previous=self.registry, **options)
        self.registry = result.registry
        self.indexes.append(index)
        (self.root / REGISTRY_NAME).write_bytes(self.registry.to_bytes())
        return result, index

    def resolver(self, instance: Instance) -> VersionedTaskResolver:
        return VersionedTaskResolver.load(tasks_root=self.root, environment=instance)

    def key(self, theorem: str, mode: str, instance: Instance):
        for record in self.registry.versions.values():
            if record.theorems == (theorem,) and record.mode == mode and record.admission_at(instance):
                return record.task_id, record.task_bundle_sha256, record.admission_at(instance).problem_id
        raise KeyError((theorem, mode, instance))


@pytest.fixture
def tasks(tmp_path) -> Tasks:
    return Tasks(tmp_path / "tasks", standard(tmp_path / "env"))


def worker(database: Database, resolver, runner: FakeRunner) -> VerificationWorker:
    return VerificationWorker(settings=settings(), sessions=database.sessions, runner=runner, tasks=resolver)


def test_an_import_only_release_keeps_ids_but_old_paid_work_stays_with_its_environment(tasks):
    _, first_index = tasks.publish(decls())
    old = Instance(COMMIT_A, first_index.environment.sha256)
    task_id, digest, old_problem = tasks.key("Pair.first", "formalized", old)
    tasks.env.write_module("FormalConjecturesUtil", "-- shared utilities reformatted\n")
    second, second_index = tasks.publish(decls(), commit=COMMIT_B)
    assert second.counts["built"] == 0
    new = Instance(COMMIT_B, second_index.environment.sha256)
    assert tasks.key("Pair.first", "formalized", new)[0] == task_id  # same version, same bundle

    async def scenario():
        database = await Database.setup()
        try:
            historical = await database.pending(
                task_id=task_id, digest=digest, problem=old_problem, theorem="Pair.first", attempts=1
            )
            new_runner = FakeRunner()
            assert await worker(database, tasks.resolver(new), new_runner).process_one() is None
            row = await database.row(historical)
            assert row.verification_attempts == 1 and row.verification_lease_until is None
            assert new_runner.calls == []

            old_runner = FakeRunner()
            processed = await worker(database, tasks.resolver(old), old_runner).process_one()
            assert processed is not None and processed.submission_id == historical
            assert processed.attempts == 2
            assert old_runner.calls == [
                {
                    "task_dir": tasks.root / "versions" / task_id,
                    "digest": digest,
                    "provenance": first_index.provenance["Pair.first"].sha256,
                }
            ]
        finally:
            await database.teardown()

    run(scenario())


def test_the_attempt_cap_parks_historical_work_for_every_environment(tasks):
    _, first_index = tasks.publish(decls())
    old = Instance(COMMIT_A, first_index.environment.sha256)
    task_id, digest, problem = tasks.key("Solo.third", "counterexample", old)
    _, second_index = tasks.publish(decls(), commit=COMMIT_B)
    new = Instance(COMMIT_B, second_index.environment.sha256)

    async def scenario():
        database = await Database.setup()
        try:
            parked = await database.pending(task_id=task_id, digest=digest, problem=problem, theorem="Solo.third", attempts=CAP)
            for instance in (new, old):
                runner = FakeRunner()
                assert await worker(database, tasks.resolver(instance), runner).process_one() is None
                assert runner.calls == []
            assert (await database.row(parked)).verification_attempts == CAP
        finally:
            await database.teardown()

    run(scenario())


def test_a_toolchain_only_release_at_the_same_commit_partitions_paid_work(tasks):
    _, first_index = tasks.publish(decls())
    old = Instance(COMMIT_A, first_index.environment.sha256)
    old_task, old_digest, old_problem = tasks.key("Pair.second", "formalized", old)
    newer = tasks.env.environment(lean_toolchain="leanprover/lean4:v4.36.0")
    second, second_index = tasks.publish(decls(), commit=COMMIT_A, environment=newer)
    assert second.counts["built"] == 6
    new = Instance(COMMIT_A, second_index.environment.sha256)
    new_task, new_digest, new_problem = tasks.key("Pair.second", "formalized", new)
    assert new_problem == old_problem and new_task != old_task

    async def scenario():
        database = await Database.setup()
        try:
            historical = await database.pending(task_id=old_task, digest=old_digest, problem=old_problem, theorem="Pair.second", attempts=1)
            current = await database.pending(task_id=new_task, digest=new_digest, problem=new_problem, theorem="Pair.second")
            new_worker = worker(database, tasks.resolver(new), FakeRunner())
            processed = await new_worker.process_one()
            assert processed is not None and processed.submission_id == current
            assert await new_worker.process_one() is None
            assert (await database.row(historical)).verification_attempts == 1
            processed = await worker(database, tasks.resolver(old), FakeRunner()).process_one()
            assert processed is not None and processed.submission_id == historical
        finally:
            await database.teardown()

    run(scenario())


def test_an_admission_only_retirement_keeps_pending_work_with_the_same_instance(tasks):
    _, first_index = tasks.publish(decls())
    instance = Instance(COMMIT_A, first_index.environment.sha256)
    task_id, digest, problem = tasks.key("Solo.third", "formalized", instance)
    retired, _ = tasks.publish(decls()[:2], exit_states={"Solo.third": "retired"})
    assert retired.counts["built"] == 0 and tasks.registry.versions[task_id].state == "retired"

    async def scenario():
        database = await Database.setup()
        try:
            pending = await database.pending(task_id=task_id, digest=digest, problem=problem, theorem="Solo.third", attempts=2)
            runner = FakeRunner()
            processed = await worker(database, tasks.resolver(instance), runner).process_one()
            assert processed is not None and processed.submission_id == pending
            assert runner.calls[0]["task_dir"] == tasks.root / "versions" / task_id
        finally:
            await database.teardown()

    run(scenario())


def legacy_version(tasks: Tasks, commit: str):
    """A legacy (v1) bundle of `commit` under history/legacy, registered at its own instance."""
    decl = declaration("Solo.third", SOLO)
    catalog = Catalog(1, commit, tasks.env.toolchain, MATHLIB_COMMIT, "test", 0, (decl,))
    from verifier.task_generator import task_id as legacy_task_id

    identifier = legacy_task_id(commit, decl.theorem, "formalized", 1)
    output = tasks.root / "history" / "legacy" / identifier
    output.parent.mkdir(parents=True, exist_ok=True)
    generate_task(catalog=catalog, declaration=decl, mode="formalized", output=output, validate_target=CountingValidator())
    from verifier.task_loader import load_task_bundle

    bundle = load_task_bundle(output)
    return identifier, bundle


def test_a_legacy_historical_row_routes_only_to_its_own_source_commit(tasks):
    legacy_id, bundle = legacy_version(tasks, LEGACY_COMMIT)
    legacy = Instance(LEGACY_COMMIT, None)
    builder = RegistryBuilder(previous=VersionRegistry.empty(), instance=legacy, environment=None)
    builder.admit(
        task_id=legacy_id, task_bundle_sha256=bundle.sha256, provenance=LEGACY_PROVENANCE, theorem="Solo.third",
        mode="formalized", tier="tier-1", location=f"history/legacy/{legacy_id}", state="retired",
    )
    tasks.registry = builder.build(allowlist_sha256="sha256:" + "4" * 64)
    _, current_index = tasks.publish(decls())
    current = Instance(COMMIT_A, current_index.environment.sha256)
    legacy_problem = problem_id(LEGACY_COMMIT, ("Solo.third",))

    async def scenario():
        database = await Database.setup()
        try:
            historical = await database.pending(task_id=legacy_id, digest=bundle.sha256, problem=legacy_problem, theorem="Solo.third", attempts=1)
            runner = FakeRunner()
            assert await worker(database, tasks.resolver(current), runner).process_one() is None
            assert runner.calls == [] and (await database.row(historical)).verification_attempts == 1
            # Asked directly, the current environment's resolver fails closed.
            with pytest.raises(TaskNotAllowed):
                tasks.resolver(current).resolve(task_id=legacy_id, task_bundle_sha256=bundle.sha256, problem_id=legacy_problem)
            legacy_runner = FakeRunner()
            processed = await worker(database, tasks.resolver(legacy), legacy_runner).process_one()
            assert processed is not None and processed.submission_id == historical
            assert legacy_runner.calls[0]["task_dir"] == tasks.root / "history" / "legacy" / legacy_id
            assert legacy_runner.calls[0]["provenance"] is None
        finally:
            await database.teardown()

    run(scenario())


def test_the_claim_matches_task_digest_and_problem_exactly(tasks):
    _, first_index = tasks.publish(decls())
    instance = Instance(COMMIT_A, first_index.environment.sha256)
    task_id, digest, problem = tasks.key("Pair.first", "counterexample", instance)

    async def scenario():
        database = await Database.setup()
        try:
            wrong_digest = await database.pending(task_id=task_id, digest="sha256:" + "ee" * 32, problem=problem, theorem="Pair.first")
            wrong_problem = await database.pending(task_id=task_id, digest=digest, problem=problem_id(COMMIT_B, ("Pair.first",)), theorem="Pair.first")
            wrong_task = await database.pending(task_id="fc-v2-pair-first-" + "0" * 24 + "-counterexample", digest=digest, problem=problem, theorem="Pair.first")
            unit = worker(database, tasks.resolver(instance), FakeRunner())
            assert await unit.process_one() is None
            for submission_id in (wrong_digest, wrong_problem, wrong_task):
                assert (await database.row(submission_id)).verification_attempts == 0
            async with database.sessions() as session:
                assert await queue.unserved_pending(session, served=unit.served) == 3
        finally:
            await database.teardown()

    run(scenario())


def test_a_claim_the_resolver_refuses_is_undone_with_its_attempt_refunded(tasks):
    _, first_index = tasks.publish(decls())
    instance = Instance(COMMIT_A, first_index.environment.sha256)
    task_id, digest, problem = tasks.key("Pair.first", "formalized", instance)

    async def scenario():
        database = await Database.setup()
        try:
            pending = await database.pending(task_id=task_id, digest=digest, problem=problem, theorem="Pair.first", attempts=1)
            runner = FakeRunner()
            unit = worker(database, tasks.resolver(instance), runner)
            # The pool on disk changed after startup: claimable by the served set, refused by
            # the resolver. Never verified, never charged, never claimed again by this process.
            unit.tasks = resolver_from_tasks(
                repository_commit=COMMIT_A,
                tasks=(ResolvedTask(task_id=task_id, tier="tier-1", task_dir=Path("/nowhere"),
                                    task_bundle_sha256="sha256:" + "ab" * 32, timeout_seconds=30, problem_id=problem),),
            )
            processed = await unit.process_one()
            assert processed is not None and processed.outcome is Outcome.OPERATOR
            row = await database.row(pending)
            assert row.verification_attempts == 1 and row.verification_lease_until is None
            assert row.verification_status == VerificationState.UNVERIFIED
            assert runner.calls == []
            assert await unit.process_one() is None
            assert (await database.row(pending)).verification_attempts == 1
        finally:
            await database.teardown()

    run(scenario())


def test_an_empty_served_set_claims_nothing(tasks):
    async def scenario():
        database = await Database.setup()
        try:
            await database.pending(task_id="fc-v2-x-" + "0" * 24 + "-formalized", digest="sha256:" + "ab" * 32, problem="p", theorem="X.y")
            async with database.sessions() as session:
                assert await queue.claim_next(session, owner="w", lease_seconds=60, max_attempts=3, served=()) is None
        finally:
            await database.teardown()

    run(scenario())


def test_an_environment_the_registry_never_published_refuses_to_start(tasks):
    _, first_index = tasks.publish(decls())
    with pytest.raises(TaskNotAllowed, match="not published"):
        tasks.resolver(Instance(COMMIT_B, first_index.environment.sha256))
