"""Prospective economics, historical family classification, and mandatory evidence enforcement."""
import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError

from conftest_api import harness, postgres_dsn
from conjectures_subnet.bounty import StaticBalanceReader
from conjectures_subnet.bounty_policy import TREASURY_CHAT_POLICY, legacy_chat_bonus_cents
from conjectures_subnet.db.models import BountyTask, RewardEvent, Submission
from submission_api.trajectories import eligibility_reason


@pytest.mark.parametrize('module,amount', [
    ('FormalConjectures.ErdosProblems.«313»', 50000),
    ('FormalConjectures.GreensOpenProblems.«51»', 25000),
    ('FormalConjectures.ResearchTargets.Math30', 25000),
    ('FormalConjectures.Wikipedia.ArtinPrimitiveRootsConjecture', 25000),
    ('FakeErdosProblems.1', 25000),
])
def test_frozen_source_module_determines_legacy_bonus(module, amount):
    assert legacy_chat_bonus_cents(module) == amount


def test_retrospective_cutoff_is_not_applied_to_required_new_bounty_logs():
    now = datetime(2026, 10, 12, tzinfo=UTC)
    sub = SimpleNamespace(created_at=now, bounty_policy_version='legacy',
                          verification_status='VERIFIED', manual_review_status='APPROVED', reward_status='ELIGIBLE')
    decision = SimpleNamespace(kind='HUMAN', decision='APPROVED', reason_code='REVIEW_APPROVED')
    assert eligibility_reason(sub, decision, cutoff=now, now=now)
    sub.created_at = now - timedelta(days=1000)
    assert eligibility_reason(sub, decision, cutoff=now, now=now) is None
    sub.created_at = now + timedelta(days=1000)
    sub.bounty_policy_version = TREASURY_CHAT_POLICY
    assert eligibility_reason(sub, decision, cutoff=now, now=now + timedelta(days=1001)) is None


@pytest.mark.skipif(postgres_dsn() is None, reason='private database required')
def test_new_target_policy_is_durable_and_follows_the_curve_from_its_own_start():
    async def scenario():
        kit = await harness().setup()
        try:
            now = datetime(2026, 10, 12, tzinfo=UTC)
            old = kit.services.pricing.reward_target_ids[0]
            pricer = replace(kit.services.pricing, new_target_policy=TREASURY_CHAT_POLICY,
                             reward_target_ids=(old, 'packing34'), clock=lambda: now,
                             balance_reader=StaticBalanceReader(4000))
            async with kit.session() as session:
                session.add(BountyTask(reward_target_id=old, opened_at=now, pricing_policy='legacy',
                                       start_share_numerator=1, start_share_denominator=20))
                await session.commit()
                quotes = await pricer.quote_many(session, reward_target_ids=(old, 'packing34'))
                assert quotes.quotes[old].amount_rao == 200
                assert quotes.quotes['packing34'].amount_rao == 100
                assert quotes.quotes['packing34'].policy_version == TREASURY_CHAT_POLICY
                await session.commit()
                # The legacy target opened at 1/20, the new one at 1/40; both reach 1/8 at 15 days.
                assert quotes.quotes['packing34'].inputs['constant_denominator'] == 40
                later = replace(pricer, clock=lambda: now + timedelta(days=100))
                assert (await later.quote(session, reward_target_id='packing34')).amount_rao == 500
                richer = replace(later, balance_reader=StaticBalanceReader(8000))
                assert (await richer.quote(session, reward_target_id='packing34')).amount_rao == 1000
                row = await session.get(BountyTask, 'packing34')
                row.pricing_policy = 'legacy'
                with pytest.raises(IntegrityError, match='original pricing policy'):
                    await session.flush()
                await session.rollback()
                with pytest.raises(IntegrityError, match='original pricing policy'):
                    await session.execute(delete(BountyTask).where(BountyTask.reward_target_id == 'packing34'))
                await session.rollback()
        finally:
            await kit.teardown()
    asyncio.run(scenario())


@pytest.mark.skipif(postgres_dsn() is None, reason='private database required')
def test_required_logs_block_all_payout_instructions_until_review():
    from test_api_accounts import client, same_origin
    from test_api_trajectories import accepted, reviewer, review_body, kit_config
    from conjectures_subnet.db.engine import create_db_engine, session_factory
    from conjectures_subnet.db.models import Account, ReviewOutcome, VerificationState
    from conjectures_subnet.db import submissions as proof_store
    from payout_notifier.worker import PayoutNotifier

    async def scenario():
        kit = await kit_config().setup()
        kit.services = replace(kit.services, pricing=replace(kit.services.pricing, new_target_policy=TREASURY_CHAT_POLICY))
        kit.app.state.services = kit.services
        engine = create_db_engine(postgres_dsn())
        worker = PayoutNotifier(sessions=session_factory(engine), webhook_url='', worker_id='required-logs-test')
        async def payout():
            async with kit.session() as session:
                sub = await session.get(Submission, uuid.UUID(sid))
                row = RewardEvent(submission_id=sub.id, amount_rao=sub.bounty_amount_rao,
                                  eligibility_reason='REVIEW_APPROVED', pricing_policy_version=sub.bounty_policy_version,
                                  pricing_inputs=sub.bounty_inputs, destination_coldkey=kit.settings.payment_recipient,
                                  initiated_by='test', generation_key='submission:' + sid)
                session.add(row)
                await session.commit()
        try:
            async with await client(kit) as owner, await client(kit) as admin:
                account, sid, package = await accepted(kit, owner, required=True)
                async with kit.session() as session:
                    row = await session.get(Account, uuid.UUID(account['id']))
                    row.payout_coldkey = kit.settings.payment_recipient
                    await session.commit()
                await reviewer(kit, admin)
                state = await owner.get(f'/v1/me/submissions/{sid}/trajectory')
                assert state.json()['chat_logs_required'] is True
                assert state.json()['amount_usd_cents'] == 0
                # The logs arrived with the proof but are not yet reviewed.
                claim = state.json()['claim']
                assert claim['amount_usd_cents'] == 0 and claim['status'] == 'UNDER_REVIEW'
                assert worker.seed_reward_events() == 0
                with pytest.raises(IntegrityError, match='requires reviewed chat logs'):
                    await payout()
                reviewed = await admin.post(f'/v1/admin/trajectories/{claim["id"]}/decision',
                                            json=review_body(state_version=claim['state_version']), headers=same_origin())
                assert reviewed.status_code == 200, reviewed.text
                assert reviewed.json()['status'] == 'APPROVED_BOUNTY_EVIDENCE'
                async with kit.session() as session:
                    sub = await session.get(Submission, uuid.UUID(sid))
                    sub.verification_status = VerificationState.REJECTED
                    await session.commit()
                assert worker.seed_reward_events() == 0
                with pytest.raises(IntegrityError, match='requires reviewed chat logs'):
                    await payout()
                async with kit.session() as session:
                    sub = await session.get(Submission, uuid.UUID(sid))
                    sub.verification_status = VerificationState.VERIFIED
                    await session.commit()
                # A later proof-review correction invalidates the exact decision binding.
                async with kit.session() as session:
                    decision = await proof_store.latest_binding_decision(session, uuid.UUID(sid))
                    await proof_store.correct_human_decision(session, uuid.UUID(sid), supersedes_id=decision.id,
                        decision=ReviewOutcome.APPROVED, reason_code='REVIEW_APPROVED', reviewer='fixture-correction',
                        notes='Correct public explanation only', notes_public='Correct public explanation only')
                    await session.commit()
                assert worker.seed_reward_events() == 0
                with pytest.raises(IntegrityError, match='requires reviewed chat logs'):
                    await payout()
                rebound = await admin.post(f'/v1/admin/trajectories/{claim["id"]}/reopen', headers=same_origin(), json={
                    'action': 'REBIND_PROOF_DECISION', 'expected_revision': 1, 'expected_state_version': 3,
                    'note': 'Verified that the proof and approval outcome remain unchanged.'})
                assert rebound.status_code == 200, rebound.text
                assert rebound.json()['status'] == 'APPROVED_BOUNTY_EVIDENCE'
                assert worker.seed_reward_events() == 1
                assert worker.seed_reward_events() == 0
                async with kit.session() as session:
                    events = (await session.scalars(select(RewardEvent))).all()
                    assert len(events) == 1
                    assert events[0].pricing_policy_version == TREASURY_CHAT_POLICY
                    assert events[0].amount_rao == kit.settings.bounty_pool_balance_rao // 40
        finally:
            engine.dispose()
            await kit.teardown()
    asyncio.run(scenario())


@pytest.mark.skipif(postgres_dsn() is None, reason='private database required')
@pytest.mark.parametrize('module,amount', [
    ('FormalConjectures.ErdosProblems.«313»', 50000),
    ('FormalConjectures.GreensOpenProblems.«51»', 25000),
    ('FormalConjectures.ResearchTargets.Math30', 25000),
])
def test_historical_bonus_amount_is_selected_by_server(module, amount):
    from conftest_api import task_entry
    from test_api_accounts import client
    from test_api_trajectories import accepted, as_upload, kit_config

    async def scenario():
        entry = task_entry()
        entry = replace(entry, manifest=replace(entry.manifest, source_module=module))
        kit = await kit_config(entries=(entry,)).setup()
        try:
            async with await client(kit) as owner:
                _, sid, package = await accepted(kit, owner)
                path = f'/v1/me/submissions/{sid}/trajectory'
                state = await owner.get(path)
                assert state.json()['amount_usd_cents'] == amount
                uploaded = await owner.post(path, **as_upload(package))
                assert uploaded.status_code == 200, uploaded.text
                assert uploaded.json()['amount_usd_cents'] == amount
                assert not uploaded.json()['chat_logs_required']
        finally:
            await kit.teardown()
    asyncio.run(scenario())


@pytest.mark.skipif(postgres_dsn() is None, reason='private database required')
def test_new_target_locks_reserve_like_every_target_and_never_reprice():
    from conftest_api import task_entry, distinct_bundle
    from test_api_accounts import client
    from test_api_session_submissions import SESSION, _digest, keyless_account, params, zip_headers
    from test_api_trajectories import DEPOSITS, as_upload, kit_config, package_for

    async def scenario():
        other = 'fc-target:Fixture.second'
        kit = await kit_config(entries=(task_entry(), task_entry(task_id='second-task', reward_target_id=other)),
                               BOUNTY_POOL_BALANCE_RAO='4000').setup()
        pricer = replace(kit.services.pricing, new_target_policy=TREASURY_CHAT_POLICY)
        kit.services = replace(kit.services, pricing=pricer)
        kit.app.state.services = kit.services
        try:
            async with await client(kit) as owner:
                await keyless_account(kit, owner, credits_=2)
                bundle, proof = distinct_bundle('first-gross-share', coldkey=None)
                assert (await owner.post(DEPOSITS, **as_upload(package_for(proof, None)))).status_code == 200
                first = await owner.post(SESSION, params=params(bundle_sha256=_digest(bundle)), content=bundle,
                                         headers=zip_headers(owner))
                assert first.status_code == 201, first.text
                first_id = first.json()['submission']['id']
                bundle, proof = distinct_bundle('competing-gross-share', coldkey=None)
                assert (await owner.post(DEPOSITS, **as_upload(package_for(proof, None)))).status_code == 200
                second = await owner.post(SESSION, params=params(bundle_sha256=_digest(bundle)), content=bundle,
                                          headers=zip_headers(owner))
                assert second.status_code == 201, second.text
                second_id = second.json()['submission']['id']
            async with kit.session() as session:
                # 1/40 of 4000, then 1/40 of what the first lock leaves.
                first_row = await session.get(Submission, uuid.UUID(first_id))
                second_row = await session.get(Submission, uuid.UUID(second_id))
                assert (first_row.bounty_amount_rao, second_row.bounty_amount_rao) == (100, 97)
                # Competing attempts at one target reserve only their maximum.
                q = await pricer.quote(session, reward_target_id=other)
                assert q.amount_rao == 97 and q.inputs['committed_bounty_rao'] == 100
                richer = replace(pricer, balance_reader=StaticBalanceReader(8000))
                assert (await richer.quote(session, reward_target_id=other)).amount_rao == 197
                assert first_row.bounty_amount_rao == 100  # Existing lock is never repriced.
                poorer = replace(pricer, balance_reader=StaticBalanceReader(100))
                exhausted = await poorer.quote(session, reward_target_id=other)
                assert exhausted.amount_rao == 0 and not exhausted.available
                assert exhausted.reason == 'TREASURY_RESERVED'
        finally:
            await kit.teardown()
    asyncio.run(scenario())


@pytest.mark.skipif(postgres_dsn() is None, reason='private database required')
@pytest.mark.parametrize('enabled', [False, True])
def test_production_default_refuses_unusable_submission_paths_before_spending(enabled):
    from sqlalchemy import func
    from conftest_api import valid_bundle
    from test_api_submissions import _post
    from test_api_accounts import client
    from test_api_session_submissions import SESSION, _digest, keyless_account, params, zip_headers
    from test_api_trajectories import DEPOSITS, as_upload, kit_config, package_for
    from conftest_api import distinct_bundle
    from conjectures_subnet.bounty import DynamicBountyPricer

    async def scenario():
        kit = await kit_config(enabled=enabled).setup()
        # Remove only the harness's historical-policy override: this is the production default.
        pricer = replace(kit.services.pricing,
                         new_target_policy=DynamicBountyPricer.__dataclass_fields__['new_target_policy'].default)
        kit.services = replace(kit.services, pricing=pricer)
        kit.app.state.services = kit.services
        try:
            response = await _post(kit, valid_bundle())
            assert response.status_code == (409 if enabled else 503), response.text
            assert response.json()['reason_code'] == ('CHAT_LOG_ACCOUNT_REQUIRED' if enabled else 'CHAT_LOG_REVIEW_UNAVAILABLE')
            async with kit.session() as session:
                assert await session.scalar(select(func.count()).select_from(Submission)) == 0
            async with await client(kit) as owner:
                await keyless_account(kit, owner)
                bundle, proof = distinct_bundle('default-account', coldkey=None)
                result = await owner.post(SESSION, params=params(bundle_sha256=_digest(bundle)), content=bundle,
                                          headers=zip_headers(owner))
                # Open intake still refuses a new-bounty proof that arrives without its logs.
                assert result.status_code == 409, result.text
                assert result.json()['reason_code'] == ('CHAT_LOG_REQUIRED' if enabled else 'CHAT_LOG_REVIEW_UNAVAILABLE')
                assert (await owner.get('/v1/me/credits')).json()['credits_available'] == 1
                deposited = await owner.post(DEPOSITS, **as_upload(package_for(proof, None)))
                assert deposited.status_code == (200 if enabled else 503), deposited.text
                if enabled:
                    result = await owner.post(SESSION, params=params(bundle_sha256=_digest(bundle)), content=bundle,
                                              headers=zip_headers(owner))
                    assert result.status_code == 201, result.text
                balance = (await owner.get('/v1/me/credits')).json()['credits_available']
                assert balance == (0 if enabled else 1)
                meta = (await owner.get('/v1/catalog/meta')).json()['bounty']
                assert meta['new_target_policy_version'] == TREASURY_CHAT_POLICY
                assert meta['new_target_share_denominator'] == 40 and meta['new_target_chat_logs_required']
        finally:
            await kit.teardown()
    asyncio.run(scenario())


@pytest.mark.skipif(postgres_dsn() is None, reason='private database required')
def test_coldkey_signed_proof_carries_logs_deposited_from_a_cli_session():
    from sqlalchemy import func
    from conftest_api import distinct_bundle
    from conjectures_subnet.bounty import DynamicBountyPricer
    from conjectures_subnet.db.trajectory_models import TrajectoryClaim
    from test_api_accounts import client
    from test_api_cli_sessions import bearer, linked_account_with_cli_token
    from test_api_submissions import _post
    from test_api_trajectories import DEPOSITS, as_upload, kit_config, package_for

    async def scenario():
        kit = await kit_config().setup()
        pricer = replace(kit.services.pricing,
                         new_target_policy=DynamicBountyPricer.__dataclass_fields__['new_target_policy'].default)
        kit.services = replace(kit.services, pricing=pricer)
        kit.app.state.services = kit.services
        try:
            bundle, proof = distinct_bundle('cli-logs')

            async def submissions():
                async with kit.session() as session:
                    return await session.scalar(select(func.count()).select_from(Submission))

            # A coldkey linked to no account has nowhere to keep logs.
            refused = await _post(kit, bundle, proof_digest=proof)
            assert refused.status_code == 409, refused.text
            assert refused.json()['reason_code'] == 'CHAT_LOG_ACCOUNT_REQUIRED'
            account, token = await linked_account_with_cli_token(kit)
            # Linked, but no logs yet: refused before the transfer is spent.
            refused = await _post(kit, bundle, proof_digest=proof)
            assert refused.status_code == 409, refused.text
            assert refused.json()['reason_code'] == 'CHAT_LOG_REQUIRED'
            assert await submissions() == 0

            # A CLI token uploads them; it has no browser to prove where the request came from.
            upload = as_upload(package_for(proof, None))
            upload['headers'] = {**bearer(token), 'content-type': 'application/zip'}
            async with await client(kit) as cli:
                deposited = await cli.post(DEPOSITS, **upload)
            assert deposited.status_code == 200, deposited.text
            assert deposited.json()['status'] == 'DEPOSITED'

            # The same transfer now buys the submission, and the logs are bound to it.
            created = await _post(kit, bundle, proof_digest=proof)
            assert created.status_code == 201, created.text
            sid = uuid.UUID(created.json()['submission_id'])
            async with kit.session() as session:
                claim = await session.scalar(select(TrajectoryClaim).where(TrajectoryClaim.submission_id == sid))
                assert claim.status == 'UNDER_REVIEW' and str(claim.account_id) == account['id']
                sub = await session.get(Submission, sid)
                assert sub.bounty_policy_version == TREASURY_CHAT_POLICY
                # Payout attribution is unchanged: the coldkey that paid, not the account.
                assert sub.account_id is None
        finally:
            await kit.teardown()
    asyncio.run(scenario())


@pytest.mark.skipif(postgres_dsn() is None, reason='private database required')
def test_requested_revision_of_required_logs_can_come_from_the_cli():
    from conftest_api import distinct_bundle
    from conjectures_subnet.bounty import DynamicBountyPricer
    from conjectures_subnet.db.models import ReviewOutcome
    from conjectures_subnet.db import submissions as proof_store
    from conjectures_subnet.db.trajectory_models import TrajectoryClaim
    from test_api_accounts import client, same_origin
    from test_api_cli_sessions import bearer, linked_account_with_cli_token
    from test_api_reviews import _verify
    from test_api_submissions import _post
    from test_api_trajectories import (DEPOSITS, as_upload, fresh_upload_budget, kit_config, package_for,
                                       review_body, reviewer)

    def cli(upload, token):
        return {**upload, 'headers': {**bearer(token), 'content-type': 'application/zip'}}

    async def scenario():
        kit = await kit_config().setup()
        pricer = replace(kit.services.pricing,
                         new_target_policy=DynamicBountyPricer.__dataclass_fields__['new_target_policy'].default)
        kit.services = replace(kit.services, pricing=pricer)
        kit.app.state.services = kit.services
        try:
            bundle, proof = distinct_bundle('cli-revision')
            _, token = await linked_account_with_cli_token(kit)
            package = package_for(proof, None)
            async with await client(kit) as miner, await client(kit) as admin:
                assert (await miner.post(DEPOSITS, **cli(as_upload(package), token))).status_code == 200
                created = await _post(kit, bundle, proof_digest=proof)
                assert created.status_code == 201, created.text
                sid = created.json()['submission_id']
                path = f'/v1/me/submissions/{sid}/trajectory'
                # The proof records no account, but the logs' owner can follow them.
                state = await miner.get(path, headers=bearer(token))
                assert state.status_code == 200, state.text
                assert state.json()['chat_logs_required'] and not state.json()['eligible']
                await _verify(kit, sid)
                async with kit.session() as session:
                    await proof_store.record_human_decision(session, uuid.UUID(sid), decision=ReviewOutcome.APPROVED,
                                                            reason_code='REVIEW_APPROVED', reviewer='fixture-reviewer')
                    await session.commit()
                await reviewer(kit, admin)
                claim = state.json()['claim']
                changes = await admin.post(f'/v1/admin/trajectories/{claim["id"]}/decision', headers=same_origin(),
                                           json=review_body(decision='NEEDS_CHANGES', state_version=claim['state_version']))
                assert changes.status_code == 200, changes.text

                # A chat that shows the proof only after it was submitted is not the original.
                async with kit.session() as session:
                    sub = await session.get(Submission, uuid.UUID(sid))
                    created_at = sub.created_at
                    sub.created_at = created_at - timedelta(hours=3)
                    await session.commit()
                fresh_upload_budget(kit)
                late = await miner.post(path, **cli(as_upload(package_for(proof, sid),
                                                               state_version=changes.json()['state_version']), token))
                assert late.status_code == 409 and late.json()['reason_code'] == 'TRAJECTORY_AFTER_THE_FACT'
                async with kit.session() as session:
                    sub = await session.get(Submission, uuid.UUID(sid))
                    sub.created_at = created_at
                    await session.commit()

                fresh_upload_budget(kit)
                revised = await miner.post(path, **cli(as_upload(package_for(proof, sid),
                                                                  state_version=changes.json()['state_version']), token))
                assert revised.status_code == 200, revised.text
                assert revised.json()['status'] == 'UNDER_REVIEW' and revised.json()['revision'] == 2

                # The third version is the last: a reviewer must then decide.
                async with kit.session() as session:
                    record = await session.scalar(select(TrajectoryClaim).where(TrajectoryClaim.id == uuid.UUID(claim['id'])))
                    record.revision = 3
                    await session.commit()
                last = await admin.post(f'/v1/admin/trajectories/{claim["id"]}/decision', headers=same_origin(),
                                        json=review_body(decision='NEEDS_CHANGES', revision=3,
                                                         state_version=revised.json()['state_version']))
                assert last.status_code == 409 and last.json()['reason_code'] == 'TRAJECTORY_LAST_REVISION'
        finally:
            await kit.teardown()
    asyncio.run(scenario())
