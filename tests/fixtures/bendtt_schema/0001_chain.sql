-- Part of the initial pilot schema. Services are configured outside the database.
CREATE TABLE chain_state (
    network text NOT NULL,
    netuid integer NOT NULL CHECK (netuid BETWEEN 0 AND 65535),
    block bigint NOT NULL CHECK (block>=0),
    block_hash text NOT NULL,
    block_time timestamptz NOT NULL,
    snapshot_sha256 sha256_hex NOT NULL REFERENCES snapshots(sha256),
    observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(network,netuid)
);

CREATE TABLE registrations (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    network text NOT NULL,
    netuid integer NOT NULL CHECK (netuid BETWEEN 0 AND 65535),
    uid integer NOT NULL CHECK (uid>=0),
    hotkey text NOT NULL CHECK (length(hotkey)>0),
    coldkey text NOT NULL CHECK (length(coldkey)>0),
    registered_block bigint NOT NULL CHECK (registered_block>=0),
    observed_block bigint NOT NULL CHECK (observed_block>=registered_block),
    observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    active boolean NOT NULL DEFAULT true,
    CONSTRAINT registration_observation UNIQUE(network,netuid,uid,registered_block,hotkey,coldkey)
);
CREATE UNIQUE INDEX registration_active_uid ON registrations(network,netuid,uid) WHERE active;
CREATE UNIQUE INDEX registration_active_hotkey ON registrations(network,netuid,hotkey) WHERE active;
COMMENT ON TABLE registrations IS 'Observed registration history. registered_block is exact chain data; observed_at is discovery time, not an invented registration timestamp.';

ALTER TABLE submissions ADD COLUMN hotkey text CHECK (hotkey IS NULL OR length(hotkey)>0);
ALTER TABLE submissions ADD COLUMN registration_id bigint REFERENCES registrations(id);
CREATE UNIQUE INDEX submission_registration_slot ON submissions(competition_id,registration_id) WHERE registration_id IS NOT NULL;
CREATE FUNCTION validate_submission_registration() RETURNS trigger LANGUAGE plpgsql
SET search_path=pg_catalog,public AS $$
BEGIN
    IF NEW.registration_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM registrations WHERE id=NEW.registration_id AND hotkey=NEW.hotkey) THEN
        RAISE EXCEPTION 'submission hotkey does not match registration';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER submission_registration BEFORE INSERT OR UPDATE OF registration_id,hotkey ON submissions
FOR EACH ROW EXECUTE FUNCTION validate_submission_registration();

CREATE TABLE weight_sets (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    competition_id bigint NOT NULL REFERENCES competitions(id),
    network text NOT NULL,
    netuid integer NOT NULL CHECK (netuid BETWEEN 0 AND 65535),
    validator_hotkey text NOT NULL,
    mode text NOT NULL CHECK (mode IN ('dry_run','live')),
    block bigint NOT NULL CHECK (block>=0),
    block_hash text NOT NULL,
    epoch_block bigint NOT NULL CHECK (epoch_block>=0 AND epoch_block<=block),
    champion_promotion_id bigint REFERENCES promotions(id),
    snapshot_sha256 sha256_hex NOT NULL REFERENCES snapshots(sha256),
    status text NOT NULL CHECK (status IN ('planned','dry_run','submitted','failed','unknown','skipped')),
    reason text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    finished_at timestamptz,
    CHECK ((status='planned')=(finished_at IS NULL)),
    UNIQUE(competition_id,network,netuid,validator_hotkey,mode,epoch_block,block)
);
CREATE INDEX weight_sets_epoch ON weight_sets(network,netuid,validator_hotkey,epoch_block,mode);
COMMENT ON TABLE weight_sets IS 'Durable before sending. dry_run never signs; submitted means SDK-reported inclusion, not a guaranteed payout. planned after a crash is ambiguous and must not be blindly resent.';
CREATE TABLE weight_allocations (
    weight_set_id bigint NOT NULL REFERENCES weight_sets(id),
    uid integer NOT NULL CHECK (uid>=0),
    hotkey text NOT NULL,
    weight double precision NOT NULL CHECK (weight>=0 AND weight<=1),
    role text NOT NULL CHECK (role IN ('miner','treasury')),
    PRIMARY KEY(weight_set_id,uid)
);
COMMENT ON TABLE weight_allocations IS 'The proposed normalized per-UID vector. SDK/chain quantization may differ; this is not measured emission.';
REVOKE ALL ON chain_state,registrations,weight_sets,weight_allocations FROM PUBLIC;
