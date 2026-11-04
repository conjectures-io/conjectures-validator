-- Authenticated login authorization. Superusers retain administrative recovery/test access.
CREATE FUNCTION public.worker_authorized(env text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=session_user AND rolsuper)
 OR EXISTS(SELECT 1 FROM public.worker_logins w JOIN public.worker_environments e USING(login)
 WHERE w.login=session_user AND w.enabled AND w.kind='benchmark' AND e.environment=env);
$$;
CREATE FUNCTION public.worker_access() RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public AS $$
 SELECT jsonb_build_object('protocol',2,'login',session_user,
 'kind',w.kind,'enabled',coalesce(w.enabled,false),
 'environments',coalesce((SELECT jsonb_agg(e.environment ORDER BY e.environment)
 FROM public.worker_environments e WHERE e.login=session_user),'[]'::jsonb))
 FROM (SELECT 1) x LEFT JOIN public.worker_logins w ON w.login=session_user;
$$;
REVOKE ALL ON FUNCTION public.worker_authorized(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.worker_access() FROM PUBLIC;
-- Frozen worker protocol. No hardware worker has table-write or promotion privileges.
CREATE FUNCTION public.claim(env text, identity text, worker text, attempt text)
RETURNS SETOF public.benchmark_jobs LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
DECLARE chosen bigint;
BEGIN
    IF length(attempt)<16 OR length(worker)<1 OR length(identity)<>64 THEN
        RAISE EXCEPTION 'invalid claim';
    END IF;
    IF NOT public.worker_authorized(env) THEN
        RAISE EXCEPTION 'worker is not authorized for environment %', env USING ERRCODE='42501';
    END IF;
    -- Delay expired work behind jobs that were already ready, even with a short retry period.
    UPDATE public.benchmark_jobs SET state='queued',claimant=NULL,owner=NULL,token=NULL,lease_until=NULL,
        due_at=clock_timestamp()+make_interval(secs=>retry_seconds),
        error=jsonb_build_object('kind','infrastructure','reason','lease expired')
      WHERE environment=env AND state='running' AND lease_until<=clock_timestamp();
    SELECT j.id INTO chosen FROM public.benchmark_jobs j
      WHERE j.environment=env AND j.request->>'identity'=identity
        AND j.state='queued' AND j.due_at<=clock_timestamp()
        AND EXISTS(SELECT 1 FROM public.evaluation_jobs l JOIN public.evaluations e
                   ON e.id=l.evaluation_id WHERE l.job_id=j.id AND e.state='pending')
      ORDER BY j.due_at,j.id LIMIT 1 FOR UPDATE OF j SKIP LOCKED;
    RETURN QUERY UPDATE public.benchmark_jobs SET state='running',claimant=session_user,owner=worker,token=attempt,
        lease_until=clock_timestamp()+make_interval(secs=>lease_seconds),
        started_at=clock_timestamp(),attempts=attempts+1
      WHERE id=chosen RETURNING *;
END $$;

CREATE FUNCTION public.heartbeat(jid bigint, worker text, attempt text)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    UPDATE public.benchmark_jobs SET lease_until=clock_timestamp()+make_interval(secs=>lease_seconds)
      WHERE id=jid AND state='running' AND claimant=session_user
        AND public.worker_authorized(environment) AND owner=worker AND token=attempt
        AND lease_until>clock_timestamp();
    RETURN FOUND;
END $$;

CREATE FUNCTION public.complete(jid bigint, worker text, attempt text, payload jsonb)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    IF payload IS NULL OR jsonb_typeof(payload)<>'object'
        OR octet_length(payload::text)>16777216 THEN RAISE EXCEPTION 'invalid result'; END IF;
    UPDATE public.benchmark_jobs SET state='completed',result=payload,completed_at=clock_timestamp(),
        claimant=NULL,owner=NULL,token=NULL,lease_until=NULL,error=NULL
      WHERE id=jid AND state='running' AND claimant=session_user
        AND public.worker_authorized(environment) AND owner=worker AND token=attempt
        AND lease_until>clock_timestamp();
    RETURN FOUND;
END $$;

CREATE FUNCTION public.fail(jid bigint, worker text, attempt text, failure jsonb)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
    IF failure IS NULL OR failure->>'kind' IS NULL
       OR failure->>'kind' NOT IN ('candidate','reference','infrastructure','checksum')
       OR failure->>'reason' IS NULL THEN RAISE EXCEPTION 'invalid failure'; END IF;
    UPDATE public.benchmark_jobs SET
        state=CASE WHEN failure->>'kind'='candidate' THEN 'failed' ELSE 'queued' END,
        error=jsonb_build_object('kind',failure->>'kind','reason',left(failure->>'reason',2000)),
        due_at=clock_timestamp()+make_interval(secs=>least(86400,retry_seconds*power(2,least(attempts-1,4)))),
        claimant=NULL,owner=NULL,token=NULL,lease_until=NULL
      WHERE id=jid AND state='running' AND claimant=session_user
        AND public.worker_authorized(environment) AND owner=worker AND token=attempt
        AND lease_until>clock_timestamp();
    RETURN FOUND;
END $$;

CREATE FUNCTION public.sources(jid bigint, worker text, attempt text)
RETURNS TABLE(candidate bytea, reference bytea) LANGUAGE sql SECURITY DEFINER
SET search_path = pg_catalog, public AS $$
    SELECT a.content,b.content FROM public.benchmark_jobs j
      JOIN public.sources a ON a.sha256=j.candidate_hash
      JOIN public.sources b ON b.sha256=j.reference_hash WHERE j.id=jid AND j.claimant=session_user AND public.worker_authorized(j.environment)
      AND j.state='running' AND j.owner=worker AND j.token=attempt
      AND j.lease_until>clock_timestamp();
$$;

CREATE FUNCTION public.immutable_source() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='UPDATE' THEN RAISE EXCEPTION 'sources are immutable'; END IF;
    IF encode(sha256(NEW.content),'hex')<>NEW.sha256 THEN
        RAISE EXCEPTION 'source hash mismatch';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER immutable_source BEFORE INSERT OR UPDATE ON sources
FOR EACH ROW EXECUTE FUNCTION public.immutable_source();

CREATE FUNCTION public.immutable_measurement() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.request,NEW.request_hash,NEW.candidate_hash,NEW.reference_hash,NEW.environment)
       IS DISTINCT FROM
       (OLD.request,OLD.request_hash,OLD.candidate_hash,OLD.reference_hash,OLD.environment)
       OR (OLD.result IS NOT NULL AND NEW.result IS DISTINCT FROM OLD.result) THEN
        RAISE EXCEPTION 'measurement evidence is immutable';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER immutable_measurement BEFORE UPDATE ON benchmark_jobs
FOR EACH ROW EXECUTE FUNCTION public.immutable_measurement();

REVOKE ALL ON FUNCTION public.claim(text,text,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.heartbeat(bigint,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.complete(bigint,text,text,jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.fail(bigint,text,text,jsonb) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.sources(bigint,text,text) FROM PUBLIC;
REVOKE ALL ON FUNCTION public.immutable_source() FROM PUBLIC;
REVOKE ALL ON FUNCTION public.immutable_measurement() FROM PUBLIC;
