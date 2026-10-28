-- Request ceilings live centrally, separately from competition-owned schemas.
CREATE TABLE competition_intake_limits (
    subject text PRIMARY KEY,
    window_start timestamptz NOT NULL,
    hits integer NOT NULL,
    CONSTRAINT ck_competition_intake_hits CHECK (hits >= 1)
);
REVOKE ALL ON competition_intake_limits FROM PUBLIC;
