-- Configuration is still external. These are explicitly published read projections.
CREATE TABLE api_profiles (
    profile_hash sha256_hex PRIMARY KEY,
    competition_id bigint NOT NULL REFERENCES competitions(id),
    snapshot_sha256 sha256_hex NOT NULL REFERENCES snapshots(sha256),
    policy jsonb NOT NULL CHECK (jsonb_typeof(policy)='object' AND octet_length(policy::text)<=131072)
);
CREATE TABLE api_runtime (
    competition_id bigint PRIMARY KEY REFERENCES competitions(id),
    profile_hash sha256_hex NOT NULL REFERENCES api_profiles(profile_hash),
    observed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE api_corpus (
    profile_hash sha256_hex NOT NULL REFERENCES api_profiles(profile_hash),
    id text NOT NULL, title text NOT NULL, group_name text NOT NULL, tier text NOT NULL,
    rounds integer NOT NULL CHECK(rounds>=3), input_sha256 sha256_hex NOT NULL,
    expected_outcome text NOT NULL CHECK(expected_outcome IN ('accepted','rejected')),
    source_project text NOT NULL, source_url text,
    PRIMARY KEY(profile_hash,id)
);
CREATE TABLE api_target_summaries (
    request_id bigint PRIMARY KEY REFERENCES benchmark_requests(id),
    quality_status text NOT NULL CHECK(quality_status IN ('usable','inconclusive-environment')),
    good_probe_fraction double precision NOT NULL CHECK(good_probe_fraction BETWEEN 0 AND 1),
    probes_recorded integer NOT NULL, probes_planned integer NOT NULL,
    challenger_baseline_speedup positive_ratio,
    incumbent_baseline_speedup positive_ratio
);
CREATE TABLE api_reward_policy (
    competition_id bigint PRIMARY KEY REFERENCES competitions(id),
    snapshot_sha256 sha256_hex NOT NULL REFERENCES snapshots(sha256),
    policy jsonb NOT NULL CHECK(jsonb_typeof(policy)='object' AND octet_length(policy::text)<=16384),
    observed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
-- Intake authorization belongs to the owner, never a request-controlled dev boolean.
CREATE TABLE api_intake_settings (
    competition_id bigint PRIMARY KEY REFERENCES competitions(id),
    dev_mode boolean NOT NULL,
    network text NOT NULL, netuid integer NOT NULL CHECK(netuid BETWEEN 0 AND 65535),
    minimum_registered_block bigint NOT NULL CHECK(minimum_registered_block>=0),
    max_chain_age_seconds integer NOT NULL CHECK(max_chain_age_seconds BETWEEN 1 AND 3600),
    max_source_bytes integer NOT NULL CHECK(max_source_bytes BETWEEN 1 AND 4194304),
    enabled boolean NOT NULL DEFAULT true
);
CREATE TABLE api_entitlements (
    competition_id bigint NOT NULL REFERENCES competitions(id),
    network text NOT NULL, netuid integer NOT NULL, uid integer NOT NULL,
    registered_block bigint NOT NULL, hotkey text NOT NULL,
    submission_id bigint NOT NULL UNIQUE REFERENCES submissions(id),
    PRIMARY KEY(competition_id,network,netuid,uid,registered_block,hotkey)
);
-- Preserve already consumed registrations, including several coldkey observations.
INSERT INTO api_entitlements
SELECT DISTINCT ON(s.competition_id,r.network,r.netuid,r.uid,r.registered_block,r.hotkey)
    s.competition_id,r.network,r.netuid,r.uid,r.registered_block,r.hotkey,s.id
FROM submissions s JOIN registrations r ON r.id=s.registration_id
ORDER BY s.competition_id,r.network,r.netuid,r.uid,r.registered_block,r.hotkey,s.id;
CREATE TABLE api_receipts (
    competition_id bigint NOT NULL REFERENCES competitions(id), hotkey text NOT NULL,
    request_id uuid NOT NULL, source_sha256 sha256_hex NOT NULL,
    submission_id bigint NOT NULL UNIQUE REFERENCES submissions(id),
    PRIMARY KEY(competition_id,hotkey,request_id)
);
CREATE INDEX submissions_hotkey_feed ON submissions(competition_id,hotkey,id DESC);
CREATE INDEX evaluations_submission_feed ON evaluations(submission_id,id DESC);
CREATE INDEX attempts_request_feed ON benchmark_attempts(request_id,id DESC);
CREATE INDEX measurements_attempt_feed ON measurements(attempt_id,id);

CREATE VIEW api_promotions AS
SELECT p.id,p.competition_id,p.evaluation_id,p.promoted_at,e.submission_id,
    s.hotkey,s.source_sha256,e.speedup_estimate,e.speedup_lower,e.speedup_upper,
    snap.payload->>'profile' AS profile_hash,
    p.id=(SELECT max(p2.id) FROM promotions p2 WHERE p2.competition_id=p.competition_id) AS current
FROM promotions p JOIN evaluations e ON e.id=p.evaluation_id
JOIN submissions s ON s.id=e.submission_id JOIN snapshots snap ON snap.sha256=e.coordinator_snapshot_sha256;
CREATE VIEW api_submissions AS
SELECT s.id,s.competition_id,s.hotkey,s.label,s.source_sha256,s.submitted_at,s.intake,
    EXISTS(SELECT 1 FROM api_promotions p WHERE p.submission_id=s.id) AS ever_promoted,
    EXISTS(SELECT 1 FROM api_promotions p WHERE p.submission_id=s.id AND p.current) AS current,
    EXISTS(SELECT 1 FROM api_promotions p WHERE p.submission_id=s.id) AND
    NOT EXISTS(SELECT 1 FROM api_promotions p WHERE p.competition_id=s.competition_id
      AND p.current AND p.source_sha256=s.source_sha256) AS source_available
FROM submissions s;
CREATE VIEW api_admissions AS
SELECT id,source_sha256,proof_identity,state,attempt_count,started_at,finished_at,
    elapsed_seconds,snapshot_sha256 FROM admissions;
CREATE VIEW api_phases AS
SELECT admission_id,attempt_number,phase,status,started_at,finished_at,elapsed_seconds,
    user_cpu_seconds,system_cpu_seconds,peak_rss_kib FROM admission_phases;
CREATE VIEW api_evaluations AS
SELECT e.id,e.competition_id,e.submission_id,e.admission_id,e.generation,e.state,e.outcome,
    e.coordinator_snapshot_sha256,e.baseline_source_sha256,e.incumbent_promotion_id,
    e.expected_targets,e.speedup_estimate,e.speedup_lower,e.speedup_upper,e.guard_passed,
    e.created_at,e.completed_at,s.payload->>'profile' AS profile_hash
FROM evaluations e JOIN snapshots s ON s.sha256=e.coordinator_snapshot_sha256;
CREATE VIEW api_requests AS
SELECT r.id,r.evaluation_id,r.target,r.state,r.available_at,r.created_at,r.finished_at,
    r.completed_attempt_id,r.rounds_completed,r.speedup_estimate,r.speedup_lower,r.speedup_upper,
    r.specification->'rounds' AS rounds,
    r.specification->'tiers' AS tiers
FROM benchmark_requests r;
CREATE VIEW api_attempts AS
SELECT a.id,a.request_id,a.attempt_number,a.state,a.failure_kind,a.started_at,a.finished_at,
    a.lease_expires_at,r.target,r.evaluation_id,e.competition_id
FROM benchmark_attempts a JOIN benchmark_requests r ON r.id=a.request_id
JOIN evaluations e ON e.id=r.evaluation_id;
CREATE VIEW api_builds AS
SELECT attempt_id,role,source_sha256,status,cache_hit,started_at,finished_at,elapsed_seconds,
    peak_rss_kib,artifact_sha256 FROM benchmark_builds;
CREATE VIEW api_measurements AS
SELECT id,attempt_id,phase,round_index,case_id,group_name,input_sha256,role,status,
    started_at,finished_at,wall_ns,cpu_ns,peak_rss_kib,check_and_render_ns,parse_ns,
    output_bytes,sandbox_seconds,returncode,outcome FROM measurements;
CREATE VIEW api_members AS
SELECT measurement_id,member_index,case_id,input_sha256,parse_ns,check_and_render_ns,
    output_bytes,outcome FROM measurement_members;
CREATE VIEW api_chain AS SELECT network,netuid,block,observed_at FROM chain_state;
CREATE VIEW api_weight_sets AS
SELECT id,competition_id,mode,status,block,epoch_block,champion_promotion_id,
    snapshot_sha256,created_at,finished_at FROM weight_sets;
CREATE VIEW api_allocations AS SELECT weight_set_id,uid,hotkey,weight,role FROM weight_allocations;

CREATE FUNCTION api_eligibility(slug text, hotkey text) RETURNS jsonb LANGUAGE plpgsql
SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE c bigint; cfg api_intake_settings; reg registrations; chain chain_state; used bigint;
BEGIN
    SELECT id INTO c FROM competitions WHERE competitions.slug=api_eligibility.slug;
    SELECT * INTO cfg FROM api_intake_settings WHERE competition_id=c;
    IF cfg.competition_id IS NULL THEN RETURN jsonb_build_object('error','POLICY_NOT_READY'); END IF;
    IF NOT cfg.enabled THEN RETURN jsonb_build_object('reason','submissions_closed'); END IF;
    IF cfg.dev_mode THEN RETURN jsonb_build_object('reason','registration_bypassed'); END IF;
    SELECT * INTO chain FROM chain_state WHERE network=cfg.network AND netuid=cfg.netuid;
    IF chain.observed_at IS NULL OR chain.observed_at<clock_timestamp()-make_interval(secs=>cfg.max_chain_age_seconds)
      OR chain.observed_at>clock_timestamp()+interval '5 seconds' THEN
        RETURN jsonb_build_object('reason','chain_unavailable');
    END IF;
    SELECT * INTO reg FROM registrations WHERE network=cfg.network AND netuid=cfg.netuid
      AND registrations.hotkey=api_eligibility.hotkey AND active;
    IF reg.id IS NULL THEN RETURN jsonb_build_object('reason','not_registered'); END IF;
    SELECT submission_id INTO used FROM api_entitlements WHERE competition_id=c AND network=reg.network
      AND netuid=reg.netuid AND uid=reg.uid AND registered_block=reg.registered_block
      AND api_entitlements.hotkey=reg.hotkey;
    RETURN jsonb_build_object('reason',CASE WHEN reg.registered_block<=cfg.minimum_registered_block
      THEN 'registration_before_cutoff' WHEN used IS NOT NULL THEN 'registration_used' ELSE 'eligible' END,
      'registration',jsonb_build_object('uid',reg.uid,'registered_block',reg.registered_block,
        'used_by_submission',used::text));
END $$;

CREATE FUNCTION api_enqueue(slug text, hotkey text, content text, request_id uuid, dev boolean)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE c bigint; cfg api_intake_settings; receipt api_receipts; reg registrations;
    digest text; sid bigint; eligible jsonb;
BEGIN
    SELECT id INTO c FROM competitions WHERE competitions.slug=api_enqueue.slug;
    SELECT * INTO cfg FROM api_intake_settings WHERE competition_id=c;
    IF cfg.competition_id IS NULL THEN RETURN jsonb_build_object('error','POLICY_NOT_READY'); END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('chain:'||cfg.network||':'||cfg.netuid,0));
    IF NOT cfg.enabled THEN RETURN jsonb_build_object('error','SUBMISSIONS_CLOSED'); END IF;
    IF cfg.dev_mode IS DISTINCT FROM dev THEN RETURN jsonb_build_object('error','INTAKE_MODE_MISMATCH'); END IF;
    IF hotkey IS NULL OR length(hotkey) NOT BETWEEN 2 AND 64 OR hotkey ~ '[[:cntrl:]]'
      OR request_id IS NULL OR content IS NULL THEN
        RETURN jsonb_build_object('error','MALFORMED_REQUEST');
    END IF;
    IF octet_length(content) NOT BETWEEN 1 AND cfg.max_source_bytes THEN
        RETURN jsonb_build_object('error','SUBMISSION_TOO_LARGE');
    END IF;
    digest:=encode(sha256(convert_to(content,'UTF8')),'hex');
    SELECT * INTO receipt FROM api_receipts WHERE competition_id=c
      AND api_receipts.hotkey=api_enqueue.hotkey AND api_receipts.request_id=api_enqueue.request_id;
    IF receipt.submission_id IS NOT NULL THEN
      IF receipt.source_sha256<>digest THEN RETURN jsonb_build_object('error','IDEMPOTENCY_CONFLICT'); END IF;
      RETURN jsonb_build_object('submission',receipt.submission_id::text,'replayed',true);
    END IF;
    IF NOT EXISTS(SELECT 1 FROM api_runtime WHERE competition_id=c) THEN
      RETURN jsonb_build_object('error','POLICY_NOT_READY');
    END IF;
    eligible:=api_eligibility(slug,hotkey);
    IF coalesce(eligible->>'reason','') NOT IN ('eligible','registration_bypassed') THEN
      RETURN jsonb_build_object('error',CASE eligible->>'reason'
        WHEN 'not_registered' THEN 'NOT_REGISTERED' WHEN 'registration_used' THEN 'NO_ENTITLEMENT'
        WHEN 'registration_before_cutoff' THEN 'REGISTRATION_BEFORE_CUTOFF' ELSE 'CHAIN_STALE' END);
    END IF;
    IF NOT dev THEN
      SELECT * INTO reg FROM registrations WHERE network=cfg.network AND netuid=cfg.netuid
        AND registrations.hotkey=api_enqueue.hotkey AND active;
    END IF;
    INSERT INTO sources(sha256,content) VALUES(digest,content) ON CONFLICT DO NOTHING;
    INSERT INTO submissions(competition_id,source_sha256,submitter,hotkey,registration_id,intake)
      VALUES(c,digest,hotkey,hotkey,reg.id,CASE WHEN dev THEN 'dev' ELSE 'api' END) RETURNING id INTO sid;
    IF NOT dev THEN
      INSERT INTO api_entitlements VALUES(c,reg.network,reg.netuid,reg.uid,reg.registered_block,hotkey,sid);
    END IF;
    INSERT INTO api_receipts VALUES(c,hotkey,request_id,digest,sid);
    RETURN jsonb_build_object('submission',sid::text,'replayed',false);
END $$;

CREATE FUNCTION api_source(competition bigint, submission bigint) RETURNS text LANGUAGE sql
SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
    SELECT src.content FROM sources src JOIN api_submissions s ON s.source_sha256=src.sha256
    WHERE s.id=submission AND s.competition_id=competition AND s.source_available
$$;

REVOKE ALL ON api_profiles,api_runtime,api_corpus,api_target_summaries,api_reward_policy,
    api_intake_settings,api_entitlements,api_receipts FROM PUBLIC;
REVOKE ALL ON api_promotions,api_submissions,api_admissions,api_phases,api_evaluations,
    api_requests,api_attempts,api_builds,api_measurements,api_members,api_chain,
    api_weight_sets,api_allocations FROM PUBLIC;
REVOKE ALL ON FUNCTION api_eligibility(text,text),api_enqueue(text,text,text,uuid,boolean),
    api_source(bigint,bigint) FROM PUBLIC;

-- Temp objects must never shadow owner tables in an execute-only RPC. Preserve this
-- protection for existing worker RPCs as well as the new API functions.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
DO $$
DECLARE entry regprocedure;
BEGIN
  FOR entry IN SELECT p.oid::regprocedure FROM pg_proc p
    JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.prosecdef AND p.proowner=(SELECT oid FROM pg_roles WHERE rolname=current_user)
  LOOP
    EXECUTE format('ALTER FUNCTION %s SET search_path=pg_catalog,public,pg_temp',entry);
  END LOOP;
END $$;
