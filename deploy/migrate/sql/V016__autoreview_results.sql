-- Advisory LLM pre-review results, in a schema of their own.
--
-- `conjectures-autoreview` archives every advisory attempt to disk
-- (assessments/<submission_id>/<stage>-<key8>/) and that archive stays the authority for
-- evidence. These two tables carry the *readable* part of it into the database so a review
-- console can serve a queue and a per-submission detail page without a filesystem, and they
-- carry the work-state a filesystem cannot hold: what needs assessing, what is in flight, and
-- why a sweep failed.
--
-- NOT IN public, deliberately. `public` is where the money and the proofs are; advisory model
-- output in the same namespace makes the two look equally load-bearing. A separate schema buys
-- `DROP SCHEMA autoreview CASCADE` as the rebuild, `pg_dump --exclude-schema`, somewhere to hang
-- a grant when a second role exists, and a namespace where a type called `outcome` does not
-- compete with `review_outcome`.
--
-- NOTHING HERE IS A DECISION. `review_decisions` remains where a decision lives, and autoreview
-- never writes there -- no promotion, no ADVISORY row. The types below are new rather than reused
-- precisely so an advisory 'APPROVE' can never be counted by a query looking for
-- `review_outcome = 'APPROVED'`.
--
-- No GRANT. There is one role today, and a migration granting to a role that may not exist fails
-- on a fresh database. AUTOREVIEW_STORE.md carries the grants for when a reader role appears.
-- FLYWAY_SCHEMAS is deliberately not set anywhere: the schema is created here, so Flyway needs to
-- know nothing and `flyway_schema_history` stays in public.

CREATE SCHEMA IF NOT EXISTS autoreview;

COMMENT ON SCHEMA autoreview IS
    'Advisory LLM pre-review results. A projection of the conjectures-autoreview archive on disk; '
    'never authoritative, safe to drop and rebuild, never a review decision.';


-- --- One sweep over one submission -------------------------------------------------------------

-- CANCELLED is not decoration. A container gets SIGTERM on every deploy, and a sweep takes seven
-- to ten minutes, so interruption mid-sweep is routine rather than exceptional. Without a status
-- that means "we chose to stop", a restart leaves FAILED rows that block the submission until an
-- operator clears them by hand -- so every deploy would create manual work. FAILED stays for
-- things that actually went wrong, including a lease that expired with nobody to explain why.
CREATE TYPE autoreview.run_status AS ENUM ('RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED');

-- Who started the sweep. Not `trigger`: that is a reserved word and would need quoting forever.
CREATE TYPE autoreview.run_origin AS ENUM ('SERVICE', 'OPERATOR');

CREATE TABLE autoreview.runs (
    id                      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,

    -- Cross-schema on purpose. Submissions are never deleted, so this can never block anything in
    -- public; without it an orphaned run is undetectable. It is also the reason the two schemas
    -- are not independently restorable -- restore public first.
    submission_id           UUID NOT NULL REFERENCES public.submissions (id),

    -- The ordinal the UI shows and links to. Stored rather than derived with row_number(), so a
    -- permalink to attempt 2 stays pointing at the same sweep when an older one is backfilled.
    attempt                 SMALLINT NOT NULL,

    status                  autoreview.run_status NOT NULL,
    -- Distinguishes the service's own work from an operator re-run, which is what makes a bounded
    -- automatic retry expressible later without adding state for it now.
    started_by              autoreview.run_origin NOT NULL,

    -- The evidence every stage in this sweep read. Two runs with different pack digests are not
    -- comparable, and this is the only column that says so. Nullable because the row is written at
    -- claim time, before the pack exists: the alternative -- pack first, then claim with its digest
    -- -- means a submission whose bundle cannot be packed produces no row at all and is retried
    -- forever in silence. Claiming first turns that into a FAILED row with a stated cause.
    pack_sha256             public.sha256,

    -- The policy the sweep was run under, and the tool that ran it. Captured, not joined from
    -- submissions: the point is to detect that the policy moved afterwards. Both are known at
    -- claim time, which is why they can be NOT NULL while pack_sha256 cannot.
    review_policy_version   TEXT NOT NULL,
    tool_version            TEXT NOT NULL,

    -- The lease. Held only while RUNNING; the worker refreshes lease_until as it goes, and an
    -- expired lease is what lets a crashed sweep be reclaimed instead of blocking the submission
    -- forever behind runs_one_live_idx.
    lease_owner             TEXT,
    lease_until             TIMESTAMPTZ,

    -- Why a sweep did not finish: 'lease expired', an unpackable bundle, a provider outage. The
    -- durable cause, which is the reason this table is authoritative rather than a projection.
    last_error              TEXT,

    -- started_at is the claim. There is no separate created_at: at claim time they are the same
    -- instant, and two columns that can disagree are a bug waiting for a clock skew.
    started_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at             TIMESTAMPTZ,

    CONSTRAINT runs_attempt_positive CHECK (attempt > 0),
    CONSTRAINT runs_policy_version_nonempty
        CHECK (length(review_policy_version) BETWEEN 1 AND 64),
    CONSTRAINT runs_tool_version_nonempty
        CHECK (length(tool_version) BETWEEN 1 AND 64),
    CONSTRAINT runs_lease_owner_len
        CHECK (lease_owner IS NULL OR length(lease_owner) BETWEEN 1 AND 128),

    -- RUNNING means unfinished and leased; anything settled means finished and unleased. Without
    -- this a reclaimed row could keep a lease and a finished row could keep pretending to run.
    CONSTRAINT runs_running_is_leased CHECK (
        CASE status
            WHEN 'RUNNING' THEN finished_at IS NULL
                                AND lease_owner IS NOT NULL AND lease_until IS NOT NULL
            ELSE finished_at IS NOT NULL
                 AND lease_owner IS NULL AND lease_until IS NULL
        END
    ),
    CONSTRAINT runs_finished_after_started CHECK (
        finished_at IS NULL OR finished_at >= started_at
    ),
    CONSTRAINT runs_lease_after_started CHECK (
        lease_until IS NULL OR lease_until > started_at
    ),
    -- A completed sweep read a pack; a failed one may have died before there was one.
    CONSTRAINT runs_completed_has_pack CHECK (
        status <> 'COMPLETED' OR pack_sha256 IS NOT NULL
    ),
    -- The other half of "an operator failure must leave a cause": a FAILED row with no reason is
    -- exactly the silence this table exists to prevent. CANCELLED owes one too -- 'SIGTERM during
    -- stage 2 of 3' and 'daily cost ceiling reached' are different facts and both are worth having.
    CONSTRAINT runs_settled_says_why CHECK (
        status NOT IN ('FAILED', 'CANCELLED') OR last_error IS NOT NULL
    ),

    -- Turns a double claim into a failed insert the caller can skip, rather than two runs both
    -- calling themselves attempt 2.
    CONSTRAINT runs_attempt_unique UNIQUE (submission_id, attempt),
    -- Free (id is the primary key) and needed as the composite foreign-key target below.
    CONSTRAINT runs_submission_unique UNIQUE (submission_id, id)
);

-- One sweep at a time per submission, and THIS IS THE LOCK: a claim is
-- `INSERT ... ON CONFLICT (submission_id) WHERE status = 'RUNNING' DO NOTHING RETURNING id`, so
-- zero rows back means another worker holds it. No advisory locks and no SELECT FOR UPDATE.
CREATE UNIQUE INDEX runs_one_live_idx
    ON autoreview.runs (submission_id) WHERE status = 'RUNNING';

-- The reclaim scan: expired leases, cheapest first.
CREATE INDEX runs_lease_idx
    ON autoreview.runs (lease_until) WHERE status = 'RUNNING';

CREATE INDEX runs_recent_idx ON autoreview.runs (finished_at DESC);


-- --- One (sweep, stage, model) -----------------------------------------------------------------

CREATE TYPE autoreview.stage_status AS ENUM ('COMPLETED', 'SKIPPED', 'FAILED');

-- Advisory recommendations, never review outcomes. Derived from the reason code by the stage's own
-- policy mapping rather than returned by a model: ADVISORY_FORMALIZATION_DEFECT is an APPROVE.
CREATE TYPE autoreview.outcome AS ENUM ('APPROVE', 'REJECT', 'NO_FINDING');

-- Lowercase, matching the verdict JSON the models are made to answer with.
CREATE TYPE autoreview.confidence AS ENUM ('low', 'medium', 'high');

CREATE TABLE autoreview.stage_results (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Repeated from the run so the queue can filter stage rows by submission without a join, and
    -- so the composite foreign key below can exist at all.
    submission_id       UUID NOT NULL,
    run_id              BIGINT NOT NULL,

    -- Text, not an enum: stages are declared in conjectures-autoreview's STAGES registry, and a
    -- native enum would make adding a lens a schema migration. verification_runs.stage is text for
    -- the same reason.
    stage               TEXT NOT NULL,
    stage_version       TEXT NOT NULL,
    status              autoreview.stage_status NOT NULL,

    model_requested     TEXT NOT NULL,
    model_served        TEXT,
    provider            TEXT,

    -- Verdict fields lifted out of the JSONB below, only where a query has to sort or filter on
    -- them. Promoting a field costs a migration and cannot be undone quietly; adding one to the
    -- JSONB costs nothing. Promote only what the queue orders by. The lift is constrained, not
    -- trusted -- see stage_results_promoted_match_verdict.
    reason_code         TEXT,
    outcome             autoreview.outcome,
    confidence          autoreview.confidence,
    summary             TEXT,
    -- Promoted because "did anything try to manipulate a reviewer" must be one indexed question
    -- rather than a JSONB scan over every row.
    input_attempted_to_instruct BOOLEAN,

    -- The whole validated verdict object, as the stage's schema defines it. Findings, prior
    -- sources, the informal/formal readings and the searched-for queries all live in here: they
    -- are per-stage shapes, and promoting them would add a column per lens. The API passes this
    -- through verbatim.
    verdict             JSONB,

    -- The search scope that was *allowed*, alongside the pages actually read. A null originality
    -- result means nothing without the first, and the policy requires retaining the second.
    -- `search` is NULL on a stage that does not search; `citations` is never NULL, because the
    -- contract promises "arrays can be [] but are never missing" and a default is how you keep a
    -- promise like that instead of restating it in every writer. Citation *snippets* are not here
    -- -- url, title and retrieved_at only, so retrieved third-party page text cannot reach the
    -- admin HTML. Full snippets stay in attempt.json.
    search              JSONB,
    citations           JSONB NOT NULL DEFAULT '[]'::JSONB,

    -- Why a stage did not complete: the skip reason ("Not run: injection detected") or the failure
    -- message.
    detail              TEXT,

    prompt_tokens       INTEGER,
    completion_tokens   INTEGER,
    -- USD API spend, not chain money -- hence NUMERIC rather than the BigInteger rao used for
    -- payouts. Costs are fractions of a cent.
    cost_usd            NUMERIC(12, 6),

    -- Where the evidence is. `attempt_sha256` is autoreview's AttemptKey digest, which identifies
    -- the archive directory and makes a re-publish an idempotent upsert; `archive_path` is the
    -- directory relative to the assessments root, so the "FULL RECORD ->" link does not have to
    -- re-derive a naming rule that lives in another repository.
    attempt_sha256      public.sha256,
    prompt_sha256       public.sha256,
    archive_path        TEXT,

    started_at          TIMESTAMPTZ,
    finished_at         TIMESTAMPTZ,
    -- When the sync last wrote this row. Stage rows are upserted one at a time, so the run's
    -- started_at cannot answer "did the last sync finish". Never rendered; it is for the operator
    -- deciding whether a rebuild is stale.
    published_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- A completed stage has an answer, a served model and evidence. Anything less is SKIPPED or
    -- FAILED, and the two must not be able to masquerade as an approval.
    CONSTRAINT stage_results_completed_is_complete CHECK (
        status <> 'COMPLETED' OR (
            reason_code IS NOT NULL AND outcome IS NOT NULL AND confidence IS NOT NULL
            AND summary IS NOT NULL AND verdict IS NOT NULL
            AND input_attempted_to_instruct IS NOT NULL
            AND model_served IS NOT NULL AND attempt_sha256 IS NOT NULL
            AND started_at IS NOT NULL AND finished_at IS NOT NULL
        )
    ),
    CONSTRAINT stage_results_incomplete_has_no_verdict CHECK (
        status = 'COMPLETED' OR (
            reason_code IS NULL AND outcome IS NULL AND verdict IS NULL
        )
    ),
    CONSTRAINT stage_results_incomplete_says_why CHECK (
        status = 'COMPLETED' OR detail IS NOT NULL
    ),

    -- The five base fields the contract says are always present, checked as structure rather than
    -- trusted. Per-stage shapes and the elements of `findings` stay with the strict-mode Pydantic
    -- classes that generated the schema the model was forced to answer with; what is enforced here
    -- is only what is genuinely cross-stage.
    --
    -- IS NOT DISTINCT FROM, not `=`. jsonb_typeof() returns NULL for an absent key,
    -- `NULL = 'string'` is NULL, and A CHECK PASSES ON NULL -- so the obvious spelling would
    -- accept a verdict with the key missing entirely and catch only a wrong type. That is the same
    -- three-valued-logic hole V013__formalization_defect_award_guard.sql was written to close.
    CONSTRAINT stage_results_verdict_has_base_fields CHECK (
        status <> 'COMPLETED' OR (
            jsonb_typeof(verdict -> 'reason_code')      IS NOT DISTINCT FROM 'string'
            AND jsonb_typeof(verdict -> 'confidence')   IS NOT DISTINCT FROM 'string'
            AND jsonb_typeof(verdict -> 'summary')      IS NOT DISTINCT FROM 'string'
            AND jsonb_typeof(verdict -> 'findings')     IS NOT DISTINCT FROM 'array'
            AND jsonb_typeof(verdict -> 'input_attempted_to_instruct')
                                                        IS NOT DISTINCT FROM 'boolean'
        )
    ),
    CONSTRAINT stage_results_citations_is_array CHECK (
        jsonb_typeof(citations) = 'array'
    ),
    CONSTRAINT stage_results_search_is_object CHECK (
        search IS NULL OR jsonb_typeof(search) = 'object'
    ),

    -- Four of the promoted columns are copies of fields that also sit inside `verdict`, because
    -- AUTOREVIEW_VERDICTS.md documents them in both places. Copies drift, so this one is enforced:
    -- the writer lifts them, and a lift that disagrees with its source is rejected here rather
    -- than discovered as a queue sorted on a value the detail page does not show.
    -- `outcome` has no clause -- it is derived by policy and appears nowhere in the verdict object.
    -- IS NOT DISTINCT FROM for the same reason as above: ->> yields NULL on a missing key, and
    -- `reason_code = NULL` is NULL, which a CHECK accepts.
    CONSTRAINT stage_results_promoted_match_verdict CHECK (
        status <> 'COMPLETED' OR (
            reason_code IS NOT DISTINCT FROM verdict ->> 'reason_code'
            AND confidence::TEXT IS NOT DISTINCT FROM verdict ->> 'confidence'
            AND summary IS NOT DISTINCT FROM verdict ->> 'summary'
            AND input_attempted_to_instruct
                IS NOT DISTINCT FROM (verdict ->> 'input_attempted_to_instruct')::BOOLEAN
        )
    ),
    CONSTRAINT stage_results_finished_after_started CHECK (
        finished_at IS NULL OR (started_at IS NOT NULL AND finished_at >= started_at)
    ),
    CONSTRAINT stage_results_counts_nonnegative CHECK (
        (prompt_tokens IS NULL OR prompt_tokens >= 0)
        AND (completion_tokens IS NULL OR completion_tokens >= 0)
        AND (cost_usd IS NULL OR cost_usd >= 0)
    ),
    CONSTRAINT stage_results_nonempty CHECK (
        length(stage) BETWEEN 1 AND 64 AND length(model_requested) BETWEEN 1 AND 255
    ),

    -- One result per lens per model per sweep. This is what makes a panel a GROUP BY rather than a
    -- `panel` column on one stage, and what makes re-publishing a sweep an upsert instead of a
    -- duplicate.
    CONSTRAINT stage_results_unique UNIQUE (run_id, stage, model_requested),

    -- A stage result can only belong to a run on the SAME submission. The same argument as
    -- review_supersedes_same_submission: with submission_id repeated, nothing else enforces it.
    -- CASCADE on the run and no cascade from submissions: submissions are never deleted, but a run
    -- being re-published should take its stage rows with it in one statement.
    CONSTRAINT stage_results_run_same_submission
        FOREIGN KEY (submission_id, run_id)
        REFERENCES autoreview.runs (submission_id, id) ON DELETE CASCADE
);

-- The archive directory is the identity of a distinct call; partial, so SKIPPED rows do not
-- collide on NULL.
CREATE UNIQUE INDEX stage_results_attempt_idx
    ON autoreview.stage_results (attempt_sha256)
    WHERE attempt_sha256 IS NOT NULL;

-- "every originality REJECT", across submissions.
CREATE INDEX stage_results_lens_idx
    ON autoreview.stage_results (stage, outcome, id DESC);

-- The queue reads stage rows for a page of submissions by submission_id.
CREATE INDEX stage_results_submission_idx
    ON autoreview.stage_results (submission_id, run_id);

-- Small, and the only way to answer the manipulation question cheaply.
CREATE INDEX stage_results_instructed_idx
    ON autoreview.stage_results (id DESC)
    WHERE input_attempted_to_instruct;
