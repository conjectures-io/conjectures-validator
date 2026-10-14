-- Chat-log packages may be up to 64 MiB (was 32 MiB).
ALTER TABLE trajectory_claims DROP CONSTRAINT trajectory_package_bound;
ALTER TABLE trajectory_claims ADD CONSTRAINT trajectory_package_bound
    CHECK (package IS NULL OR octet_length(package) <= 67108864);
