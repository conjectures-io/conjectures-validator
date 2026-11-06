ALTER TABLE trajectory_uploads DROP CONSTRAINT trajectory_upload_size;
ALTER TABLE trajectory_uploads ALTER COLUMN byte_length TYPE bigint;
ALTER TABLE trajectory_uploads ALTER COLUMN received_bytes TYPE bigint;
ALTER TABLE trajectory_upload_chunks ALTER COLUMN byte_offset TYPE bigint;
ALTER TABLE trajectory_uploads ADD CONSTRAINT trajectory_upload_size CHECK (byte_length BETWEEN 1 AND 10737418240);
ALTER TABLE trajectory_claims ADD COLUMN package_chunked boolean NOT NULL DEFAULT false;
ALTER TABLE trajectory_claims ADD CONSTRAINT trajectory_package_storage CHECK (NOT package_chunked OR package IS NULL);
CREATE TABLE trajectory_package_chunks (
    claim_id uuid NOT NULL REFERENCES trajectory_claims(id) ON DELETE CASCADE,
    byte_offset bigint NOT NULL,
    data bytea NOT NULL,
    PRIMARY KEY (claim_id, byte_offset),
    CONSTRAINT trajectory_package_chunk_offset CHECK (byte_offset >= 0 AND byte_offset < 10737418240 AND byte_offset % 8388608 = 0),
    CONSTRAINT trajectory_package_chunk_size CHECK (octet_length(data) BETWEEN 1 AND 8388608)
);
