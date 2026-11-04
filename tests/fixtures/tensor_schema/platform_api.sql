-- Central API: public evidence reads and intake; no proof/job/promotion writes.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM platform_api;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM platform_api;
GRANT USAGE ON SCHEMA public TO platform_api;
GRANT SELECT ON registrations, sources, submissions, board_progress, evaluations,
  evaluation_jobs, benchmark_jobs, promotions, weight_sets TO platform_api;
GRANT SELECT(source_hash,active) ON source_disqualifications TO platform_api;
GRANT INSERT ON sources TO platform_api;
GRANT INSERT(hotkey,registration_id,source_sha256,account_id,intake_mode) ON submissions TO platform_api;
GRANT USAGE ON SEQUENCE submissions_id_seq TO platform_api;
GRANT SELECT(id,config_hash,config_evidence,publication,publication_hash) ON coordinator_state TO platform_api;
