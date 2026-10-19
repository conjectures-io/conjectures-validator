-- Owner decision, October 14, 2026: a reviewer may ask for more of a bounty's required chat logs
-- instead of rejecting them, and the contributor may then upload a new version, up to the three
-- versions every claim already allows (trajectory_revision_bound). A deposit, made before its
-- proof exists, is still always the first version.
ALTER TABLE trajectory_claims DROP CONSTRAINT trajectory_required_logs_fixed;
ALTER TABLE trajectory_claims ADD CONSTRAINT trajectory_deposit_first_version
    CHECK (status <> 'DEPOSITED' OR revision = 1);
