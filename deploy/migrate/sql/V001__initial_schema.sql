-- Requires PostgreSQL 13+ (gen_random_uuid() in core).

CREATE DOMAIN sha256 AS BYTEA CHECK (octet_length(VALUE) = 32);   -- raw 32 bytes, never 'sha256:<hex>'
CREATE DOMAIN ss58 AS TEXT CHECK (VALUE ~ '^[1-9A-HJ-NP-Za-km-z]{48}$');   -- Bittensor address, prefix 42

CREATE TYPE task_mode AS ENUM (
    'formalized', 'counterexample'
);

CREATE TYPE verification_state AS ENUM (
    'UNVERIFIED', 'REJECTED', 'VERIFIED'
);

CREATE TYPE manual_review_state AS ENUM (
    'UNREVIEWED', 'REJECTED', 'APPROVED'
);

CREATE TYPE reward_state AS ENUM (
    'INELIGIBLE', 'ELIGIBLE', 'REWARDED', 'FAILED'
);

-- Separate from submissions so it can be made write-once: REVOKE UPDATE, DELETE
-- from the service role and the bytes we verified physically cannot be rewritten.
CREATE TABLE proofs (
    digest      sha256 PRIMARY KEY,
    content     BYTEA NOT NULL,                                -- miner's Main.lean, UTF-8 Lean source
    byte_length INTEGER NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- pg_catalog-qualified: bare sha256(...) could read as a cast to the domain above.
    CONSTRAINT proof_digest_matches CHECK (digest = pg_catalog.sha256(content)),
    CONSTRAINT proof_length_matches CHECK (byte_length = octet_length(content))
);

CREATE TABLE submissions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hotkey                  ss58 NOT NULL,                                 -- submitting miner
    idempotency_key         UUID NOT NULL,                                 -- SUBNET.md:128, client-generated, one attempt
    request_digest          sha256 NOT NULL,                               -- canonical request; tells replay from conflict
    task_id                 TEXT NOT NULL                                  -- manifest task id; no FK, the task repo is the source of truth
        CONSTRAINT task_id_nonempty CHECK (length(task_id) BETWEEN 1 AND 255),
    task_bundle_sha256      sha256 NOT NULL,                               -- exact bundle this proof was written against
    problem_id              TEXT NOT NULL                                  -- the conjecture; its proof task and counterexample task share it
        CONSTRAINT problem_id_nonempty CHECK (length(problem_id) BETWEEN 1 AND 255),
    task_mode               task_mode NOT NULL,                            -- both derived server-side from the allowlist, never sent by the miner

    proof_digest            sha256 NOT NULL UNIQUE REFERENCES proofs (digest),  -- drop the UNIQUE if identical proofs should ever both be payable.

    -- Payment confirmation data
    payment_reference       TEXT NOT NULL                                  -- finalized extrinsic id
        CONSTRAINT payment_reference_nonempty CHECK (length(payment_reference) BETWEEN 1 AND 128),
    payment_sender          ss58 NOT NULL,                                 -- coldkey; must own the submitting hotkey
    payment_amount_rao      BIGINT NOT NULL                                -- integer RAO, never float (SUBNET.md:168)
        CONSTRAINT payment_amount_positive CHECK (payment_amount_rao > 0),
    payment_block           BIGINT NOT NULL                                -- finalized block it was observed in
        CONSTRAINT payment_block_positive CHECK (payment_block > 0),
    hotkey_signature        BYTEA NOT NULL                                 -- sr25519 by hotkey over request_digest; binds miner to this payment
        CONSTRAINT hotkey_signature_len CHECK (octet_length(hotkey_signature) = 64),

    verification_status     verification_state NOT NULL DEFAULT 'UNVERIFIED',
    manual_review_status    manual_review_state NOT NULL DEFAULT 'UNREVIEWED',  -- whether a review is needed comes from manual_review_required
    reward_status           reward_state NOT NULL DEFAULT 'INELIGIBLE',
    failure_reason          TEXT,                                          -- ReasonCode where one applies; ordering rules live in the service

    manual_review_required  BOOLEAN NOT NULL DEFAULT true,
    review_policy_version   TEXT NOT NULL
        CONSTRAINT review_policy_version_nonempty CHECK (length(review_policy_version) BETWEEN 1 AND 64),

    bounty_amount_rao       BIGINT NOT NULL                                -- alpha base units, NOT the TAO rao of payment_amount_rao above
        CONSTRAINT bounty_amount_positive CHECK (bounty_amount_rao > 0),
    bounty_policy_version   TEXT NOT NULL                                  -- the pricing rule that produced the amount, as review_policy_version is for review
        CONSTRAINT bounty_policy_version_nonempty CHECK (length(bounty_policy_version) BETWEEN 1 AND 64),
    bounty_inputs           JSONB,                                         -- what the rule read: pool balance, task age, any rate. Makes the quote reproducible instead of asserted.

    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT submissions_idempotency_unique UNIQUE (hotkey, idempotency_key),  -- SUBNET.md:154, per miner, not global
    CONSTRAINT submissions_payment_reference_unique UNIQUE (payment_reference),  -- SUBNET.md:155, one transfer backs one submission
    CONSTRAINT updated_not_before_created CHECK (updated_at >= created_at)
);


-- Worker queues, oldest first, for FOR UPDATE SKIP LOCKED. Partial, so only rows
-- still awaiting work are indexed and the indexes stay small as history grows.
CREATE INDEX submissions_verification_queue_idx ON submissions (created_at)
    WHERE verification_status = 'UNVERIFIED';
CREATE INDEX submissions_review_queue_idx ON submissions (created_at)
    WHERE verification_status = 'VERIFIED' AND manual_review_status = 'UNREVIEWED';
CREATE INDEX submissions_reward_queue_idx ON submissions (created_at)
    WHERE reward_status = 'ELIGIBLE';

CREATE INDEX submissions_task_idx ON submissions (task_id, created_at DESC);
CREATE INDEX submissions_hotkey_idx ON submissions (hotkey, created_at DESC);

CREATE UNIQUE INDEX submissions_problem_reward_unique ON submissions (problem_id)
    WHERE reward_status <> 'INELIGIBLE';
CREATE INDEX submissions_problem_verified_idx ON submissions (problem_id, task_mode)
    WHERE verification_status = 'VERIFIED';
-- No proof_digest index: the UNIQUE above already builds one, and it also means a copied proof
-- is refused at INSERT rather than found afterwards, so there is nothing to scan for.

CREATE FUNCTION submissions_touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER submissions_touch_updated_at
    BEFORE UPDATE ON submissions
    FOR EACH ROW EXECUTE FUNCTION submissions_touch_updated_at();


CREATE TABLE verification_runs (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,   -- monotonic, so attempt order is derivable
    submission_id       UUID NOT NULL REFERENCES submissions (id),

    task_bundle_sha256  sha256 NOT NULL,                 -- re-derived from live Lean, not trusted from disk
    proof_digest        sha256 NOT NULL REFERENCES proofs (digest),

    verifier_version    TEXT NOT NULL,                   -- SUBNET.md:186
    container_digest    sha256 NOT NULL,                 -- immutable image that ran it
    sandbox_mode        TEXT NOT NULL,                   -- 'landrun' in production; anything else means no real isolation

    accepted            BOOLEAN NOT NULL,
    reason_code         TEXT NOT NULL,                   -- verifier/errors.py ReasonCode
    stage               TEXT NOT NULL,
    checks              JSONB,                           -- the 14 gate booleans; a projection of report, not the authority
    report              BYTEA,                           -- exact report bytes, so the digest stays recomputable; NULL if the run died first
    report_digest       sha256,

    started_at          TIMESTAMPTZ NOT NULL,
    finished_at         TIMESTAMPTZ NOT NULL,

    CONSTRAINT runs_report_paired CHECK ((report IS NULL) = (report_digest IS NULL)),
    CONSTRAINT runs_report_digest_matches
        CHECK (report IS NULL OR report_digest = pg_catalog.sha256(report)),
    CONSTRAINT runs_finished_after_started CHECK (finished_at >= started_at)
);

CREATE UNIQUE INDEX verification_runs_submission_idx ON verification_runs (submission_id, id);
CREATE INDEX verification_runs_reason_idx ON verification_runs (reason_code, finished_at DESC);   -- rejection rates by gate


CREATE TYPE payout_state AS ENUM (
    'PENDING', 'SUBMITTED', 'CONFIRMED', 'FAILED'
);

CREATE TABLE reward_events (
    id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    submission_id           UUID NOT NULL REFERENCES submissions (id),

    eligibility_reason      TEXT NOT NULL,                   -- 'REVIEW_APPROVED' or 'AUTO_REVIEW_DISABLED' (SUBNET.md:206)

    amount_rao              BIGINT NOT NULL                  -- alpha base units (1e-9); integer only, as SUBNET.md:168 requires of TAO
        CONSTRAINT reward_amount_positive CHECK (amount_rao > 0),

    -- Captured, not derived from submissions: this is where the money actually went,
    -- an external fact. Alpha is held as stake, so a transfer needs both keys.
    destination_coldkey     ss58 NOT NULL,                   -- normally submissions.payment_sender, already proven to own the hotkey
    destination_hotkey      ss58 NOT NULL,

    status                  payout_state NOT NULL DEFAULT 'PENDING',
    extrinsic_reference     TEXT,                            -- NULL until submitted
    submitted_block         BIGINT,
    finalized_block         BIGINT,
    failure_reason          TEXT,                            -- why this attempt failed; the next attempt is a new row

    initiated_by            TEXT NOT NULL,                   -- operator who ran the payout CLI, or 'system' once automated

    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    submitted_at            TIMESTAMPTZ,
    confirmed_at            TIMESTAMPTZ,

    -- Deduplication (SUBNET.md:208) is the CLI's job, not a constraint: it must query for an
    -- existing payout on this submission and refuse before signing anything.

    -- PENDING means exactly "no extrinsic exists yet", so status and reference must not drift
    -- apart: a SUBMITTED row without one would be indistinguishable from the unresolved case
    -- that has to block further payouts. FAILED is exempt — an attempt can die before broadcast.
    CONSTRAINT reward_submitted_needs_reference
        CHECK (status NOT IN ('SUBMITTED', 'CONFIRMED')
               OR (extrinsic_reference IS NOT NULL AND submitted_at IS NOT NULL)),
    CONSTRAINT reward_confirmed_needs_finality
        CHECK (status <> 'CONFIRMED' OR (finalized_block IS NOT NULL AND confirmed_at IS NOT NULL)),
    CONSTRAINT reward_failed_needs_reason
        CHECK (status <> 'FAILED' OR failure_reason IS NOT NULL),

    CONSTRAINT reward_blocks_positive
        CHECK ((submitted_block IS NULL OR submitted_block > 0)
               AND (finalized_block IS NULL OR finalized_block > 0)),
    CONSTRAINT reward_finalized_not_before_submitted
        CHECK (finalized_block IS NULL OR submitted_block IS NULL OR finalized_block >= submitted_block),
    CONSTRAINT reward_submitted_not_before_created CHECK (submitted_at IS NULL OR submitted_at >= created_at),
    CONSTRAINT reward_confirmed_after_submitted
        CHECK (confirmed_at IS NULL OR (submitted_at IS NOT NULL AND confirmed_at >= submitted_at))
);

-- Not the dedup key: one extrinsic cannot be two payouts, so this catches recording the
-- same transfer twice, while a genuine second transfer has its own reference and is allowed.
CREATE UNIQUE INDEX reward_events_extrinsic_idx ON reward_events (extrinsic_reference)
    WHERE extrinsic_reference IS NOT NULL;                   -- partial, so unpaid rows don't collide on NULL

-- UNIQUE is free (id is already the primary key) and leaves the pair available as a
-- composite foreign-key target.
CREATE UNIQUE INDEX reward_events_submission_idx ON reward_events (submission_id, id);   -- the CLI's pre-flight check, and attempt order
CREATE INDEX reward_events_pending_idx ON reward_events (created_at)
    WHERE status IN ('PENDING', 'SUBMITTED');                -- queue: pay, then poll for finality
CREATE INDEX reward_events_destination_idx ON reward_events (destination_coldkey, created_at DESC);


CREATE TYPE review_outcome AS ENUM (
    'APPROVED', 'REJECTED'
);

CREATE TYPE reviewer_kind AS ENUM (
    'HUMAN',        -- an authorised person; the only kind that may reject a Lean-valid proof
    'AUTOMATIC',    -- manual review was disabled, still recorded as a policy decision (SUBNET.md:241)
    'ADVISORY'      -- an LLM pre-check; recorded as evidence, never binding
);


CREATE TABLE review_decisions (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,   -- monotonic, so the latest decision is max(id)
    submission_id       UUID NOT NULL REFERENCES submissions (id),

    decision            review_outcome NOT NULL,
    kind                reviewer_kind NOT NULL,
    reviewer            TEXT NOT NULL                        -- operator identity, or the model id for ADVISORY
        CONSTRAINT reviewer_nonempty CHECK (length(reviewer) BETWEEN 1 AND 255),

    policy_version      TEXT NOT NULL,                       -- SUBNET.md:197, the review criteria in force
    reason_code         TEXT NOT NULL,                       -- structured, because a rejection is shown to the miner (SUBNET.md:236)
    notes               TEXT,                                -- free text for the audit trail, not the machine-readable reason
    evidence            JSONB,                               -- ADVISORY output kept as-is: model, verdict, and what it was asked

    -- Corrections chain instead of overwriting. The FK below is composite, so a decision can
    -- only supersede another decision on the SAME submission.
    supersedes_id       BIGINT,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Free (id is already the primary key). Declared in-table because the self-reference below
    -- needs the pair as a foreign-key target, and it doubles as the per-submission history
    -- index — btree scans backwards, so latest-first needs no DESC index.
    CONSTRAINT review_decisions_submission_unique UNIQUE (submission_id, id),

    CONSTRAINT review_supersedes_not_self CHECK (supersedes_id IS DISTINCT FROM id),
    CONSTRAINT review_supersedes_same_submission
        FOREIGN KEY (submission_id, supersedes_id) REFERENCES review_decisions (submission_id, id)
);
CREATE INDEX review_decisions_reviewer_idx ON review_decisions (reviewer, created_at DESC);
CREATE INDEX review_decisions_reason_idx ON review_decisions (reason_code, created_at DESC);   -- what reviewers actually reject for


CREATE TABLE api_rejection_log (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    reason_code         TEXT NOT NULL,                   -- PAYMENT_NOT_FINALIZED, SIGNATURE_INVALID, DUPLICATE_PROOF, IDEMPOTENCY_CONFLICT, ...
    http_status         SMALLINT,

    -- Claimed, never verified. If we had verified it, this would be a submission instead.
    hotkey_claimed      TEXT,
    idempotency_key     TEXT,                            -- TEXT, so a malformed key is logged rather than rejected
    task_id             TEXT,
    task_bundle_sha256  TEXT,                            -- hex as sent
    proof_digest        TEXT,                            -- hex, computed by us over what arrived
    proof_byte_length   INTEGER,                         -- size only; rejected proof bytes are not kept
    request_digest      TEXT,                            -- lets a support query match a miner's own record of the call
    payment_reference   TEXT,                            -- the reference they cited; the whole point when someone paid and was refused

    source_ip           INET,                            -- personal data, so the retention window is not optional
    user_agent          TEXT,
    detail              JSONB                            -- validation errors, observed vs expected amount, whatever explains the refusal
);

CREATE INDEX api_rejection_log_recent_idx ON api_rejection_log (occurred_at DESC);
CREATE INDEX api_rejection_log_reason_idx ON api_rejection_log (reason_code, occurred_at DESC);
CREATE INDEX api_rejection_log_hotkey_idx ON api_rejection_log (hotkey_claimed, occurred_at DESC)
    WHERE hotkey_claimed IS NOT NULL;
CREATE INDEX api_rejection_log_payment_idx ON api_rejection_log (payment_reference)
    WHERE payment_reference IS NOT NULL;                 -- "I paid in extrinsic X and got an error" support lookup
