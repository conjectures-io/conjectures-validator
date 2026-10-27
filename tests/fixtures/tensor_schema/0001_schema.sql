CREATE TABLE worker_logins (
 login TEXT PRIMARY KEY, kind TEXT NOT NULL, enabled BOOLEAN DEFAULT true NOT NULL,
 created_at TIMESTAMPTZ DEFAULT now() NOT NULL, updated_at TIMESTAMPTZ DEFAULT now() NOT NULL,
 CONSTRAINT ck_worker_kind CHECK (kind IN ('benchmark','auditor'))
);
CREATE TABLE worker_environments (
 login TEXT REFERENCES worker_logins(login) NOT NULL, environment TEXT NOT NULL,
 PRIMARY KEY(login,environment)
);
CREATE TABLE audit_cursors (
	profile_identity TEXT NOT NULL,
	promotion_id BIGINT DEFAULT '0' NOT NULL,
	PRIMARY KEY (profile_identity)
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
	CONSTRAINT ck_sources_size CHECK (octet_length(content) BETWEEN 1 AND 256000)
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
	registration_id BIGINT NOT NULL,
	source_sha256 TEXT NOT NULL,
	submitted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	account_id TEXT,
	state TEXT DEFAULT 'queued' NOT NULL,
	verification_identity TEXT,
	proof_report JSONB,
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
	CONSTRAINT ck_proof_state CHECK (state IN ('queued','running','proved','rejected')),
	CONSTRAINT ck_proof_lease CHECK ((state = 'running') = (owner IS NOT NULL AND token IS NOT NULL AND lease_until IS NOT NULL)),
	UNIQUE (registration_id),
	FOREIGN KEY(registration_id) REFERENCES registrations (id),
	FOREIGN KEY(source_sha256) REFERENCES sources (sha256)
);
CREATE INDEX ix_submissions_proof_cache ON submissions (source_sha256, verification_identity, state);
CREATE INDEX ix_submissions_queue ON submissions (state, next_attempt_at, submitted_at, id);
CREATE TABLE audits (
	id BIGSERIAL NOT NULL,
	source_hash TEXT NOT NULL,
	submission_id BIGINT NOT NULL,
	audit_identity TEXT NOT NULL,
	state TEXT DEFAULT 'queued' NOT NULL,
	seed BIGINT NOT NULL,
	cases INTEGER NOT NULL,
	report JSONB,
	attempts INTEGER DEFAULT '0' NOT NULL,
	due_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	finished_at TIMESTAMP WITH TIME ZONE,
	request JSONB DEFAULT '{}' NOT NULL,
	owner TEXT,
	token TEXT,
	lease_until TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (id),
	UNIQUE (source_hash, audit_identity),
	CONSTRAINT ck_exact_audit_state CHECK (state IN ('queued','running','clean','failed','error')),
	CONSTRAINT ck_audit_lease CHECK ((state = 'running') = (owner IS NOT NULL AND token IS NOT NULL AND lease_until IS NOT NULL)),
	FOREIGN KEY(source_hash) REFERENCES sources (sha256),
	FOREIGN KEY(submission_id) REFERENCES submissions (id)
);
CREATE INDEX ix_audits_due ON audits (state, due_at);
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
CREATE TABLE audit_attempts (
	token TEXT NOT NULL,
	audit_id BIGINT NOT NULL,
	owner TEXT NOT NULL,
	state TEXT DEFAULT 'running' NOT NULL,
	report JSONB,
	started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	finished_at TIMESTAMP WITH TIME ZONE,
	PRIMARY KEY (token),
	FOREIGN KEY(audit_id) REFERENCES audits (id)
);
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
