-- Cancellation suppresses unsigned instructions without deleting the economic record.
-- Restoration is another action, never a rewrite of history. No chain fact is altered.
CREATE TABLE payout_instruction_actions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reward_event_id BIGINT NOT NULL REFERENCES reward_events(id),
    action TEXT NOT NULL CONSTRAINT payout_instruction_action_valid CHECK (action IN ('CANCEL', 'RESTORE')),
    actor TEXT NOT NULL CONSTRAINT payout_instruction_actor_nonempty CHECK (length(actor) BETWEEN 1 AND 256),
    reason TEXT NOT NULL CONSTRAINT payout_instruction_reason_nonempty CHECK (length(reason) BETWEEN 1 AND 20000),
    review_decision_id BIGINT NOT NULL REFERENCES review_decisions(id),
    chain_evidence JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX payout_instruction_actions_event_idx ON payout_instruction_actions(reward_event_id, id DESC);
CREATE FUNCTION payout_instruction_actions_immutable() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'payout instruction actions are append-only' USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER payout_instruction_actions_immutable BEFORE UPDATE OR DELETE ON payout_instruction_actions
FOR EACH ROW EXECUTE FUNCTION payout_instruction_actions_immutable();
