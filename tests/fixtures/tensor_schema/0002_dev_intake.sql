-- Mark development-only uploads without manufacturing chain registration rows.
ALTER TABLE submissions ALTER COLUMN registration_id DROP NOT NULL;
ALTER TABLE submissions ADD COLUMN intake_mode text NOT NULL DEFAULT 'subnet';
ALTER TABLE submissions ADD CONSTRAINT ck_submissions_intake CHECK (
    (intake_mode = 'subnet' AND registration_id IS NOT NULL) OR
    (intake_mode = 'dev' AND registration_id IS NULL)
);
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='platform_api') THEN
        GRANT INSERT(intake_mode) ON submissions TO platform_api;
    END IF;
END $$;
