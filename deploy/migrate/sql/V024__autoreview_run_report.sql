-- The run rendered as one signable markdown document, written at publish time by
-- conjectures-autoreview's deterministic report generator (code assembles it from the stage
-- rows and the archive; no model is involved).  Stored on the run rather than re-rendered on
-- read so the document a reviewer signs is canonical bytes: two reads cannot disagree, and the
-- reviews API can serve it verbatim.
--
-- Nullable on purpose: every run published before the generator existed has none, and a run
-- that did not complete has nothing worth signing.  Absence means "not rendered", never
-- "rendered empty".

ALTER TABLE autoreview.runs
    ADD COLUMN report TEXT;

COMMENT ON COLUMN autoreview.runs.report IS
    'The run as one deterministic markdown document, rendered by code at publish time; '
    'NULL for runs published before the generator existed or that did not complete.';
