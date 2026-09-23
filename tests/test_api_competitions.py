"""The competition surface: one router, one engine per competition, an adapter each.

Most of these drive the real miniz_oxide adapter against a throwaway database holding the slice
of that competition's schema the adapter maps. A second, in-memory adapter (`Toy`) shares the
router with it, which is the property the design exists for: a competition with different
files, different metrics and none of the optional capabilities is served without any change to
the router, the schemas or the proofs database.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("fastapi", reason="submission API tests need the service extra")
pytest.importorskip("sqlalchemy", reason="submission API tests need the db extra")
pytest.importorskip("httpx", reason="submission API tests need the service extra")
pytest.importorskip("psycopg", reason="submission API tests need the db extra")

from conftest import COMPETITION_SKIP_REASON, competition_dsn
from conftest_api import OTHER_MINER_COLDKEY, build_settings, harness, postgres_dsn
from test_api_accounts import client, grant_role, same_origin, sign_in_by_email
from test_api_auth import MINER_COLDKEY, production_env

from conjectures_subnet.db import async_session_factory, create_async_db_engine
from conjectures_subnet.db.models import ADMIN_ROLE
from submission_api.competitions import (
    Competition,
    CompetitionConfig,
    CompetitionConfigError,
    CompetitionRegistry,
    build_registry,
    database_url_variable,
)
from submission_api.competitions import base
from submission_api.competitions.catalog import ADAPTERS
from submission_api.competitions.miniz_oxide import MinizOxide
from submission_api.competitions.miniz_oxide import tables as t
from submission_api.competitions.signature import submit_message
from submission_api.settings import Settings, SettingsError

pytestmark = pytest.mark.skipif(postgres_dsn() is None, reason="no database")
needs_competition_db = pytest.mark.skipif(competition_dsn() is None, reason=COMPETITION_SKIP_REASON)

SLUG = "miniz-oxide"
RUST = b"fn parse(input: &[u8]) {}"
LEAN = b"theorem holds : True := trivial"


def run(coro):
    return asyncio.run(coro)


# ── configuration ─────────────────────────────────────────────────────────────────────────


def test_no_competitions_are_served_unless_listed():
    assert build_settings().competitions == ()


def test_each_listed_competition_needs_its_own_database_url():
    with pytest.raises(SettingsError, match="COMPETITION_MINIZ_OXIDE_DATABASE_URL"):
        build_settings(COMPETITIONS=SLUG)
    settings = build_settings(
        COMPETITIONS=SLUG, COMPETITION_MINIZ_OXIDE_DATABASE_URL="postgresql+psycopg://h/miniz"
    )
    assert settings.competitions == (CompetitionConfig(SLUG, "postgresql+psycopg://h/miniz"),)


def test_production_takes_the_same_configuration():
    # No production-only fallback to refuse: there is no fallback anywhere.
    settings = Settings.from_env(
        production_env(
            COMPETITIONS=SLUG, COMPETITION_MINIZ_OXIDE_DATABASE_URL="postgresql+psycopg://h/m"
        )
    )
    assert [c.slug for c in settings.competitions] == [SLUG]


def test_a_malformed_slug_is_refused_at_startup():
    with pytest.raises(SettingsError, match="not a competition slug"):
        build_settings(COMPETITIONS="Miniz_Oxide")


def test_the_url_variable_is_derived_from_the_slug():
    assert database_url_variable("miniz-oxide") == "COMPETITION_MINIZ_OXIDE_DATABASE_URL"


def _build(*configs: CompetitionConfig, proofs: str = "postgresql+psycopg://h/proofs"):
    return build_registry(
        configs,
        proofs_database_url=proofs,
        adapters=ADAPTERS,
        create_engine=create_async_db_engine,
        session_factory=async_session_factory,
    )


def test_a_competition_without_an_adapter_is_refused():
    with pytest.raises(CompetitionConfigError, match="no adapter"):
        _build(CompetitionConfig("rust-competition", "postgresql+psycopg://h/rust"))


def test_a_competition_may_not_use_the_proofs_database():
    with pytest.raises(CompetitionConfigError, match="proofs database"):
        _build(CompetitionConfig(SLUG, "postgresql+psycopg://h/p"), proofs="postgresql+psycopg://h/p")


def test_two_competitions_may_not_share_a_database():
    adapters = {**ADAPTERS, "toy": Toy}
    with pytest.raises(CompetitionConfigError, match="same database"):
        build_registry(
            (
                CompetitionConfig(SLUG, "postgresql+psycopg://h/shared"),
                CompetitionConfig("toy", "postgresql+psycopg://h/shared"),
            ),
            proofs_database_url="postgresql+psycopg://h/proofs",
            adapters=adapters,
            create_engine=create_async_db_engine,
            session_factory=async_session_factory,
        )


def test_each_competition_gets_an_engine_of_its_own():
    async def scenario():
        registry = build_registry(
            (
                CompetitionConfig(SLUG, "postgresql+psycopg://h/miniz"),
                CompetitionConfig("toy", "postgresql+psycopg://h/toy"),
            ),
            proofs_database_url="postgresql+psycopg://h/proofs",
            adapters={**ADAPTERS, "toy": Toy},
            create_engine=create_async_db_engine,
            session_factory=async_session_factory,
        )
        engines = [c.engine for c in registry]
        assert [c.slug for c in registry] == [SLUG, "toy"]
        assert engines[0] is not engines[1]
        assert [e.url.database for e in engines] == ["miniz", "toy"]
        await registry.dispose()

    run(scenario())


# ── a second adapter, to prove the router is generic ──────────────────────────────────────


class Toy(base.CompetitionAdapter):
    """A competition with one file, one metric and no optional capabilities, held in memory."""

    info = base.CompetitionInfo(
        slug="toy",
        name="Toy",
        description="One file, scored by length.",
        files=(base.FileSpec("answer.txt", 64, "the answer"),),
        metrics=(base.Metric("length", "Length", "chars", "higher"),),
        ranked_by="length",
    )

    def __init__(self) -> None:
        self.rows: list[base.Submission] = []

    async def headline(self, session):
        return {"target": 42}

    async def queue_depth(self, session):
        return sum(1 for r in self.rows if r.state is base.SubmissionState.QUEUED)

    async def leaderboard(self, session, *, after, limit):
        return []

    async def rank_before(self, session, after):
        return 0

    async def submissions(self, session, *, after, limit, state=None, hotkeys=None):
        return [r for r in reversed(self.rows) if hotkeys is None or r.hotkey in hotkeys][:limit]

    async def submission(self, session, submission_id):
        return next((r for r in self.rows if r.id == submission_id), None)

    async def report(self, session, submission_id):
        return None

    async def stats(self, session):
        return base.Stats(by_state={"queued": len(self.rows)}, competitors=0, last_accepted_at=None)

    async def best_for(self, session, hotkey):
        return None

    async def eligibility(self, session, hotkey):
        return base.Eligibility(registered=True, slots_remaining=None, pending=0)

    async def registered_by(self, session, *, hotkey, coldkey):
        return True

    async def hotkeys_of(self, session, coldkey):
        return []

    async def queue(self, session, *, hotkey, digest, files):
        row = base.Submission(
            id=str(len(self.rows) + 1),
            hotkey=hotkey,
            digest=digest,
            state=base.SubmissionState.QUEUED,
            submitted_at=datetime.now(UTC),
            finished_at=None,
            metrics={"length": len(files["answer.txt"])},
            position=("0", str(len(self.rows) + 1)),
        )
        self.rows.append(row)
        return base.Queued(submission=row, created=True, eligibility=await self.eligibility(session, hotkey))


# ── fixtures ──────────────────────────────────────────────────────────────────────────────


async def _competition_engine():
    """A competition database holding exactly the adapter's slice of the schema, empty."""
    engine = create_async_db_engine(competition_dsn())
    async with engine.begin() as conn:
        await conn.run_sync(t.metadata.drop_all)
        await conn.run_sync(t.metadata.create_all)
    return engine


def _registry(engine, *extra: base.CompetitionAdapter) -> CompetitionRegistry:
    adapters = (MinizOxide(), *extra)
    return CompetitionRegistry(
        Competition(adapter=a, engine=engine, sessions=async_session_factory(engine))
        for a in adapters
    )


class Kit:
    """The API with miniz_oxide (and optionally more) served from the competition database."""

    def __init__(self, *extra: base.CompetitionAdapter, **overrides: str) -> None:
        self.extra = extra
        self.overrides = overrides

    async def __aenter__(self):
        self.engine = await _competition_engine()
        self.api = await harness(competitions=_registry(self.engine, *self.extra), **self.overrides).setup()
        return self

    async def __aexit__(self, *exc):
        await self.api.teardown()

    async def execute(self, statement, rows=None):
        async with self.engine.begin() as conn:
            result = await conn.execute(statement, rows) if rows is not None else await conn.execute(statement)
            try:
                return result.scalar_one()
            except Exception:  # noqa: BLE001 - not every statement returns one value
                return None

    async def register(self, hotkey: str, *, coldkey: str = "5Cold", slots: int = 1) -> None:
        # One row per registration event; (uid, block) is unique, so each gets its own uid.
        for _ in range(slots):
            self.uids = getattr(self, "uids", 0) + 1
            await self.execute(
                t.registrations.insert().values(
                    uid=self.uids, ss58_hot=hotkey, ss58_cold=coldkey, block=100,
                    block_date=datetime.now(UTC),
                )
            )

    async def submission(self, hotkey: str | None, **values) -> int:
        values.setdefault("digest", uuid.uuid4().hex)
        values.setdefault("state", "accepted")
        if hotkey is None:
            values.setdefault("baseline_key", "incumbent")
        return await self.execute(
            t.submissions.insert().values(hotkey=hotkey, **values).returning(t.submissions.c.id)
        )


async def _http(kit: Kit):
    return await client(kit.api)


def _keypair(uri: str = "//Alice"):
    from bittensor.sp_core import Keypair

    return Keypair.create_from_uri(uri)


def _digest(rust: bytes = RUST, lean: bytes = LEAN) -> str:
    return MinizOxide().digest({"parse.rs": rust, "Parse.lean": lean})


def _signed(keypair, *, digest: str, competition: str = SLUG, timestamp: int | None = None):
    stamp = int(time.time()) if timestamp is None else timestamp
    message = submit_message(
        competition=competition, digest=digest, hotkey=keypair.ss58_address, timestamp=stamp
    )
    return {
        "X-Conjectures-Hotkey": keypair.ss58_address,
        "X-Conjectures-Timestamp": str(stamp),
        "X-Conjectures-Signature": keypair.sign(message.encode()).hex(),
    }


def _files(rust: bytes = RUST, lean: bytes = LEAN):
    return {"parse.rs": ("parse.rs", rust), "Parse.lean": ("Parse.lean", lean)}


# ── an unconfigured deployment ────────────────────────────────────────────────────────────


def test_a_deployment_serving_no_competitions_lists_none_and_404s_every_slug():
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as http:
                assert (await http.get("/v1/competitions")).json() == {"competitions": []}
                for path in (f"/v1/competitions/{SLUG}", f"/v1/competitions/{SLUG}/leaderboard"):
                    assert (await http.get(path)).status_code == 404, path
                ready = await http.get("/readyz")
            assert ready.status_code == 200
            assert ready.json()["competitions"] == {}
        finally:
            await kit.teardown()

    run(scenario())


def test_the_competition_surface_lives_under_one_prefix_and_no_other():
    from submission_api.app import create_app

    app = create_app(settings=build_settings())
    tagged = [
        path
        for path, operations in app.openapi()["paths"].items()
        for op in operations.values()
        if "competitions" in op.get("tags", [])
    ]
    assert tagged and all(path.startswith("/v1/competitions") for path in tagged)
    assert not any("competition" in path for path in app.openapi()["paths"] if path.startswith(("/v1/me", "/v1/admin")))


# ── reading ───────────────────────────────────────────────────────────────────────────────


@needs_competition_db
def test_each_competition_describes_itself_well_enough_to_render_and_submit_to():
    async def scenario():
        async with Kit(Toy()) as kit:
            await kit.submission(None, incumbent_bytes=2_153_387, bytes=2_153_387)
            async with await _http(kit) as http:
                index = (await http.get("/v1/competitions")).json()["competitions"]
            miniz, toy = index
            assert miniz["slug"] == SLUG
            assert [f["name"] for f in miniz["files"]] == ["parse.rs", "Parse.lean"]
            assert miniz["ranked_by"] == "bytes"
            assert miniz["headline"] == {"incumbent_bytes": 2_153_387, "speed_floor": 8.0}
            assert miniz["submissions_open"] is True
            assert toy["files"] == [{"name": "answer.txt", "max_bytes": 64, "description": "the answer"}]
            assert toy["metrics"][0]["better"] == "higher"
            assert toy["headline"] == {"target": 42}

    run(scenario())


@needs_competition_db
def test_the_board_ranks_each_hotkeys_best_and_pages_with_absolute_ranks():
    async def scenario():
        async with Kit() as kit:
            await kit.submission("5Alice", bytes=2_300_000, incumbent_bytes=2_400_000, raw_bytes=10_000_000)
            await kit.submission("5Alice", bytes=2_200_000, incumbent_bytes=2_400_000)
            await kit.submission("5Bob", bytes=2_250_000)
            await kit.submission("5Carol", bytes=2_100_000)
            await kit.submission("5Dave", bytes=1_000, state="rejected")
            await kit.submission(None, bytes=10)  # a baseline: not anyone's standing
            async with await _http(kit) as http:
                first = (await http.get(f"/v1/competitions/{SLUG}/leaderboard?limit=2")).json()
                second = (
                    await http.get(
                        f"/v1/competitions/{SLUG}/leaderboard",
                        params={"limit": 2, "cursor": first["next_cursor"]},
                    )
                ).json()
                # A cursor from one feed is refused by another, not reinterpreted.
                crossed = await http.get(
                    f"/v1/competitions/{SLUG}/submissions", params={"cursor": first["next_cursor"]}
                )
            ranking = first["ranking"] + second["ranking"]
            assert [(r["rank"], r["hotkey"], r["metrics"]["bytes"]) for r in ranking] == [
                (1, "5Carol", 2_100_000),
                (2, "5Alice", 2_200_000),
                (3, "5Bob", 2_250_000),
            ]
            assert ranking[1]["metrics"]["vs_incumbent"] == round(2_200_000 / 2_400_000, 5)
            assert second["next_cursor"] is None
            assert crossed.status_code == 400
            assert crossed.json()["reason_code"] == "INVALID_CURSOR"

    run(scenario())


@needs_competition_db
def test_a_cursor_for_one_competition_is_refused_by_another():
    async def scenario():
        async with Kit(Toy()) as kit:
            for n in range(3):
                await kit.submission(f"5Hot{n}", bytes=100 + n)
            async with await _http(kit) as http:
                page = (await http.get(f"/v1/competitions/{SLUG}/submissions?limit=1")).json()
                crossed = await http.get(
                    "/v1/competitions/toy/submissions", params={"cursor": page["next_cursor"]}
                )
            assert crossed.status_code == 400

    run(scenario())


@needs_competition_db
def test_the_feed_filters_by_state_and_hotkey_and_hides_baselines():
    async def scenario():
        async with Kit() as kit:
            await kit.submission("5Alice", state="queued")
            await kit.submission("5Alice", state="rejected")
            await kit.submission("5Bob", state="accepted", bytes=5)
            await kit.submission(None, bytes=1)
            async with await _http(kit) as http:
                everything = (await http.get(f"/v1/competitions/{SLUG}/submissions")).json()
                alice = (await http.get(f"/v1/competitions/{SLUG}/submissions?hotkey=5Alice")).json()
                rejected = (await http.get(f"/v1/competitions/{SLUG}/submissions?state=rejected")).json()
                bad = await http.get(f"/v1/competitions/{SLUG}/submissions?state=bogus")
            assert [r["hotkey"] for r in everything["items"]] == ["5Bob", "5Alice", "5Alice"]
            assert {r["state"] for r in alice["items"]} == {"queued", "rejected"}
            assert [r["state"] for r in rejected["items"]] == ["rejected"]
            assert bad.status_code == 400

    run(scenario())


@needs_competition_db
def test_one_submission_its_report_and_only_an_accepted_ones_source():
    async def scenario():
        async with Kit() as kit:
            accepted = await kit.submission("5Alice", bytes=7, report="ACCEPTED\n", exit_code=0)
            rejected = await kit.submission("5Alice", state="rejected", report="REJECTED\n")
            for name, content in (("parse.rs", RUST), ("Parse.lean", LEAN)):
                for sid in (accepted, rejected):
                    await kit.execute(
                        t.submission_files.insert().values(submission_id=sid, name=name, content=content)
                    )
            base_path = f"/v1/competitions/{SLUG}/submissions"
            async with await _http(kit) as http:
                one = (await http.get(f"{base_path}/{accepted}")).json()
                report = (await http.get(f"{base_path}/{rejected}/report")).json()
                source = await http.get(f"{base_path}/{accepted}/source")
                hidden = await http.get(f"{base_path}/{rejected}/source")
                missing = await http.get(f"{base_path}/999999")
                garbage = await http.get(f"{base_path}/not-an-id")
            assert one["id"] == str(accepted) and one["state"] == "accepted"
            assert one["metrics"]["bytes"] == 7
            assert report["report"] == "REJECTED\n"
            assert source.json()["files"] == {"parse.rs": RUST.decode(), "Parse.lean": LEAN.decode()}
            assert hidden.status_code == 404
            assert missing.status_code == 404 and garbage.status_code == 404

    run(scenario())


@needs_competition_db
def test_a_competitor_sees_their_standing_and_what_they_may_still_queue():
    async def scenario():
        async with Kit() as kit:
            await kit.register("5Alice", slots=3)
            await kit.submission("5Carol", bytes=90)
            await kit.submission("5Alice", bytes=100)
            await kit.submission("5Alice", state="queued")
            async with await _http(kit) as http:
                alice = (await http.get(f"/v1/competitions/{SLUG}/competitors/5Alice")).json()
                stranger = (await http.get(f"/v1/competitions/{SLUG}/competitors/5Nobody")).json()
            # Three registrations, one queued: two more may be queued now. The accepted row has
            # not spent one here, because the test inserted it without an entitlement claim.
            assert alice["registered"] is True
            assert (alice["slots_remaining"], alice["pending"]) == (2, 1)
            assert alice["best"]["rank"] == 2 and alice["best"]["metrics"]["bytes"] == 100
            assert stranger == {
                "competition": SLUG,
                "hotkey": "5Nobody",
                "registered": False,
                "slots_remaining": 0,
                "pending": 0,
                "best": None,
            }

    run(scenario())


@needs_competition_db
def test_stats_count_every_state_including_the_empty_ones():
    async def scenario():
        async with Kit() as kit:
            await kit.submission("5Alice", bytes=100, finished_at=datetime(2026, 9, 1, tzinfo=UTC))
            await kit.submission("5Bob", state="rejected")
            async with await _http(kit) as http:
                stats = (await http.get(f"/v1/competitions/{SLUG}/stats")).json()
            assert stats["submissions"] == {
                "queued": 0, "verifying": 0, "accepted": 1, "rejected": 1, "error": 0,
            }
            assert stats["competitors"] == 1
            assert stats["best"] == {"bytes": 100}
            assert stats["last_accepted_at"] == "2026-09-01T00:00:00Z"

    run(scenario())


@needs_competition_db
def test_scores_are_the_latest_scoring_pass_as_the_competition_recorded_it():
    async def scenario():
        async with Kit() as kit:
            async with await _http(kit) as http:
                none_yet = await http.get(f"/v1/competitions/{SLUG}/scores")
            ws = await kit.execute(
                t.weight_sets.insert()
                .values(netuid=66, block=9_000_000, uids=[0, 121], weights=[0.2, 0.8],
                        accepted=False, dry_run=True, summary="dry")
                .returning(t.weight_sets.c.id)
            )
            await kit.execute(
                t.score_snapshots.insert().values(
                    weight_set_id=ws, hotkey="5Alice", submission_id=7, payable_weight=0.6,
                    on_frontier=True, pareto_weight=0.6, combined_weight=0.6, ratio_pct=21.5,
                )
            )
            await kit.execute(
                t.score_snapshots.insert().values(
                    weight_set_id=ws, baseline_key="incumbent", payable_weight=0.0,
                    burn_reason="baseline",
                )
            )
            async with await _http(kit) as http:
                scores = (await http.get(f"/v1/competitions/{SLUG}/scores")).json()
            assert none_yet.status_code == 404
            assert scores["dry_run"] is True and scores["block"] == 9_000_000
            first, second = scores["entries"]
            assert (first["hotkey"], first["submission"], first["weight"]) == ("5Alice", "7", 0.6)
            assert first["metrics"]["on_frontier"] == 1
            assert (second["hotkey"], second["note"]) == (None, "baseline")

    run(scenario())


@needs_competition_db
def test_a_competition_without_a_capability_answers_404_not_supported():
    async def scenario():
        async with Kit(Toy()) as kit:
            async with await _http(kit) as http:
                scores = await http.get("/v1/competitions/toy/scores")
            assert scores.status_code == 404
            assert scores.json()["reason_code"] == "NOT_SUPPORTED"

    run(scenario())


# ── the signed submit ─────────────────────────────────────────────────────────────────────


@needs_competition_db
def test_a_registered_hotkey_queues_a_submission_and_its_files_reach_the_database():
    async def scenario():
        keypair = _keypair()
        async with Kit() as kit:
            await kit.register(keypair.ss58_address)
            async with await _http(kit) as http:
                created = await http.post(
                    f"/v1/competitions/{SLUG}/submissions",
                    files=_files(),
                    headers=_signed(keypair, digest=_digest()),
                )
                # The same files again: the same submission, and no second place in the queue --
                # even though the one slot is now held by the first.
                retried = await http.post(
                    f"/v1/competitions/{SLUG}/submissions",
                    files=_files(),
                    headers=_signed(keypair, digest=_digest()),
                )
            assert created.status_code == 201, created.text
            body = created.json()
            assert (body["state"], body["created"], body["slots_remaining"]) == ("queued", True, 0)
            assert body["digest"] == _digest()
            assert retried.json()["submission"] == body["submission"]
            assert retried.json()["created"] is False
            async with kit.engine.connect() as conn:
                stored = dict(
                    (await conn.execute(t.submission_files.select().with_only_columns(
                        t.submission_files.c.name, t.submission_files.c.content
                    ))).all()
                )
            assert stored == {"parse.rs": RUST, "Parse.lean": LEAN}

    run(scenario())


@needs_competition_db
def test_submitting_needs_a_registration_and_an_unspent_one():
    async def scenario():
        keypair = _keypair()
        async with Kit() as kit:
            async with await _http(kit) as http:
                unregistered = await http.post(
                    f"/v1/competitions/{SLUG}/submissions",
                    files=_files(),
                    headers=_signed(keypair, digest=_digest()),
                )
                await kit.register(keypair.ss58_address)
                await http.post(
                    f"/v1/competitions/{SLUG}/submissions",
                    files=_files(),
                    headers=_signed(keypair, digest=_digest()),
                )
                other = b"fn parse(x: &[u8]) { /* another */ }"
                exhausted = await http.post(
                    f"/v1/competitions/{SLUG}/submissions",
                    files=_files(rust=other),
                    headers=_signed(keypair, digest=_digest(rust=other)),
                )
            assert unregistered.status_code == 402
            assert unregistered.json()["reason_code"] == "NOT_REGISTERED"
            assert exhausted.status_code == 402
            assert exhausted.json()["reason_code"] == "NO_ENTITLEMENT"

    run(scenario())


@needs_competition_db
def test_a_signature_for_another_competition_or_other_files_is_refused():
    async def scenario():
        keypair = _keypair()
        async with Kit() as kit:
            await kit.register(keypair.ss58_address)
            async with await _http(kit) as http:
                replayed = await http.post(
                    f"/v1/competitions/{SLUG}/submissions",
                    files=_files(),
                    headers=_signed(keypair, digest=_digest(), competition="toy"),
                )
                swapped = await http.post(
                    f"/v1/competitions/{SLUG}/submissions",
                    files=_files(rust=b"fn parse() { swapped }"),
                    headers=_signed(keypair, digest=_digest()),
                )
                stale = await http.post(
                    f"/v1/competitions/{SLUG}/submissions",
                    files=_files(),
                    headers=_signed(keypair, digest=_digest(), timestamp=int(time.time()) - 3_600),
                )
            assert replayed.status_code == 401
            assert swapped.status_code == 401
            assert stale.status_code == 401
            assert stale.json()["reason_code"] == "SIGNATURE_EXPIRED"

    run(scenario())


@needs_competition_db
def test_the_body_must_carry_exactly_the_declared_files_within_their_caps():
    async def scenario():
        keypair = _keypair()
        async with Kit() as kit:
            await kit.register(keypair.ss58_address)
            path = f"/v1/competitions/{SLUG}/submissions"
            async with await _http(kit) as http:
                missing = await http.post(
                    path, files={"parse.rs": ("parse.rs", RUST)}, headers=_signed(keypair, digest="x")
                )
                huge = b"x" * (512 * 1024 + 1)
                oversized = await http.post(
                    path, files=_files(rust=huge), headers=_signed(keypair, digest="x")
                )
            assert missing.status_code == 400
            assert oversized.status_code == 413

    run(scenario())


@needs_competition_db
def test_a_pause_refuses_before_anything_is_verified():
    async def scenario():
        async with Kit(SUBMISSIONS_PAUSED="1") as kit:
            async with await _http(kit) as http:
                refused = await http.post(
                    f"/v1/competitions/{SLUG}/submissions",
                    files=_files(),
                    headers={
                        "X-Conjectures-Hotkey": "junk",
                        "X-Conjectures-Timestamp": "0",
                        "X-Conjectures-Signature": "junk",
                    },
                )
                described = (await http.get(f"/v1/competitions/{SLUG}")).json()
            assert refused.status_code == 503
            assert refused.json()["reason_code"] == "SUBMISSIONS_PAUSED"
            assert described["submissions_open"] is False

    run(scenario())


@needs_competition_db
def test_a_new_competition_is_submitted_to_through_the_same_router():
    async def scenario():
        keypair = _keypair()
        toy = Toy()
        async with Kit(toy) as kit:
            digest = toy.digest({"answer.txt": b"forty-two"})
            async with await _http(kit) as http:
                created = await http.post(
                    "/v1/competitions/toy/submissions",
                    files={"answer.txt": ("answer.txt", b"forty-two")},
                    headers=_signed(keypair, digest=digest, competition="toy"),
                )
                listed = (await http.get("/v1/competitions/toy/submissions")).json()
            assert created.status_code == 201, created.text
            assert created.json()["slots_remaining"] is None
            assert listed["items"][0]["metrics"] == {"length": 9}

    run(scenario())


# ── the browser path, and the account's own ───────────────────────────────────────────────


async def _link_submission_coldkey(kit: Kit, account_id: str, coldkey: str) -> None:
    """Link and designate a coldkey directly: the competition path is under test, not linking.

    Both rows, because `account_submission_coldkey_is_linked` requires the designated coldkey to
    be one the account proved it controls -- the constraint that stops an account inheriting a
    stranger's registrations.
    """
    from sqlalchemy import update

    from conjectures_subnet.db.models import Account, AccountWallet

    async with kit.api.services.sessions() as session:
        session.add(
            AccountWallet(account_id=uuid.UUID(account_id), coldkey=coldkey, signature=b"\x00" * 64)
        )
        await session.flush()
        await session.execute(
            update(Account).where(Account.id == uuid.UUID(account_id)).values(submission_coldkey=coldkey)
        )
        await session.commit()


@needs_competition_db
def test_an_account_submits_for_a_hotkey_its_own_coldkey_registered_and_no_other():
    async def scenario():
        async with Kit() as kit:
            await kit.register("5MineHot", coldkey=MINER_COLDKEY)
            await kit.register("5TheirsHot", coldkey=OTHER_MINER_COLDKEY)
            path = f"/v1/competitions/{SLUG}/submissions/session"
            async with await _http(kit) as http:
                account = await sign_in_by_email(kit.api, http)
                unlinked = await http.post(
                    path, files=_files(), headers={**same_origin(http), "X-Conjectures-Hotkey": "5MineHot"}
                )
                await _link_submission_coldkey(kit, account["id"], MINER_COLDKEY)
                mine = await http.post(
                    path, files=_files(), headers={**same_origin(http), "X-Conjectures-Hotkey": "5MineHot"}
                )
                theirs = await http.post(
                    path, files=_files(), headers={**same_origin(http), "X-Conjectures-Hotkey": "5TheirsHot"}
                )
                listed = (await http.get(f"/v1/competitions/{SLUG}/me/submissions")).json()
            assert unlinked.status_code == 402
            assert unlinked.json()["reason_code"] == "NO_SUBMISSION_COLDKEY"
            assert mine.status_code == 201, mine.text
            assert theirs.status_code == 403
            assert theirs.json()["reason_code"] == "HOTKEY_NOT_YOURS"
            assert [row["id"] for row in listed["items"]] == [mine.json()["submission"]]

    run(scenario())


@needs_competition_db
def test_the_account_listing_includes_hotkey_signed_submissions_from_its_hotkeys():
    async def scenario():
        async with Kit() as kit:
            signed = await kit.submission("5MineHot", state="queued")
            await kit.submission("5SomeoneElse", state="queued")
            await kit.register("5MineHot", coldkey=MINER_COLDKEY)
            async with await _http(kit) as http:
                account = await sign_in_by_email(kit.api, http)
                await _link_submission_coldkey(kit, account["id"], MINER_COLDKEY)
                listed = (await http.get(f"/v1/competitions/{SLUG}/me/submissions")).json()
                anonymous = await (await client(kit.api)).get(f"/v1/competitions/{SLUG}/me/submissions")
            assert [row["id"] for row in listed["items"]] == [str(signed)]
            assert anonymous.status_code == 401

    run(scenario())


# ── the operator surface ──────────────────────────────────────────────────────────────────


@needs_competition_db
def test_an_operator_sees_what_is_stuck_and_puts_it_back():
    async def scenario():
        async with Kit() as kit:
            long_ago = datetime.now(UTC) - timedelta(days=1)
            stale = await kit.submission("5Alice", state="verifying", claimed_at=long_ago, worker_id="gate-1")
            await kit.submission("5Alice", state="verifying", claimed_at=datetime.now(UTC))
            done = await kit.submission("5Bob", state="accepted", bytes=1)
            base_path = f"/v1/competitions/{SLUG}/admin"
            async with await _http(kit) as http:
                account = await sign_in_by_email(kit.api, http)
                forbidden = await http.get(f"{base_path}/queue")
                await grant_role(kit.api, account["id"], ADMIN_ROLE)
                queue = (await http.get(f"{base_path}/queue")).json()
                requeued = await http.post(
                    f"{base_path}/submissions/{stale}/requeue?reason=gate+host+died",
                    headers=same_origin(http),
                )
                refused = await http.post(
                    f"{base_path}/submissions/{done}/requeue", headers=same_origin(http)
                )
                after = (await http.get(f"{base_path}/submissions/{stale}")).json()
            assert forbidden.status_code == 403
            assert [item["submission"]["id"] for item in queue["items"]] == [str(stale)]
            assert queue["items"][0]["worker_id"] == "gate-1"
            assert requeued.json() == {"competition": SLUG, "id": str(stale), "requeued": True, "state": "queued"}
            assert refused.json()["requeued"] is False and refused.json()["state"] == "accepted"
            assert after["submission"]["state"] == "queued" and after["worker_id"] is None

    run(scenario())


# ── a competition's database going away ───────────────────────────────────────────────────


def test_an_unreachable_competition_is_a_503_for_its_routes_and_nothing_else():
    """Reported by readiness, never gating it: every replica reads the same competition
    database, so gating would take the whole proofs platform down with one competition."""

    async def scenario():
        dead = create_async_db_engine("postgresql+psycopg://nobody:nothing@127.0.0.1:1/absent")
        registry = CompetitionRegistry(
            [Competition(adapter=MinizOxide(), engine=dead, sessions=async_session_factory(dead))]
        )
        kit = await harness(competitions=registry).setup()
        try:
            async with await client(kit) as http:
                detail = await http.get(f"/v1/competitions/{SLUG}")
                index = await http.get("/v1/competitions")
                ready = await http.get("/readyz")
            assert detail.status_code == 503
            assert detail.json()["reason_code"] == "COMPETITION_UNAVAILABLE"
            assert index.json() == {"competitions": []}
            assert ready.status_code == 200
            assert ready.json()["competitions"] == {SLUG: False}
        finally:
            await kit.teardown()

    run(scenario())


@needs_competition_db
def test_readiness_reports_a_reachable_competition():
    async def scenario():
        async with Kit() as kit:
            async with await _http(kit) as http:
                ready = await http.get("/readyz")
            assert ready.json()["competitions"] == {SLUG: True}

    run(scenario())

