-- Cluster-wide NOLOGIN permission groups. Downgrade removes database objects, not roles.
DO $$
DECLARE name text;
BEGIN
 FOREACH name IN ARRAY ARRAY['tensor_benchmark_access','tensor_auditor_access'] LOOP
  IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=name) THEN
   EXECUTE format('CREATE ROLE %I NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',name);
  END IF;
  IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=name AND
    (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls))
    OR EXISTS(SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname=name) THEN
   RAISE EXCEPTION 'unsafe existing permission role %',name;
  END IF;
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO %I',current_database(),name);
 END LOOP;
END $$;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO tensor_benchmark_access, tensor_auditor_access;
GRANT EXECUTE ON FUNCTION public.claim(text,text,text,text),public.heartbeat(bigint,text,text),
 public.complete(bigint,text,text,jsonb),public.fail(bigint,text,text,jsonb),
 public.sources(bigint,text,text),public.worker_access() TO tensor_benchmark_access;
GRANT EXECUTE ON FUNCTION public.worker_access() TO tensor_auditor_access;
GRANT SELECT ON promotions,evaluations,submissions,sources TO tensor_auditor_access;
GRANT SELECT,INSERT,UPDATE ON audits,audit_cursors,audit_attempts TO tensor_auditor_access;
GRANT USAGE,SELECT ON SEQUENCE audits_id_seq TO tensor_auditor_access;
