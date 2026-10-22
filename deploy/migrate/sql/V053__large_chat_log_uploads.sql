-- Expand the existing bound without changing accepted package bytes or their hashes.
ALTER TABLE trajectory_claims DROP CONSTRAINT trajectory_package_bound;
ALTER TABLE trajectory_claims ADD CONSTRAINT trajectory_package_bound
    CHECK (package IS NULL OR octet_length(package) <= 268435456);

-- Private, temporary transport storage. Parts stay below the public proxy's request limit.
CREATE TABLE trajectory_uploads (
    id uuid PRIMARY KEY,
    account_id uuid NOT NULL UNIQUE REFERENCES accounts(id),
    byte_length integer NOT NULL,
    received_bytes integer NOT NULL,
    expires_at timestamptz NOT NULL,
    CONSTRAINT trajectory_upload_size CHECK (byte_length BETWEEN 1 AND 268435456),
    CONSTRAINT trajectory_upload_received CHECK (received_bytes BETWEEN 0 AND byte_length)
);
CREATE TABLE trajectory_upload_chunks (
    upload_id uuid NOT NULL REFERENCES trajectory_uploads(id) ON DELETE CASCADE,
    byte_offset integer NOT NULL,
    data bytea NOT NULL,
    PRIMARY KEY (upload_id, byte_offset),
    CONSTRAINT trajectory_chunk_offset CHECK (byte_offset >= 0 AND byte_offset % 8388608 = 0),
    CONSTRAINT trajectory_chunk_size CHECK (octet_length(data) BETWEEN 1 AND 8388608)
);
REVOKE ALL ON trajectory_uploads, trajectory_upload_chunks FROM PUBLIC;
