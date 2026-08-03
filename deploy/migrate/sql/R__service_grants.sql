-- Desired-state grants. Roles are created by deploy/db/00_init.sh on the
-- managed deployment. Conditional blocks keep the schema portable to scratch
-- PostgreSQL instances used by the ORM drift check.

DO $$
BEGIN
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'conjectures_api') THEN
        REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM conjectures_api;
        REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM conjectures_api;
        REVOKE ALL PRIVILEGES ON SCHEMA public FROM conjectures_api;
        GRANT USAGE ON SCHEMA public TO conjectures_api;
        GRANT SELECT ON submissions, verification_runs, review_decisions,
            reward_events, problem_winners, submission_events, api_rejection_log
            TO conjectures_api;
        GRANT SELECT, INSERT ON proofs TO conjectures_api;
        GRANT INSERT ON submissions, submission_events, api_rejection_log TO conjectures_api;
        GRANT USAGE, SELECT ON SEQUENCE submission_events_id_seq,
            api_rejection_log_id_seq TO conjectures_api;
    END IF;

    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'conjectures_verifier') THEN
        REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM conjectures_verifier;
        REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM conjectures_verifier;
        REVOKE ALL PRIVILEGES ON SCHEMA public FROM conjectures_verifier;
        GRANT USAGE ON SCHEMA public TO conjectures_verifier;
        GRANT SELECT ON proofs, submissions, verification_runs, review_decisions,
            problem_winners, submission_events TO conjectures_verifier;
        GRANT INSERT ON verification_runs, review_decisions, problem_winners,
            submission_events TO conjectures_verifier;
        GRANT UPDATE (
            verification_status, manual_review_status, reward_status, failure_reason,
            verification_attempts, verification_lease_owner,
            verification_lease_expires_at, verification_next_attempt_at
        ) ON submissions TO conjectures_verifier;
        GRANT USAGE, SELECT ON SEQUENCE verification_runs_id_seq,
            review_decisions_id_seq, submission_events_id_seq TO conjectures_verifier;
    END IF;

    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'conjectures_reviewer') THEN
        REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM conjectures_reviewer;
        REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM conjectures_reviewer;
        REVOKE ALL PRIVILEGES ON SCHEMA public FROM conjectures_reviewer;
        GRANT USAGE ON SCHEMA public TO conjectures_reviewer;
        GRANT SELECT ON submissions, verification_runs, review_decisions,
            reward_events, problem_winners, submission_events TO conjectures_reviewer;
        GRANT INSERT ON review_decisions, problem_winners, submission_events
            TO conjectures_reviewer;
        GRANT UPDATE (manual_review_status, reward_status, failure_reason)
            ON submissions TO conjectures_reviewer;
        GRANT USAGE, SELECT ON SEQUENCE review_decisions_id_seq,
            submission_events_id_seq TO conjectures_reviewer;
    END IF;

    IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'conjectures_reward') THEN
        REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM conjectures_reward;
        REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM conjectures_reward;
        REVOKE ALL PRIVILEGES ON SCHEMA public FROM conjectures_reward;
        GRANT USAGE ON SCHEMA public TO conjectures_reward;
        GRANT SELECT ON submissions, problem_winners, reward_events, submission_events
            TO conjectures_reward;
        GRANT INSERT ON reward_events TO conjectures_reward;
        GRANT UPDATE (
            status, extrinsic_reference, finalized_block, failure_reason, confirmed_at
        ) ON reward_events TO conjectures_reward;
        GRANT INSERT ON submission_events TO conjectures_reward;
        GRANT UPDATE (reward_status, failure_reason) ON submissions TO conjectures_reward;
        GRANT USAGE, SELECT ON SEQUENCE reward_events_id_seq,
            submission_events_id_seq TO conjectures_reward;
    END IF;
END;
$$;
