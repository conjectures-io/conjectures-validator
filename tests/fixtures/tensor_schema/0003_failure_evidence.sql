-- A failed job keeps what the worker reported: a timeout's evidence and the measurement it had
-- reached by then, not only the failure's kind and reason. Only those keys, and at most 1 MiB of
-- them: an oversized report drops the measurement, then the timeout, and says so, rather than
-- losing the failure itself.
CREATE OR REPLACE FUNCTION public.fail(jid bigint, worker text, attempt text, failure jsonb)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
DECLARE
    evidence jsonb := jsonb_build_object();
BEGIN
    IF failure IS NULL OR failure->>'kind' IS NULL
       OR failure->>'kind' NOT IN ('candidate','reference','infrastructure','checksum')
       OR failure->>'reason' IS NULL THEN RAISE EXCEPTION 'invalid failure'; END IF;
    IF jsonb_typeof(failure->'timeout')='object' THEN
        evidence := evidence || jsonb_build_object('timeout',failure->'timeout');
    END IF;
    IF jsonb_typeof(failure->'partial')='object' THEN
        evidence := evidence || jsonb_build_object('partial',failure->'partial');
    END IF;
    IF octet_length(evidence::text)>1048576 THEN
        evidence := (evidence - 'partial') || jsonb_build_object('truncated',true);
    END IF;
    IF octet_length(evidence::text)>1048576 THEN
        evidence := jsonb_build_object('truncated',true);
    END IF;
    UPDATE public.benchmark_jobs SET
        state=CASE WHEN failure->>'kind'='candidate' THEN 'failed' ELSE 'queued' END,
        error=evidence || jsonb_build_object('kind',failure->>'kind','reason',left(failure->>'reason',2000)),
        due_at=clock_timestamp()+make_interval(secs=>least(86400,retry_seconds*power(2,least(attempts-1,4)))),
        claimant=NULL,owner=NULL,token=NULL,lease_until=NULL
      WHERE id=jid AND state='running' AND claimant=session_user
        AND public.worker_authorized(environment) AND owner=worker AND token=attempt
        AND lease_until>clock_timestamp();
    RETURN FOUND;
END $$;

-- Decisions taken before this recorded a candidate that ran out of time as `benchmark-failed`,
-- with only the reason to say why. Relabel them `timed-out`, with what the reason and the failed
-- job still show; what the worker measured before the timeout was never kept. The one decision an
-- operator wrote by hand names the exhausted budget instead of the usual reason.
WITH timed_out AS (
    SELECT e.id,
        coalesce(substring(e.decision->>'reason' from '^(benchmark|build)-timeout: '),
                 'benchmark') AS phase,
        substring(e.decision->>'reason' from 'exceeded its ([0-9.]+)s time allowance')::float8
            AS allowance,
        j.environment, j.seed,
        (j.request->'timing'->>'run_seconds')::float8 AS run_seconds,
        (j.request->'timing'->>'benchmark_seconds')::float8 AS budget
    FROM evaluations e
    LEFT JOIN LATERAL (
        SELECT ej.environment, ej.seed, b.request
        FROM evaluation_jobs ej JOIN benchmark_jobs b ON b.id=ej.job_id
        WHERE ej.evaluation_id=e.id AND b.state='failed' AND b.error->>'kind'='candidate'
        ORDER BY b.error->>'reason' IS NOT DISTINCT FROM e.decision->>'reason' DESC, b.id
        LIMIT 1
    ) j ON true
    WHERE e.decision->>'outcome'='benchmark-failed'
      AND (e.decision->>'reason' ~ '^(benchmark|build)-timeout: '
           OR e.decision->>'reason' ~ 'exhausted the shared measurement budget')
)
UPDATE evaluations e SET decision = e.decision || jsonb_build_object(
    'outcome', 'timed-out',
    'timeout', jsonb_build_object(
        'phase', t.phase,
        'limit', CASE WHEN t.phase<>'benchmark' THEN NULL
                      WHEN t.allowance=t.run_seconds THEN 'invocation'
                      ELSE 'budget' END,
        'allowance_seconds', t.allowance,
        'budget_seconds', CASE WHEN t.phase='benchmark' THEN t.budget END,
        'environment', t.environment,
        'seed', t.seed))
FROM timed_out t WHERE e.id=t.id;
