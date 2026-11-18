-- Coordinator-owned requests, independently leased workers, relational observations.
CREATE DOMAIN sha256_hex AS text CHECK (VALUE ~ '^[0-9a-f]{64}$');
CREATE DOMAIN nonnegative_seconds AS double precision
    CHECK (VALUE >= 0 AND VALUE < 'Infinity'::double precision);
CREATE DOMAIN positive_ratio AS double precision
    CHECK (VALUE > 0 AND VALUE < 'Infinity'::double precision);

CREATE TABLE sources (
    sha256 sha256_hex PRIMARY KEY,
    content text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (octet_length(content) BETWEEN 1 AND 4194304),
    CHECK (sha256 = encode(pg_catalog.sha256(convert_to(content, 'UTF8')), 'hex'))
);
COMMENT ON TABLE sources IS 'Immutable Lean source bytes, shared by submissions and reference baselines.';

CREATE TABLE snapshots (
    sha256 sha256_hex PRIMARY KEY,
    kind text NOT NULL CHECK (kind ~ '^[a-z][a-z0-9_-]*$'),
    schema_version integer NOT NULL CHECK (schema_version > 0),
    payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (NOT (payload ? 'git_commit') OR
        (jsonb_typeof(payload->'git_commit') = 'string' AND payload->>'git_commit' ~ '^([0-9a-f]{40}|[0-9a-f]{64})$')),
    CHECK (NOT (payload ? 'git_dirty') OR jsonb_typeof(payload->'git_dirty') = 'boolean')
);
COMMENT ON TABLE snapshots IS 'Deduplicated provenance, not live configuration. Payload may include git_commit, git_dirty, code_fingerprint, image_digest, settings and machine metadata. Insert through put_snapshot().';

CREATE FUNCTION snapshot_hash(snapshot_kind text, version integer, data jsonb) RETURNS text
LANGUAGE sql STABLE SET search_path = pg_catalog AS $$
    SELECT encode(sha256(convert_to(jsonb_build_object('kind', snapshot_kind, 'schema_version', version, 'payload', data)::text, 'UTF8')), 'hex');
$$;
CREATE FUNCTION validate_snapshot_hash() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE expected text;
BEGIN
    expected := snapshot_hash(NEW.kind, NEW.schema_version, NEW.payload);
    IF NEW.sha256 IS NOT NULL AND NEW.sha256 <> expected THEN
        RAISE EXCEPTION 'snapshot hash mismatch';
    END IF;
    NEW.sha256 := expected;
    RETURN NEW;
END $$;
CREATE TRIGGER snapshot_identity BEFORE INSERT ON snapshots FOR EACH ROW EXECUTE FUNCTION validate_snapshot_hash();
CREATE FUNCTION put_snapshot(snapshot_kind text, version integer, data jsonb) RETURNS text
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE identity text := snapshot_hash(snapshot_kind, version, data);
BEGIN
    INSERT INTO snapshots(sha256, kind, schema_version, payload) VALUES(identity, snapshot_kind, version, data)
        ON CONFLICT (sha256) DO NOTHING;
    RETURN identity;
END $$;
COMMENT ON FUNCTION put_snapshot(text,integer,jsonb) IS 'Hash canonical PostgreSQL JSONB including kind/version; identical payloads insert once. No active configuration is selected in the database.';

CREATE TABLE competitions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    slug text NOT NULL UNIQUE CHECK (slug ~ '^[a-z0-9][a-z0-9_-]*$'),
    name text NOT NULL CHECK (length(name) > 0),
    coordinator_token uuid,
    created_at timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE competitions IS 'Target identity and history namespace only. Services are configured outside the database.';

CREATE TABLE submissions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    competition_id bigint NOT NULL REFERENCES competitions(id),
    source_sha256 sha256_hex NOT NULL REFERENCES sources(sha256),
    submitter text NOT NULL CHECK (length(submitter) > 0),
    label text NOT NULL DEFAULT '',
    intake text NOT NULL DEFAULT 'dev' CHECK (intake IN ('dev', 'api')),
    submitted_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (id, competition_id)
);
CREATE INDEX submissions_chronology ON submissions(competition_id, submitted_at, id);
COMMENT ON TABLE submissions IS 'Intake history; repeated dev controls are allowed. Proof and benchmark state are separate.';

CREATE TABLE admissions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_sha256 sha256_hex NOT NULL REFERENCES sources(sha256),
    proof_identity sha256_hex NOT NULL,
    snapshot_sha256 sha256_hex NOT NULL REFERENCES snapshots(sha256),
    state text NOT NULL DEFAULT 'queued' CHECK (state IN ('queued', 'running', 'passed', 'rejected', 'failed')),
    available_at timestamptz NOT NULL DEFAULT now(),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    owner text,
    lease_token uuid,
    lease_expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    elapsed_seconds nonnegative_seconds,
    reason text,
    evidence_uri text,
    proof_report jsonb CHECK (jsonb_typeof(proof_report) = 'object'),
    UNIQUE (source_sha256, proof_identity),
    CHECK ((state = 'running' AND owner IS NOT NULL AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)
        OR (state <> 'running' AND owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL)),
    CHECK ((state IN ('passed', 'rejected', 'failed')) = (finished_at IS NOT NULL)),
    CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at)
);
COMMENT ON TABLE admissions IS 'Proof verdict under a specific proof identity/software snapshot. No runtime qualification is implied. Timings belong in columns/admission_phases, not proof_report.';

CREATE TABLE admission_phases (
    admission_id bigint NOT NULL REFERENCES admissions(id),
    attempt_number integer NOT NULL CHECK (attempt_number > 0),
    phase text NOT NULL CHECK (length(phase) > 0),
    status text NOT NULL CHECK (status IN ('completed', 'timeout', 'failed')),
    started_at timestamptz NOT NULL,
    finished_at timestamptz NOT NULL,
    elapsed_seconds nonnegative_seconds NOT NULL,
    user_cpu_seconds nonnegative_seconds,
    system_cpu_seconds nonnegative_seconds,
    peak_rss_kib bigint CHECK (peak_rss_kib > 0),
    returncode integer NOT NULL,
    evidence_uri text,
    PRIMARY KEY (admission_id, attempt_number, phase),
    CHECK (finished_at >= started_at)
);
COMMENT ON TABLE admission_phases IS 'Relational proof/build phase measurements, including admission retry history.';

CREATE TABLE evaluations (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    competition_id bigint NOT NULL REFERENCES competitions(id),
    submission_id bigint NOT NULL,
    admission_id bigint NOT NULL REFERENCES admissions(id),
    coordinator_snapshot_sha256 sha256_hex NOT NULL REFERENCES snapshots(sha256),
    baseline_source_sha256 sha256_hex NOT NULL REFERENCES sources(sha256),
    incumbent_promotion_id bigint,
    expected_targets text[] NOT NULL CHECK (cardinality(expected_targets) > 0 AND array_position(expected_targets, NULL) IS NULL),
    generation integer NOT NULL DEFAULT 1 CHECK (generation > 0),
    state text NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'completed', 'superseded', 'cancelled')),
    outcome text CHECK (outcome IN ('promoted', 'not_promoted', 'benchmark_failed', 'inconclusive')),
    speedup_estimate positive_ratio,
    speedup_lower positive_ratio,
    speedup_upper positive_ratio,
    guard_passed boolean,
    reason text,
    report_uri text,
    created_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    FOREIGN KEY (submission_id, competition_id) REFERENCES submissions(id, competition_id),
    UNIQUE (submission_id, generation),
    UNIQUE (id, competition_id),
    CHECK ((state = 'pending') = (completed_at IS NULL)),
    CHECK ((state = 'completed') = (outcome IS NOT NULL)),
    CHECK (completed_at IS NULL OR completed_at >= created_at)
);
CREATE UNIQUE INDEX one_pending_evaluation ON evaluations(competition_id) WHERE state = 'pending';
COMMENT ON TABLE evaluations IS 'One challenger against a fixed incumbent/reference. expected_targets seals the required request set, not a machine configuration registry. One pending challenger; its machine requests execute in parallel.';
COMMENT ON COLUMN evaluations.incumbent_promotion_id IS 'NULL means the evaluation baseline; otherwise this exact older champion term. Never a moving champion pointer.';

CREATE TABLE benchmark_requests (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    evaluation_id bigint NOT NULL REFERENCES evaluations(id),
    target text NOT NULL CHECK (target ~ '^[A-Za-z0-9][A-Za-z0-9_-]*$'),
    snapshot_sha256 sha256_hex NOT NULL REFERENCES snapshots(sha256),
    schema_version integer NOT NULL CHECK (schema_version > 0),
    specification jsonb NOT NULL CHECK (jsonb_typeof(specification) = 'object'),
    state text NOT NULL DEFAULT 'queued' CHECK (state IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    available_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    completed_attempt_id bigint,
    rounds_completed integer CHECK (rounds_completed >= 0),
    speedup_estimate positive_ratio,
    speedup_lower positive_ratio,
    speedup_upper positive_ratio,
    reason text,
    UNIQUE (evaluation_id, target),
    CHECK ((state IN ('completed', 'failed', 'cancelled')) = (finished_at IS NOT NULL)),
    CHECK ((state = 'completed') = (completed_attempt_id IS NOT NULL)),
    CHECK (finished_at IS NULL OR finished_at >= created_at)
);
CREATE INDEX benchmark_queue ON benchmark_requests(target, state, available_at, id);
COMMENT ON TABLE benchmark_requests IS 'Coordinator writes one immutable request per expected machine/target. specification is the versioned workload/limits/seed plan; all observed timing values live in relational tables.';

CREATE TABLE benchmark_attempts (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_id bigint NOT NULL REFERENCES benchmark_requests(id),
    attempt_number integer NOT NULL CHECK (attempt_number > 0),
    worker_key text NOT NULL CHECK (length(worker_key) > 0),
    snapshot_sha256 sha256_hex NOT NULL REFERENCES snapshots(sha256),
    lease_token uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    lease_expires_at timestamptz NOT NULL,
    state text NOT NULL DEFAULT 'running' CHECK (state IN ('running', 'completed', 'failed', 'expired', 'cancelled')),
    failure_kind text CHECK (failure_kind IN ('candidate', 'incumbent', 'baseline', 'infrastructure')),
    reason text,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    UNIQUE (request_id, attempt_number),
    UNIQUE (id, request_id),
    CHECK ((state = 'running') = (finished_at IS NULL)),
    CHECK ((state = 'failed') = (failure_kind IS NOT NULL)),
    CHECK (state <> 'failed' OR reason IS NOT NULL),
    CHECK (lease_expires_at > started_at),
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);
ALTER TABLE benchmark_requests ADD CONSTRAINT completed_attempt_belongs_to_request
    FOREIGN KEY (completed_attempt_id, id) REFERENCES benchmark_attempts(id, request_id);
CREATE UNIQUE INDEX one_running_attempt_per_request ON benchmark_attempts(request_id) WHERE state = 'running';
CREATE UNIQUE INDEX one_running_attempt_per_worker ON benchmark_attempts(worker_key) WHERE state = 'running';
CREATE INDEX attempts_expiring ON benchmark_attempts(lease_expires_at) WHERE state = 'running';
COMMENT ON TABLE benchmark_attempts IS 'Worker claims/retries with fresh tokens. Worker code/machine provenance is a snapshot, not a deployment configuration table. Old attempts and observations remain intact.';
COMMENT ON COLUMN benchmark_attempts.worker_key IS 'Operator-configured exclusive benchmark slot/device identity. The same key cannot benchmark two requests simultaneously.';

CREATE TABLE benchmark_builds (
    attempt_id bigint NOT NULL REFERENCES benchmark_attempts(id),
    role text NOT NULL CHECK (role IN ('baseline', 'incumbent', 'challenger')),
    source_sha256 sha256_hex NOT NULL REFERENCES sources(sha256),
    status text NOT NULL CHECK (status IN ('completed', 'timeout', 'failed')),
    cache_hit boolean NOT NULL DEFAULT false,
    started_at timestamptz NOT NULL,
    finished_at timestamptz NOT NULL,
    elapsed_seconds nonnegative_seconds NOT NULL,
    user_cpu_seconds nonnegative_seconds,
    system_cpu_seconds nonnegative_seconds,
    peak_rss_kib bigint CHECK (peak_rss_kib > 0),
    returncode integer NOT NULL,
    artifact_sha256 sha256_hex,
    evidence_uri text,
    reason text,
    PRIMARY KEY (attempt_id, role),
    CHECK (finished_at >= started_at),
    CHECK (status <> 'completed' OR (artifact_sha256 IS NOT NULL AND returncode = 0))
);
COMMENT ON TABLE benchmark_builds IS 'Native preparation/cache measurements for each role on each attempt. Excluded from checker runtime scores.';

CREATE TABLE measurements (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    attempt_id bigint NOT NULL REFERENCES benchmark_attempts(id),
    phase text NOT NULL DEFAULT 'benchmark' CHECK (phase IN ('benchmark', 'probe_before', 'probe_after')),
    round_index integer NOT NULL CHECK (round_index >= 0),
    case_id text NOT NULL CHECK (length(case_id) > 0),
    group_name text NOT NULL CHECK (length(group_name) > 0),
    input_sha256 sha256_hex NOT NULL,
    role text NOT NULL CHECK (role IN ('baseline', 'incumbent', 'challenger')),
    status text NOT NULL CHECK (status IN ('completed', 'timeout', 'input-error', 'resource-failure',
        'invalid-output', 'invalid-observation', 'invalid-timing', 'budget-exhausted', 'wrong-output',
        'affinity-mismatch', 'diagnostic-mismatch')),
    started_at timestamptz NOT NULL,
    finished_at timestamptz NOT NULL,
    wall_ns bigint CHECK (wall_ns > 0),
    cpu_ns bigint CHECK (cpu_ns > 0),
    peak_rss_kib bigint CHECK (peak_rss_kib > 0),
    check_and_render_ns bigint CHECK (check_and_render_ns > 0),
    parse_ns bigint CHECK (parse_ns >= 0),
    output_bytes bigint CHECK (output_bytes >= 0),
    sandbox_seconds nonnegative_seconds NOT NULL,
    returncode integer NOT NULL,
    outcome text CHECK (outcome IN ('accepted', 'rejected', 'exhausted', 'parse-error')),
    diagnostic text,
    allowed_cpu_count integer CHECK (allowed_cpu_count > 0),
    start_cpu integer CHECK (start_cpu >= 0),
    load_average_1 double precision CHECK (load_average_1 >= 0 AND load_average_1 < 'Infinity'::double precision),
    load_average_5 double precision CHECK (load_average_5 >= 0 AND load_average_5 < 'Infinity'::double precision),
    load_average_15 double precision CHECK (load_average_15 >= 0 AND load_average_15 < 'Infinity'::double precision),
    evidence_uri text,
    UNIQUE (attempt_id, phase, round_index, case_id, role),
    CHECK (finished_at >= started_at),
    CHECK (phase = 'benchmark' OR role = 'baseline'),
    CHECK (status <> 'completed' OR (wall_ns IS NOT NULL AND cpu_ns IS NOT NULL
        AND peak_rss_kib IS NOT NULL AND check_and_render_ns IS NOT NULL AND parse_ns IS NOT NULL
        AND outcome IN ('accepted', 'rejected') AND outcome IS NOT NULL AND returncode = 0)),
    CHECK (outcome IS DISTINCT FROM 'rejected' OR diagnostic IS NOT NULL)
);
CREATE INDEX measurements_timeline ON measurements(attempt_id, started_at, id);
COMMENT ON TABLE measurements IS 'Authoritative per-process observations, including load probes and failures. Timing, memory, output and diagnostics are columns, never hidden in JSON.';

CREATE TABLE measurement_members (
    measurement_id bigint NOT NULL REFERENCES measurements(id),
    member_index integer NOT NULL CHECK (member_index >= 0),
    case_id text NOT NULL CHECK (length(case_id) > 0),
    input_sha256 sha256_hex NOT NULL,
    parse_ns bigint NOT NULL CHECK (parse_ns >= 0),
    check_and_render_ns bigint NOT NULL CHECK (check_and_render_ns > 0),
    output_bytes bigint NOT NULL CHECK (output_bytes >= 0),
    outcome text NOT NULL CHECK (outcome IN ('accepted', 'rejected', 'exhausted')),
    diagnostic text,
    PRIMARY KEY (measurement_id, member_index),
    CHECK (outcome <> 'rejected' OR diagnostic IS NOT NULL)
);
COMMENT ON TABLE measurement_members IS 'Relational per-book results inside a batch. Completed members survive parent-process timeout; no partial timing arrays in JSON.';

CREATE TABLE promotions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    competition_id bigint NOT NULL REFERENCES competitions(id),
    evaluation_id bigint NOT NULL UNIQUE,
    promoted_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (evaluation_id, competition_id) REFERENCES evaluations(id, competition_id),
    UNIQUE (id, competition_id)
);
ALTER TABLE evaluations ADD CONSTRAINT evaluation_incumbent_same_competition
    FOREIGN KEY (incumbent_promotion_id, competition_id) REFERENCES promotions(id, competition_id);
COMMENT ON TABLE promotions IS 'Append-only champion history. Latest promotion is the current champion; no duplicate mutable pointer.';

CREATE FUNCTION validate_evaluation() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE candidate text; admitted admissions;
BEGIN
    SELECT source_sha256 INTO candidate FROM submissions WHERE id = NEW.submission_id;
    SELECT * INTO admitted FROM admissions WHERE id = NEW.admission_id;
    IF admitted.state IS DISTINCT FROM 'passed' OR admitted.source_sha256 IS DISTINCT FROM candidate THEN
        RAISE EXCEPTION 'evaluation requires a passed admission for its exact source';
    END IF;
    IF (SELECT count(DISTINCT name) FROM unnest(NEW.expected_targets) AS name) <> cardinality(NEW.expected_targets)
        OR EXISTS (SELECT 1 FROM unnest(NEW.expected_targets) AS name WHERE name !~ '^[A-Za-z0-9][A-Za-z0-9_-]*$') THEN
        RAISE EXCEPTION 'expected targets must be unique safe routing labels';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER evaluation_admission BEFORE INSERT ON evaluations FOR EACH ROW EXECUTE FUNCTION validate_evaluation();

CREATE FUNCTION preserve_evaluation_snapshot() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    IF (NEW.competition_id, NEW.submission_id, NEW.admission_id, NEW.coordinator_snapshot_sha256,
        NEW.baseline_source_sha256, NEW.incumbent_promotion_id, NEW.generation, NEW.expected_targets)
        IS DISTINCT FROM (OLD.competition_id, OLD.submission_id, OLD.admission_id, OLD.coordinator_snapshot_sha256,
        OLD.baseline_source_sha256, OLD.incumbent_promotion_id, OLD.generation, OLD.expected_targets) THEN
        RAISE EXCEPTION 'evaluation snapshots are immutable';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER evaluation_snapshot BEFORE UPDATE ON evaluations FOR EACH ROW EXECUTE FUNCTION preserve_evaluation_snapshot();

CREATE FUNCTION validate_request() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    IF TG_OP = 'INSERT' AND NOT EXISTS (SELECT 1 FROM evaluations WHERE id = NEW.evaluation_id
            AND state = 'pending' AND NEW.target = ANY(expected_targets)) THEN
        RAISE EXCEPTION 'request target is not expected by this pending evaluation';
    END IF;
    IF TG_OP = 'UPDATE' AND (NEW.evaluation_id, NEW.target, NEW.snapshot_sha256, NEW.schema_version, NEW.specification)
        IS DISTINCT FROM (OLD.evaluation_id, OLD.target, OLD.snapshot_sha256, OLD.schema_version, OLD.specification) THEN
        RAISE EXCEPTION 'benchmark request specification is immutable';
    END IF;
    IF NEW.state = 'completed' AND NOT EXISTS (SELECT 1 FROM benchmark_attempts
            WHERE id = NEW.completed_attempt_id AND request_id = NEW.id AND state = 'completed') THEN
        RAISE EXCEPTION 'completed request requires its completed attempt';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER request_identity BEFORE INSERT OR UPDATE ON benchmark_requests FOR EACH ROW EXECUTE FUNCTION validate_request();

CREATE FUNCTION validate_attempt() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.state <> 'running' OR NEW.lease_expires_at <= clock_timestamp()
            OR NOT EXISTS (SELECT 1 FROM benchmark_requests WHERE id = NEW.request_id AND state = 'running') THEN
            RAISE EXCEPTION 'attempt requires a running request and live lease';
        END IF;
    ELSE
        IF (NEW.request_id, NEW.attempt_number, NEW.worker_key, NEW.snapshot_sha256, NEW.lease_token, NEW.started_at)
            IS DISTINCT FROM (OLD.request_id, OLD.attempt_number, OLD.worker_key, OLD.snapshot_sha256, OLD.lease_token, OLD.started_at) THEN
            RAISE EXCEPTION 'attempt identity is immutable';
        END IF;
        IF OLD.state <> 'running' THEN RAISE EXCEPTION 'terminal attempts are immutable'; END IF;
        IF (NEW.state = 'completed' OR NEW.lease_expires_at IS DISTINCT FROM OLD.lease_expires_at)
            AND OLD.lease_expires_at <= clock_timestamp() THEN
            RAISE EXCEPTION 'expired attempt cannot complete or renew';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER attempt_identity BEFORE INSERT OR UPDATE ON benchmark_attempts FOR EACH ROW EXECUTE FUNCTION validate_attempt();

CREATE FUNCTION require_live_attempt() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE chosen bigint;
BEGIN
    IF TG_TABLE_NAME = 'measurement_members' THEN
        SELECT attempt_id INTO chosen FROM measurements WHERE id = NEW.measurement_id;
    ELSE chosen := NEW.attempt_id;
    END IF;
    PERFORM 1 FROM benchmark_attempts WHERE id = chosen AND state = 'running'
        AND lease_expires_at > clock_timestamp() FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'observation requires a live running attempt'; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER live_measurement BEFORE INSERT ON measurements FOR EACH ROW EXECUTE FUNCTION require_live_attempt();
CREATE TRIGGER live_member BEFORE INSERT ON measurement_members FOR EACH ROW EXECUTE FUNCTION require_live_attempt();
CREATE TRIGGER live_build BEFORE INSERT ON benchmark_builds FOR EACH ROW EXECUTE FUNCTION require_live_attempt();

CREATE FUNCTION reject_record_mutation() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
    RAISE EXCEPTION '% evidence is append-only', TG_TABLE_NAME;
END $$;
CREATE TRIGGER immutable_sources BEFORE UPDATE OR DELETE ON sources FOR EACH ROW EXECUTE FUNCTION reject_record_mutation();
CREATE TRIGGER immutable_snapshots BEFORE UPDATE OR DELETE ON snapshots FOR EACH ROW EXECUTE FUNCTION reject_record_mutation();
CREATE TRIGGER immutable_admission_phases BEFORE UPDATE OR DELETE ON admission_phases FOR EACH ROW EXECUTE FUNCTION reject_record_mutation();
CREATE TRIGGER immutable_measurements BEFORE UPDATE OR DELETE ON measurements FOR EACH ROW EXECUTE FUNCTION reject_record_mutation();
CREATE TRIGGER immutable_members BEFORE UPDATE OR DELETE ON measurement_members FOR EACH ROW EXECUTE FUNCTION reject_record_mutation();
CREATE TRIGGER immutable_builds BEFORE UPDATE OR DELETE ON benchmark_builds FOR EACH ROW EXECUTE FUNCTION reject_record_mutation();
CREATE TRIGGER immutable_promotions BEFORE UPDATE OR DELETE ON promotions FOR EACH ROW EXECUTE FUNCTION reject_record_mutation();

CREATE FUNCTION guard_promotion() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE evaluation evaluations; current_id bigint;
BEGIN
    PERFORM 1 FROM competitions WHERE id = NEW.competition_id FOR UPDATE;
    SELECT * INTO evaluation FROM evaluations WHERE id = NEW.evaluation_id;
    SELECT id INTO current_id FROM promotions WHERE competition_id = NEW.competition_id ORDER BY id DESC LIMIT 1;
    IF evaluation.competition_id IS DISTINCT FROM NEW.competition_id
        OR evaluation.state IS DISTINCT FROM 'completed' OR evaluation.outcome IS DISTINCT FROM 'promoted'
        OR evaluation.incumbent_promotion_id IS DISTINCT FROM current_id THEN
        RAISE EXCEPTION 'promotion requires a qualifying evaluation against the current champion';
    END IF;
    IF EXISTS (SELECT 1 FROM unnest(evaluation.expected_targets) AS expected(target)
        LEFT JOIN benchmark_requests r ON r.evaluation_id = evaluation.id AND r.target = expected.target
        WHERE r.id IS NULL OR r.state <> 'completed') THEN
        RAISE EXCEPTION 'promotion requires a completed request for every expected target';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER promotion_guard BEFORE INSERT ON promotions FOR EACH ROW EXECUTE FUNCTION guard_promotion();

CREATE VIEW current_champions AS
SELECT DISTINCT ON (p.competition_id) p.competition_id, p.id AS promotion_id,
    p.evaluation_id, e.submission_id, s.submitter, s.source_sha256, p.promoted_at
FROM promotions p JOIN evaluations e ON e.id = p.evaluation_id
JOIN submissions s ON s.id = e.submission_id ORDER BY p.competition_id, p.id DESC;

CREATE VIEW evaluation_progress AS
SELECT e.id AS evaluation_id, e.competition_id, e.submission_id, e.incumbent_promotion_id, e.state, e.outcome,
    cardinality(e.expected_targets) AS required_requests, count(r.id) AS planned_requests,
    count(r.id) FILTER (WHERE r.state = 'completed') AS completed_requests,
    count(r.id) FILTER (WHERE r.state = 'failed') AS failed_requests, e.created_at, e.completed_at
FROM evaluations e LEFT JOIN benchmark_requests r ON r.evaluation_id = e.id GROUP BY e.id;

CREATE VIEW worker_status AS
SELECT a.worker_key, r.target, a.id AS running_attempt_id, a.request_id, a.snapshot_sha256,
    a.lease_expires_at, (a.lease_expires_at <= clock_timestamp()) AS lease_expired
FROM benchmark_attempts a JOIN benchmark_requests r ON r.id = a.request_id WHERE a.state = 'running';

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;

-- Authorization is separate from deployment configuration: presence grants a route.
CREATE TABLE worker_authorizations (
    login text NOT NULL,
    target text NOT NULL CHECK (target ~ '^[A-Za-z0-9][A-Za-z0-9_-]*$'),
    PRIMARY KEY (login,target)
);
REVOKE ALL ON worker_authorizations FROM PUBLIC;

-- Worker logins get EXECUTE on the public entry points only, never table writes/promotions.
CREATE FUNCTION worker_access() RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
SET search_path=pg_catalog,public AS $$
    SELECT jsonb_build_object('targets',coalesce(jsonb_agg(target ORDER BY target),'[]'::jsonb))
    FROM worker_authorizations WHERE login=session_user;
$$;
REVOKE ALL ON FUNCTION worker_access() FROM PUBLIC;

CREATE FUNCTION worker_snapshot(data jsonb) RETURNS text LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
BEGIN
    IF jsonb_typeof(data) <> 'object' OR octet_length(data::text) > 65536 THEN RAISE EXCEPTION 'invalid worker snapshot'; END IF;
    RETURN put_snapshot('worker',1,data || jsonb_build_object('database_login',session_user));
END $$;

CREATE FUNCTION reap_benchmark_requests() RETURNS integer LANGUAGE plpgsql
SET search_path = pg_catalog, public AS $$
DECLARE request benchmark_requests; changed integer := 0; attempts integer;
BEGIN
    FOR request IN SELECT r.* FROM benchmark_requests r WHERE r.state='running'
        ORDER BY r.id FOR UPDATE SKIP LOCKED LOOP
        UPDATE benchmark_attempts SET state='expired',finished_at=clock_timestamp()
            WHERE request_id=request.id AND state='running' AND lease_expires_at<=clock_timestamp();
        IF EXISTS (SELECT 1 FROM benchmark_attempts WHERE request_id=request.id AND state='running') THEN CONTINUE; END IF;
        SELECT count(*) INTO attempts FROM benchmark_attempts WHERE request_id=request.id;
        IF attempts >= coalesce((request.specification->>'retry_limit')::int,3) THEN
            UPDATE benchmark_requests SET state='failed',finished_at=clock_timestamp(),reason='infrastructure: lease retry limit exhausted' WHERE id=request.id;
        ELSE
            UPDATE benchmark_requests SET state='queued',available_at=clock_timestamp(),reason='infrastructure: lease expired' WHERE id=request.id;
        END IF;
        changed := changed+1;
    END LOOP;
    RETURN changed;
END $$;

CREATE FUNCTION worker_claim(route text, device text, snapshot text, identity text, lease_seconds integer)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE request benchmark_requests; attempt benchmark_attempts; evaluation evaluations;
    candidate sources; baseline sources; incumbent sources; worker text := session_user || ':' || device;
BEGIN
    IF lease_seconds NOT BETWEEN 1 AND 3600 OR device !~ '^[A-Za-z0-9][A-Za-z0-9_-]*$' OR length(identity)<>64 THEN
        RAISE EXCEPTION 'invalid worker claim';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM snapshots WHERE sha256=snapshot AND kind='worker' AND payload->>'database_login'=session_user
        AND payload->>'execution_identity'=identity) THEN RAISE EXCEPTION 'worker snapshot identity mismatch'; END IF;
    IF NOT EXISTS (SELECT 1 FROM worker_authorizations WHERE login=session_user AND target=route) THEN
        RAISE EXCEPTION 'worker not authorized for target' USING ERRCODE='42501';
    END IF;
    PERFORM reap_benchmark_requests();
    -- Serialize claims by authenticated device key before selecting requests.
    PERFORM pg_advisory_xact_lock(hashtextextended(worker,0));
    IF EXISTS (SELECT 1 FROM benchmark_attempts WHERE worker_key=worker AND state='running') THEN RETURN NULL; END IF;
    SELECT r.* INTO request FROM benchmark_requests r JOIN evaluations e ON e.id=r.evaluation_id
        WHERE r.state='queued' AND r.target=route AND r.available_at<=clock_timestamp() AND e.state='pending'
          AND r.specification->>'execution_identity'=identity
          AND (r.specification->>'machine_identity' IS NULL OR r.specification->>'machine_identity'=(SELECT payload->>'machine_identity' FROM snapshots WHERE sha256=snapshot))
        ORDER BY r.available_at,r.id LIMIT 1 FOR UPDATE OF r SKIP LOCKED;
    IF NOT FOUND THEN RETURN NULL; END IF;
    UPDATE benchmark_requests SET state='running',reason=NULL WHERE id=request.id;
    INSERT INTO benchmark_attempts(request_id,attempt_number,worker_key,snapshot_sha256,lease_expires_at)
        SELECT request.id,coalesce(max(attempt_number),0)+1,worker,snapshot,clock_timestamp()+make_interval(secs=>lease_seconds)
        FROM benchmark_attempts WHERE request_id=request.id RETURNING * INTO attempt;
    SELECT * INTO evaluation FROM evaluations WHERE id=request.evaluation_id;
    SELECT src.* INTO candidate FROM submissions sub JOIN sources src ON src.sha256=sub.source_sha256 WHERE sub.id=evaluation.submission_id;
    SELECT * INTO baseline FROM sources WHERE sha256=evaluation.baseline_source_sha256;
    IF evaluation.incumbent_promotion_id IS NULL THEN incumbent := baseline;
    ELSE
        SELECT src.* INTO incumbent FROM promotions p JOIN evaluations e ON e.id=p.evaluation_id
            JOIN submissions s ON s.id=e.submission_id JOIN sources src ON src.sha256=s.source_sha256
            WHERE p.id=evaluation.incumbent_promotion_id;
    END IF;
    RETURN jsonb_build_object('request_id',request.id,'attempt_id',attempt.id,'token',attempt.lease_token,
        'target',request.target,'specification',request.specification,
        'baseline',jsonb_build_object('sha256',baseline.sha256,'content',baseline.content),
        'incumbent',jsonb_build_object('sha256',incumbent.sha256,'content',incumbent.content),
        'challenger',jsonb_build_object('sha256',candidate.sha256,'content',candidate.content));
END $$;

CREATE FUNCTION worker_attempt(token uuid) RETURNS benchmark_attempts LANGUAGE plpgsql
SET search_path = pg_catalog, public AS $$
DECLARE chosen benchmark_attempts; request_id bigint;
BEGIN
    SELECT a.request_id INTO request_id FROM benchmark_attempts a WHERE a.lease_token=token;
    PERFORM 1 FROM benchmark_requests r JOIN evaluations e ON e.id=r.evaluation_id
        WHERE r.id=request_id AND r.state='running' AND e.state='pending'
          AND EXISTS (SELECT 1 FROM worker_authorizations w WHERE w.login=session_user AND w.target=r.target)
        FOR UPDATE OF r;
    IF NOT FOUND THEN RAISE EXCEPTION 'lease lost' USING ERRCODE='55000'; END IF;
    SELECT * INTO chosen FROM benchmark_attempts WHERE lease_token=token AND state='running'
        AND split_part(worker_key,':',1)=session_user AND lease_expires_at>clock_timestamp() FOR UPDATE;
    IF NOT FOUND THEN RAISE EXCEPTION 'lease lost' USING ERRCODE='55000'; END IF;
    RETURN chosen;
END $$;

CREATE FUNCTION worker_heartbeat(token uuid, lease_seconds integer) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE chosen benchmark_attempts;
BEGIN
    IF lease_seconds NOT BETWEEN 1 AND 3600 THEN RAISE EXCEPTION 'invalid lease duration'; END IF;
    chosen := worker_attempt(token);
    UPDATE benchmark_attempts SET lease_expires_at=clock_timestamp()+make_interval(secs=>lease_seconds) WHERE id=chosen.id;
    RETURN true;
EXCEPTION WHEN object_not_in_prerequisite_state THEN RETURN false;
END $$;

CREATE FUNCTION worker_build(token uuid, data jsonb) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE chosen benchmark_attempts; item benchmark_builds; previous benchmark_builds;
BEGIN
    chosen := worker_attempt(token);
    item := jsonb_populate_record(NULL::benchmark_builds,data);
    item.attempt_id := chosen.id;
    INSERT INTO benchmark_builds SELECT (item).* ON CONFLICT(attempt_id,role) DO NOTHING;
    SELECT * INTO previous FROM benchmark_builds WHERE attempt_id=item.attempt_id AND role=item.role;
    IF previous IS DISTINCT FROM item THEN RAISE EXCEPTION 'conflicting build record'; END IF;
    RETURN true;
END $$;

CREATE FUNCTION worker_measurement(token uuid, data jsonb, members jsonb) RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE chosen benchmark_attempts; item measurements; previous measurements; member measurement_members;
    old_member measurement_members; value jsonb; inserted bigint;
BEGIN
    chosen := worker_attempt(token);
    item := jsonb_populate_record(NULL::measurements,data);
    item.attempt_id := chosen.id;
    item.id := nextval(pg_get_serial_sequence('public.measurements','id'));
    INSERT INTO measurements OVERRIDING SYSTEM VALUE SELECT (item).*
        ON CONFLICT(attempt_id,phase,round_index,case_id,role) DO NOTHING RETURNING id INTO inserted;
    IF inserted IS NULL THEN
        SELECT * INTO previous FROM measurements WHERE attempt_id=item.attempt_id AND phase=item.phase
            AND round_index=item.round_index AND case_id=item.case_id AND role=item.role;
        item.id := previous.id;
        IF previous IS DISTINCT FROM item THEN RAISE EXCEPTION 'conflicting measurement record'; END IF;
    END IF;
    IF jsonb_typeof(members)<>'array' THEN RAISE EXCEPTION 'members must be an array'; END IF;
    FOR value IN SELECT * FROM jsonb_array_elements(members) LOOP
        member := jsonb_populate_record(NULL::measurement_members,value);
        member.measurement_id := item.id;
        INSERT INTO measurement_members SELECT (member).* ON CONFLICT(measurement_id,member_index) DO NOTHING;
        SELECT * INTO old_member FROM measurement_members WHERE measurement_id=member.measurement_id AND member_index=member.member_index;
        IF old_member IS DISTINCT FROM member THEN RAISE EXCEPTION 'conflicting batch member record'; END IF;
    END LOOP;
    RETURN item.id;
END $$;

CREATE FUNCTION worker_finish(token uuid, succeeded boolean, failure_kind text, reason text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE chosen benchmark_attempts; retries integer;
BEGIN
    chosen := worker_attempt(token);
    SELECT coalesce((specification->>'retry_limit')::integer,3) INTO retries FROM benchmark_requests WHERE id=chosen.request_id;
    IF succeeded THEN
        UPDATE benchmark_attempts SET state='completed',finished_at=clock_timestamp() WHERE id=chosen.id;
        UPDATE benchmark_requests SET state='completed',completed_attempt_id=chosen.id,finished_at=clock_timestamp() WHERE id=chosen.request_id;
    ELSE
        UPDATE benchmark_attempts SET state='failed',finished_at=clock_timestamp(),failure_kind=worker_finish.failure_kind,
            reason=worker_finish.reason WHERE id=chosen.id;
        IF failure_kind='candidate' OR chosen.attempt_number>=retries THEN
            UPDATE benchmark_requests SET state='failed',finished_at=clock_timestamp(),reason=failure_kind || ': ' || worker_finish.reason WHERE id=chosen.request_id;
        ELSE
            UPDATE benchmark_requests SET state='queued',available_at=clock_timestamp()+make_interval(secs=>least(300,5*power(2,least(chosen.attempt_number-1,6)))),reason=failure_kind || ': ' || worker_finish.reason WHERE id=chosen.request_id;
        END IF;
    END IF;
    RETURN true;
END $$;

REVOKE ALL ON FUNCTION worker_snapshot(jsonb),reap_benchmark_requests(),worker_claim(text,text,text,text,integer),
    worker_attempt(uuid),worker_heartbeat(uuid,integer),worker_build(uuid,jsonb),worker_measurement(uuid,jsonb,jsonb),
    worker_finish(uuid,boolean,text,text) FROM PUBLIC;
