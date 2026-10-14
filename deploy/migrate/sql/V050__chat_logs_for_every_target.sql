-- Owner decision, October 13, 2026: every reward target requires accepted chat logs with its
-- proof, not only targets admitted after chat-log intake opened. Existing targets move to the
-- chat-log policy but keep their opening time and starting share, so their bounty amounts are
-- unchanged; only the evidence requirement changes. Accepted submissions keep the bounty policy
-- recorded on them, so nothing already submitted is affected.
--
-- A target's policy is otherwise immutable (V046). This one-time move is the only exception: the
-- guard is off for this transaction alone, and the move is only ever onto the stricter policy.
ALTER TABLE bounty_tasks DISABLE TRIGGER bounty_target_policy_immutable;
UPDATE bounty_tasks SET pricing_policy = 'linear-age-v3-chat-logs' WHERE pricing_policy = 'legacy';
ALTER TABLE bounty_tasks ENABLE TRIGGER bounty_target_policy_immutable;
