-- exact-integer-kernels competition database, version eik_0001.
-- Tensor kernels' schema (tensor_0001..tensor_0007) without the Lean champion audits and the
-- timing-progress stream, plus submissions.normalised_sha256. The central API reads it with the
-- same queries; see coordinator/CONTRACT.md.

CREATE TABLE worker_logins (
 login TEXT PRIMARY KEY, kind TEXT NOT NULL, enabled BOOLEAN DEFAULT true NOT NULL,
 created_at TIMESTAMPTZ DEFAULT now() NOT NULL, updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
 CONSTRAINT ck_worker_kind CHECK (kind IN ('benchmark'))
);
CREATE TABLE worker_environments (
 login TEXT REFERENCES worker_logins(login) NOT NULL, environment TEXT NOT NULL,
 PRIMARY KEY(login,environment)
);
CREATE TABLE coordinator_state (
	id SERIAL NOT NULL,
	leader_token TEXT,
	config_hash TEXT,
	config_evidence JSONB,
	publication JSONB,
	publication_hash TEXT,
	PRIMARY KEY (id),
	CONSTRAINT ck_coordinator_singleton CHECK (id = 1)
);
CREATE TABLE registrations (
	id BIGSERIAL NOT NULL,
	uid INTEGER NOT NULL,
	ss58_hot TEXT NOT NULL,
	ss58_cold TEXT NOT NULL,
	block BIGINT NOT NULL,
	block_date TIMESTAMP WITH TIME ZONE NOT NULL,
	inserted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT uq_registrations_uid_block UNIQUE (uid, block)
);
CREATE INDEX ix_registrations_ss58_hot ON registrations (ss58_hot);
CREATE INDEX ix_registrations_uid ON registrations (uid);
CREATE TABLE sources (
	sha256 TEXT NOT NULL,
	content BYTEA NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (sha256),
	CONSTRAINT ck_sources_hash CHECK (sha256 ~ '^[0-9a-f]{64}$'),
	-- eik/config.py CAP_SOURCE_BYTES
	CONSTRAINT ck_sources_size CHECK (octet_length(content) BETWEEN 1 AND 262144)
);
CREATE TABLE weight_sets (
	id BIGSERIAL NOT NULL,
	netuid INTEGER NOT NULL,
	block BIGINT NOT NULL,
	uids JSONB NOT NULL,
	weights JSONB NOT NULL,
	summary TEXT,
	accepted BOOLEAN DEFAULT 'false' NOT NULL,
	error TEXT,
	dry_run BOOLEAN DEFAULT 'false' NOT NULL,
	publication_hash TEXT,
	publication JSONB,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id)
);
CREATE INDEX ix_weight_sets_netuid_id ON weight_sets (netuid, id);
CREATE TABLE benchmark_jobs (
	id BIGSERIAL NOT NULL,
	request_hash TEXT NOT NULL,
	request JSONB NOT NULL,
	candidate_hash TEXT NOT NULL,
	reference_hash TEXT NOT NULL,
	environment TEXT NOT NULL,
	lease_seconds FLOAT NOT NULL,
	retry_seconds FLOAT NOT NULL,
	state TEXT DEFAULT 'queued' NOT NULL,
	due_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	attempts INTEGER DEFAULT '0' NOT NULL,
	claimant TEXT,
	owner TEXT,
	token TEXT,
	lease_until TIMESTAMP WITH TIME ZONE,
	started_at TIMESTAMP WITH TIME ZONE,
	completed_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	result JSONB,
	error JSONB,
	PRIMARY KEY (id),
	CONSTRAINT ck_job_state CHECK (state IN ('queued','running','completed','failed','cancelled')),
	CONSTRAINT ck_job_periods CHECK (lease_seconds > 0 AND lease_seconds <= 86400 AND retry_seconds > 0 AND retry_seconds <= 86400),
	CONSTRAINT ck_job_lease CHECK ((state = 'running') = (owner IS NOT NULL AND token IS NOT NULL AND lease_until IS NOT NULL AND claimant IS NOT NULL)),
	FOREIGN KEY(candidate_hash) REFERENCES sources (sha256),
	FOREIGN KEY(reference_hash) REFERENCES sources (sha256)
);
CREATE INDEX ix_job_due ON benchmark_jobs (environment, state, due_at, id);
CREATE INDEX ix_job_reuse ON benchmark_jobs (request_hash, state);
CREATE TABLE source_disqualifications (
	source_hash TEXT NOT NULL,
	active BOOLEAN DEFAULT 'true' NOT NULL,
	reason TEXT NOT NULL,
	actor TEXT NOT NULL,
	history JSONB DEFAULT '[]' NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (source_hash),
	FOREIGN KEY(source_hash) REFERENCES sources (sha256)
);
CREATE TABLE submissions (
	id BIGSERIAL NOT NULL,
	hotkey TEXT NOT NULL,
	registration_id BIGINT,
	intake_mode TEXT DEFAULT 'subnet' NOT NULL,
	source_sha256 TEXT NOT NULL,
	submitted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	account_id TEXT,
	state TEXT DEFAULT 'queued' NOT NULL,
	verification_identity TEXT,
	proof_report JSONB,
	normalised_sha256 TEXT,
	stage TEXT,
	reason TEXT,
	attempts INTEGER DEFAULT '0' NOT NULL,
	next_attempt_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	owner TEXT,
	token TEXT,
	lease_until TIMESTAMP WITH TIME ZONE,
	finished_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	CONSTRAINT uq_submissions_hotkey_source UNIQUE (hotkey, source_sha256),
	CONSTRAINT ck_submissions_intake CHECK (
	    (intake_mode = 'subnet' AND registration_id IS NOT NULL) OR
	    (intake_mode = 'dev' AND registration_id IS NULL)),
	CONSTRAINT ck_proof_state CHECK (state IN ('queued','running','proved','rejected')),
	CONSTRAINT ck_proof_lease CHECK ((state = 'running') = (owner IS NOT NULL AND token IS NOT NULL AND lease_until IS NOT NULL)),
	CONSTRAINT ck_normalised_hash CHECK (normalised_sha256 IS NULL OR normalised_sha256 ~ '^[0-9a-f]{64}$'),
	UNIQUE (registration_id),
	FOREIGN KEY(registration_id) REFERENCES registrations (id),
	FOREIGN KEY(source_sha256) REFERENCES sources (sha256)
);
CREATE INDEX ix_submissions_proof_cache ON submissions (source_sha256, verification_identity, state);
CREATE INDEX ix_submissions_queue ON submissions (state, next_attempt_at, submitted_at, id);
CREATE INDEX ix_submissions_normalised ON submissions (normalised_sha256) WHERE state = 'proved';
CREATE TABLE board_progress (
	board TEXT NOT NULL,
	activation_boundary BIGINT NOT NULL,
	last_submission_id BIGINT,
	last_submitted_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (board),
	FOREIGN KEY(last_submission_id) REFERENCES submissions (id)
);
CREATE TABLE evaluations (
	id BIGSERIAL NOT NULL,
	submission_id BIGINT NOT NULL,
	board TEXT NOT NULL,
	generation INTEGER NOT NULL,
	config_hash TEXT NOT NULL,
	reference_promotion_id BIGINT,
	reference_hash TEXT NOT NULL,
	plan JSONB NOT NULL,
	state TEXT DEFAULT 'pending' NOT NULL,
	decision JSONB,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	completed_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	UNIQUE (submission_id, board, generation),
	CONSTRAINT ck_evaluation_state CHECK (state IN ('pending','completed','superseded')),
	FOREIGN KEY(submission_id) REFERENCES submissions (id),
	FOREIGN KEY(reference_hash) REFERENCES sources (sha256)
);
CREATE INDEX ix_evaluation_pending ON evaluations (board, state, submission_id);
CREATE UNIQUE INDEX uq_evaluation_board_pending ON evaluations (board) WHERE state = 'pending';
CREATE TABLE evaluation_jobs (
	evaluation_id BIGINT NOT NULL,
	job_id BIGINT NOT NULL,
	environment TEXT NOT NULL,
	seed BIGINT NOT NULL,
	PRIMARY KEY (evaluation_id, job_id),
	UNIQUE (evaluation_id, environment, seed),
	FOREIGN KEY(evaluation_id) REFERENCES evaluations (id),
	FOREIGN KEY(job_id) REFERENCES benchmark_jobs (id)
);
CREATE TABLE promotions (
	id BIGSERIAL NOT NULL,
	board TEXT NOT NULL,
	evaluation_id BIGINT NOT NULL,
	source_environment JSONB DEFAULT '{}' NOT NULL,
	promoted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	disqualified_at TIMESTAMP WITH TIME ZONE,
	disqualification_reason TEXT,
	PRIMARY KEY (id),
	UNIQUE (evaluation_id),
	FOREIGN KEY(evaluation_id) REFERENCES evaluations (id)
);
CREATE INDEX ix_promotions_board ON promotions (board, id);
ALTER TABLE evaluations ADD CONSTRAINT fk_evaluation_reference FOREIGN KEY(reference_promotion_id) REFERENCES promotions (id);
INSERT INTO coordinator_state(id) VALUES (1);

-- Worker protocol (Tensor kernels' protocol 4, without timing progress). Workers hold execute
-- grants only: no table writes, no promotions.
CREATE FUNCTION public.worker_authorized(env text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=session_user AND rolsuper)
 OR EXISTS(SELECT 1 FROM public.worker_logins w JOIN public.worker_environments e USING(login)
 WHERE w.login=session_user AND w.enabled AND w.kind='benchmark' AND e.environment=env);
$$;
CREATE FUNCTION public.worker_access() RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT jsonb_build_object('protocol','eik-1','login',session_user,
 'kind',w.kind,'enabled',coalesce(w.enabled,false),
 'environments',coalesce((SELECT jsonb_agg(e.environment ORDER BY e.environment)
 FROM public.worker_environments e WHERE e.login=session_user),'[]'::jsonb))
 FROM (SELECT 1) x LEFT JOIN public.worker_logins w ON w.login=session_user;
$$;

CREATE FUNCTION public.claim(env text, identity text, worker text, attempt text)
RETURNS SETOF public.benchmark_jobs LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE chosen bigint;
BEGIN
    IF length(attempt)<16 OR length(worker)<1 OR length(identity)<>64 THEN
        RAISE EXCEPTION 'invalid claim';
    END IF;
    IF NOT public.worker_authorized(env) THEN
        RAISE EXCEPTION 'worker is not authorized for environment %', env USING ERRCODE='42501';
    END IF;
    UPDATE public.benchmark_jobs SET state='queued',claimant=NULL,owner=NULL,token=NULL,lease_until=NULL,
        due_at=clock_timestamp()+make_interval(secs=>retry_seconds),
        error=jsonb_build_object('kind','infrastructure','reason','lease expired')
      WHERE environment=env AND state='running' AND lease_until<=clock_timestamp();
    SELECT j.id INTO chosen FROM public.benchmark_jobs j
      WHERE j.environment=env AND j.request->>'identity'=identity
        AND j.state='queued' AND j.due_at<=clock_timestamp()
        AND EXISTS(SELECT 1 FROM public.evaluation_jobs l JOIN public.evaluations e
                   ON e.id=l.evaluation_id WHERE l.job_id=j.id AND e.state='pending')
      ORDER BY j.due_at,j.id LIMIT 1 FOR UPDATE OF j SKIP LOCKED;
    RETURN QUERY UPDATE public.benchmark_jobs SET state='running',claimant=session_user,owner=worker,token=attempt,
        lease_until=clock_timestamp()+make_interval(secs=>lease_seconds),
        started_at=clock_timestamp(),attempts=attempts+1
      WHERE id=chosen RETURNING *;
END $$;

CREATE FUNCTION public.heartbeat(jid bigint, worker text, attempt text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    UPDATE public.benchmark_jobs SET lease_until=clock_timestamp()+make_interval(secs=>lease_seconds)
      WHERE id=jid AND state='running' AND claimant=session_user
        AND public.worker_authorized(environment) AND owner=worker AND token=attempt
        AND lease_until>clock_timestamp();
    RETURN FOUND;
END $$;

CREATE FUNCTION public.complete(jid bigint, worker text, attempt text, payload jsonb)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    IF payload IS NULL OR jsonb_typeof(payload)<>'object'
        OR octet_length(payload::text)>16777216 THEN RAISE EXCEPTION 'invalid result'; END IF;
    UPDATE public.benchmark_jobs SET state='completed',result=payload,completed_at=clock_timestamp(),
        claimant=NULL,owner=NULL,token=NULL,lease_until=NULL,error=NULL
      WHERE id=jid AND state='running' AND claimant=session_user
        AND public.worker_authorized(environment) AND owner=worker AND token=attempt
        AND lease_until>clock_timestamp();
    RETURN FOUND;
END $$;

-- An infrastructure failure requeues with backoff. 'candidate' is accepted for protocol
-- compatibility, but EIK workers complete a rejection with its report instead.
CREATE FUNCTION public.fail(jid bigint, worker text, attempt text, failure jsonb)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    IF failure IS NULL OR failure->>'kind' IS NULL
       OR failure->>'kind' NOT IN ('candidate','reference','infrastructure','checksum')
       OR failure->>'reason' IS NULL THEN RAISE EXCEPTION 'invalid failure'; END IF;
    UPDATE public.benchmark_jobs SET
        state=CASE WHEN failure->>'kind'='candidate' THEN 'failed' ELSE 'queued' END,
        error=jsonb_build_object('kind',failure->>'kind','reason',left(failure->>'reason',2000)),
        due_at=clock_timestamp()+make_interval(secs=>least(86400,retry_seconds*power(2,least(attempts-1,4)))),
        claimant=NULL,owner=NULL,token=NULL,lease_until=NULL
      WHERE id=jid AND state='running' AND claimant=session_user
        AND public.worker_authorized(environment) AND owner=worker AND token=attempt
        AND lease_until>clock_timestamp();
    RETURN FOUND;
END $$;

CREATE FUNCTION public.sources(jid bigint, worker text, attempt text)
RETURNS TABLE(candidate bytea, reference bytea) LANGUAGE sql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
    SELECT a.content,b.content FROM public.benchmark_jobs j
      JOIN public.sources a ON a.sha256=j.candidate_hash
      JOIN public.sources b ON b.sha256=j.reference_hash WHERE j.id=jid AND j.claimant=session_user
      AND public.worker_authorized(j.environment)
      AND j.state='running' AND j.owner=worker AND j.token=attempt
      AND j.lease_until>clock_timestamp();
$$;

-- A compare worker reads only the candidate and the champions named in its lease.
CREATE FUNCTION public.bundle_sources(jid bigint, worker text, attempt text)
RETURNS TABLE(candidate bytea, reference_hash text, reference bytea)
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
    SELECT DISTINCT a.content, b.sha256, b.content
      FROM public.benchmark_jobs j
      JOIN public.sources a ON a.sha256=j.candidate_hash
      CROSS JOIN LATERAL jsonb_each(j.request->'comparisons') AS comparison(board, spec)
      JOIN public.sources b ON b.sha256=comparison.spec->>'reference_hash'
     WHERE j.id=jid AND j.claimant=session_user
       AND public.worker_authorized(j.environment)
       AND j.state='running' AND j.owner=worker AND j.token=attempt
       AND j.lease_until>clock_timestamp()
     ORDER BY b.sha256;
$$;

CREATE FUNCTION public.immutable_source() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='UPDATE' THEN RAISE EXCEPTION 'sources are immutable'; END IF;
    IF encode(sha256(NEW.content),'hex')<>NEW.sha256 THEN
        RAISE EXCEPTION 'source hash mismatch';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER immutable_source BEFORE INSERT OR UPDATE ON sources
FOR EACH ROW EXECUTE FUNCTION public.immutable_source();

CREATE FUNCTION public.immutable_measurement() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.request,NEW.request_hash,NEW.candidate_hash,NEW.reference_hash,NEW.environment)
       IS DISTINCT FROM
       (OLD.request,OLD.request_hash,OLD.candidate_hash,OLD.reference_hash,OLD.environment)
       OR (OLD.result IS NOT NULL AND NEW.result IS DISTINCT FROM OLD.result) THEN
        RAISE EXCEPTION 'measurement evidence is immutable';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER immutable_measurement BEFORE UPDATE ON benchmark_jobs
FOR EACH ROW EXECUTE FUNCTION public.immutable_measurement();

REVOKE ALL ON FUNCTION public.worker_authorized(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.worker_access() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.claim(text,text,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.heartbeat(bigint,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.complete(bigint,text,text,jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fail(bigint,text,text,jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.sources(bigint,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.bundle_sources(bigint,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.immutable_source() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.immutable_measurement() FROM PUBLIC;

-- The worker permission group. Roles are cluster-wide, hence the competition prefix.
DO $$
BEGIN
  IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='eik_worker_access') THEN
    CREATE ROLE eik_worker_access NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
  END IF;
  IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='eik_worker_access' AND
    (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)) THEN
    RAISE EXCEPTION 'unsafe existing permission role eik_worker_access';
  END IF;
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO eik_worker_access', current_database());
END $$;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO eik_worker_access;
GRANT EXECUTE ON FUNCTION public.claim(text,text,text,text), public.heartbeat(bigint,text,text),
 public.complete(bigint,text,text,jsonb), public.fail(bigint,text,text,jsonb),
 public.sources(bigint,text,text), public.bundle_sources(bigint,text,text),
 public.worker_access() TO eik_worker_access;
