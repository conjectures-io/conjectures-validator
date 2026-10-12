from __future__ import annotations

import asyncio
import datetime as dt
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from conftest import postgres_dsn, DATABASE_SKIP_REASON
from test_payout_notifier import seed_eligible_submission

from conjectures_subnet.db.engine import (create_db_engine, session_factory,
    create_async_db_engine, async_session_factory)
from conjectures_subnet.db.errors import RecordConflict, RecordNotFound
from conjectures_subnet.db.models import (Account, Base, REVIEWER_ROLE, RewardEvent,
    ReviewDecision, ReviewOutcome, RewardState, Submission, PayoutInstructionAction,
    PayoutState, PayoutDiscordDelivery, TreasuryPayout)
from conjectures_subnet.db.payout_instructions import (
    cancel_and_withdraw, change_instruction, instruction_enabled, validate_chain_evidence)
from conjectures_subnet.db.submissions import correct_human_decision
from payout_notifier.worker import PayoutNotifier
from scripts.generate_payout_commands import _query


def evidence(**changes):
    return {"checked_at":dt.datetime.now(dt.UTC).isoformat(), "network":"finney",
        "multisig_address":"fixture-multisig", "pending_multisig_count":0,
        "no_matching_chain_transfer":True, "finalized_block_hash":"0xfixture", **changes}


@pytest.mark.parametrize("change", [
    {"pending_multisig_count":1}, {"pending_multisig_count":False},
    {"no_matching_chain_transfer":False}, {"network":"test"},
    {"finalized_block_hash":""}, {"checked_at":"bad"},
    {"checked_at":(dt.datetime.now(dt.UTC)-dt.timedelta(minutes=5)).isoformat()},
    {"checked_at":(dt.datetime.now(dt.UTC)+dt.timedelta(minutes=5)).isoformat()},
])
def test_unclear_or_stale_chain_check_refused(change):
    with pytest.raises(RecordConflict):
        validate_chain_evidence(evidence(**change), now=dt.datetime.now(dt.UTC))


@pytest.fixture
def kit():
    if postgres_dsn() is None:
        pytest.skip(DATABASE_SKIP_REASON)
    engine=create_db_engine(postgres_dsn())
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    sessions=session_factory(engine)
    sid=seed_eligible_submission(sessions)
    reviewer=uuid.uuid4()
    with sessions.begin() as s:
        s.add(Account(id=reviewer, email="payout-reviewer@example.com", roles=[REVIEWER_ROLE]))
    notifier=PayoutNotifier(sessions=sessions, webhook_url="", worker_id="fixture")
    assert notifier.seed_reward_events()==1
    with sessions() as s:
        event=s.scalar(select(RewardEvent))
        decision=s.scalar(select(ReviewDecision))
        request=dict(reward_event_id=event.id, submission_id=sid, expected_review_id=decision.id,
            expected_action_id=None, reviewer=str(reviewer), reason="Established prior proof",
            chain_evidence=evidence())
    yield sessions, notifier, request
    engine.dispose()


def command_rows(event_id):
    # Execute the real command-generator SELECT through the fixture's verified connection.
    engine=create_db_engine(postgres_dsn())
    try:
        with engine.connect() as c:
            sql=_query([event_id]).removeprefix("BEGIN READ ONLY;\n").removesuffix("ROLLBACK;\n")
            return c.execute(text(sql)).all()
    finally:
        engine.dispose()


def run(operation):
    async def execute():
        engine=create_async_db_engine(postgres_dsn())
        try:
            async with async_session_factory(engine).begin() as s:
                return await operation(s)
        finally:
            await engine.dispose()
    return asyncio.run(execute())


def cancel(s, req):
    return cancel_and_withdraw(s, **req, reason_code="NOT_NOVEL", notes="Human authorized correction",
        notes_public="Established earlier proof of the exact problem; Lean result remains valid.")


def test_cancel_atomic_history_command_exclusion_and_explicit_restore(kit):
    sessions, notifier, req=kit
    action, rejected=run(lambda s:cancel(s,req))
    assert rejected.supersedes_id==req['expected_review_id']
    assert notifier.seed_reward_events()==0
    assert notifier.seed()==0
    assert notifier.claim() is None
    # Query used by the actual operational command generator, not a mock of its predicate.
    assert command_rows(req['reward_event_id'])==[]
    with sessions() as s:
        reward=s.get(RewardEvent,req['reward_event_id'])
        assert reward.status==PayoutState.PENDING and reward.amount_rao==1000
        assert s.get(Submission,req['submission_id']).reward_status==RewardState.INELIGIBLE
        assert len(s.scalars(select(ReviewDecision)).all())==2
        assert not s.scalar(select(instruction_enabled()).where(RewardEvent.id==reward.id))
    restore={**req, 'expected_review_id':rejected.id, 'expected_action_id':action.id,
             'chain_evidence':evidence()}
    with pytest.raises(RecordConflict,match="approved eligible"):
        run(lambda s:change_instruction(s,action="RESTORE",**restore))
    approved=run(lambda s:correct_human_decision(s,req['submission_id'],
        supersedes_id=rejected.id, decision=ReviewOutcome.APPROVED, reason_code="REVIEW_APPROVED",
        reviewer=req['reviewer'], notes="Contrary evidence resolved", notes_public="Reconsidered"))
    # Re-approval by itself must not make a cancelled instruction payable.
    assert command_rows(req['reward_event_id'])==[]
    restored=run(lambda s:change_instruction(s,action="RESTORE",**{
        **restore,'expected_review_id':approved.id,'chain_evidence':evidence()}))
    assert restored.id>action.id
    assert len(command_rows(req['reward_event_id']))==1
    with sessions.begin() as s:
        with pytest.raises(IntegrityError,match="append-only"):
            s.execute(text("UPDATE payout_instruction_actions SET reason='changed' WHERE id=:id"), {'id':action.id})


@pytest.mark.parametrize('change', [
    {'extrinsic_reference':'123-0-0'}, {'submitted_block':123}, {'failure_reason':'uncertain'},
    {'submitted_at':dt.datetime.now(dt.UTC)},
])
def test_transaction_history_blocks_cancellation(kit,change):
    sessions, _, req=kit
    with sessions.begin() as s:
        event=s.get(RewardEvent,req['reward_event_id'])
        for k,v in change.items(): setattr(event,k, event.created_at + dt.timedelta(seconds=1) if k == "submitted_at" else v)
    with pytest.raises(RecordConflict,match="transaction or failure"):
        run(lambda s:cancel(s,req))
    with sessions() as s:
        assert s.scalar(select(PayoutInstructionAction.id)) is None
        assert s.get(Submission,req['submission_id']).reward_status==RewardState.ELIGIBLE


def test_mapping_review_role_and_inflight_guards(kit):
    sessions, notifier, req=kit
    with pytest.raises(RecordNotFound):
        run(lambda s:cancel(s,{**req,'submission_id':uuid.uuid4()}))
    with pytest.raises(RecordConflict,match="review changed"):
        run(lambda s:cancel(s,{**req,'expected_review_id':9999}))
    with pytest.raises(RecordConflict,match="reviewer role"):
        run(lambda s:cancel(s,{**req,'reviewer':str(uuid.uuid4())}))
    notifier.seed()
    claimed=notifier.claim()
    assert claimed is not None
    with pytest.raises(RecordConflict,match="notification is in flight"):
        run(lambda s:cancel(s,req))


def test_standard_correction_cannot_skip_cancellation_and_failed_withdrawal_rolls_back(kit):
    sessions, _, req=kit
    with pytest.raises(RecordConflict):
        run(lambda s:correct_human_decision(s,req['submission_id'],
            supersedes_id=req['expected_review_id'], decision=ReviewOutcome.REJECTED,
            reason_code="NOT_NOVEL",reviewer=req['reviewer'],notes="why",notes_public="why"))
    # A second, unresolved instruction must still prevent withdrawal of this submission.
    with sessions.begin() as s:
        original=s.get(RewardEvent,req['reward_event_id'])
        s.add(RewardEvent(submission_id=original.submission_id, amount_rao=original.amount_rao,
            destination_coldkey=original.destination_coldkey, eligibility_reason="REVIEW_APPROVED",
            pricing_policy_version=original.pricing_policy_version, pricing_inputs=original.pricing_inputs,
            initiated_by="fixture-second"))
    with pytest.raises(RecordConflict):
        run(lambda s:cancel(s,req))
    with sessions() as s:
        assert s.scalar(select(PayoutInstructionAction.id)) is None
        assert len(s.scalars(select(ReviewDecision)).all())==1


def test_observed_transfer_blocks_even_before_watcher_binding(kit):
    sessions, _, req=kit
    with sessions.begin() as s:
        event=s.get(RewardEvent,req['reward_event_id'])
        s.add(TreasuryPayout(extrinsic_reference="123-0-0",block=123,
            block_timestamp=dt.datetime.now(dt.UTC),extrinsic_index=0,event_index=0,
            origin_coldkey=event.destination_coldkey,origin_hotkey=event.destination_coldkey,
            destination_coldkey=event.destination_coldkey,destination_hotkey=event.destination_coldkey,
            origin_netuid=66,destination_netuid=66,amount_rao=event.amount_rao))
    with pytest.raises(RecordConflict,match="matching treasury transfer"):
        run(lambda s:cancel(s,req))


@pytest.mark.parametrize('state',[PayoutState.SUBMITTED,PayoutState.CONFIRMED,PayoutState.FAILED])
def test_nonpending_payout_blocks(kit,state):
    sessions, _, req=kit
    with sessions.begin() as s:
        event=s.get(RewardEvent,req['reward_event_id'])
        event.status=state
        if state==PayoutState.FAILED:
            event.failure_reason="unresolved attempt"
        else:
            event.extrinsic_reference="123-0-0";event.submitted_block=123
            event.submitted_at=event.created_at;event.chain_observed=True
            if state==PayoutState.CONFIRMED:
                event.finalized_block=124;event.confirmed_at=event.created_at
    with pytest.raises(RecordConflict,match="transaction or failure"):
        run(lambda s:cancel(s,req))


def test_stale_action_and_repeated_cancellation_refused(kit):
    _, _, req=kit
    with pytest.raises(RecordConflict,match="instruction changed"):
        run(lambda s:cancel(s,{**req,'expected_action_id':999}))
    first=run(lambda s:change_instruction(s,action="CANCEL",**req))
    with pytest.raises(RecordConflict,match="instruction changed"):
        run(lambda s:cancel(s,req))
    with pytest.raises(RecordConflict,match="already has"):
        run(lambda s:change_instruction(s,action="CANCEL",**{**req,'expected_action_id':first.id}))
