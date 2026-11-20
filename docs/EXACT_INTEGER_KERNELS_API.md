# Exact integer kernels API

Exact integer kernels is the third kernel competition. A miner writes one C file, `entry.c`,
with three correctly rounded float functions in integer arithmetic. The validator scores it by
instructions counted on emulated embedded targets (`rv32im`, `thumbv6m`) and checks it on every
one of the 2^32 inputs. Its coordinator (`exact-integer-kernels`) writes Tensor kernels' schema
with the Lean parts removed, so the central API serves it as a third slug of the same kind,
from its own database:

    /v1/competitions/exact-integer-kernels/...

The routes are Tensor kernels' (see [TENSOR_KERNELS_API.md](TENSOR_KERNELS_API.md),
[TORCHLEAN_API.md](TORCHLEAN_API.md) and [TORCHLEAN_READ_API.md](TORCHLEAN_READ_API.md)), with
three differences: the upload is `entry.c`, its cap is 262,144 bytes, and the report carries
instruction counts and exhaustive checks instead of timing rounds. The database contract is the
coordinator's `coordinator/CONTRACT.md`. Nothing is shared with the other kernel competitions at
request time: not the database, the switches or the request-rate budget. A signature made for one
is refused by the others, because the slug is part of the signed message.

| Method and path (relative to `/v1/competitions/exact-integer-kernels`) | Purpose |
|---|---|
| `POST /submissions` | Signed upload of one `entry.c` |
| `GET` (the base path) | Overview, upload state and limit, queue depth, boards |
| `GET /health` | Policy readiness and processing counts |
| `GET /boards` | `rv32im`, `thumbv6m`, `overall`: targets scored, reward shares, bar |
| `GET /leaderboard?board=overall` | Challengers against a board's champion |
| `GET /champions?board=overall` | Promotion history and reward ranks |
| `GET /submissions` | Paged submissions, optionally by `state` |
| `GET /submissions/{id}` | Gate status and per-board decisions |
| `GET /submissions/{id}/report` | Instruction counts, checks and recorded decisions |
| `GET /submissions/{id}/source` | `entry.c` (`text/x-c`), once it is a former champion on every board and not embargoed (below) |

## Intake

`POST /submissions` takes `multipart/form-data` with exactly one file part, named `entry.c`:
non-empty UTF-8, at most `EXACT_INTEGER_KERNELS_MAX_SOURCE_BYTES` (default and maximum 262,144,
the gate's `CAP_SOURCE_BYTES`; the database's `ck_sources_size` is the same number). A part named
`Kernel.lean` is refused with 400, as `entry.c` is by the other two slugs. The headers and the
signed message are TorchLean's, with `competition: exact-integer-kernels`:

    conjectures-competition-submit-v1
    competition: exact-integer-kernels
    digest: <sha256 of entry.c, hex>
    hotkey: <ss58 hotkey>
    timestamp: <unix seconds>

`X-Conjectures-Hotkey`, `X-Conjectures-Timestamp` and `X-Conjectures-Signature` (hex sr25519)
carry it. Registration, entitlement and duplicate rules are Tensor kernels'. The miner kit's
`miner/submit.py` sends exactly this.

`proof_state` keeps Tensor's names. Here `proved` means accepted by `eik gate`. A gate rejection
is `rejected`, with the gate's `stage` (for example `ast`) and its reasons joined by `; ` in
`reason`.

## Reads

Boards, leaderboard, champions, submissions, health and the overview read the same tables as
Tensor kernels and need no change. A board's `models` is the targets it scores
(`overall: {rv32im: 0.5, thumbv6m: 0.5}`, a geometric mean). The `ratio` of a challenger or
champion is the decision's `score`: `entry_S / champion_S` on that board, lower is better. In
the overview, `files` names `entry.c`, `metric_definitions` labels the ratio "Score / champion",
and `execution_limits` and `environment_limits` are empty, because nothing is timed.

`evaluations[].decision` on a submission keeps the shape the other kernel competitions use
(`outcome`, `score`, `reason`). `outcome` is `qualifies`, `above-bar`, `benchmark-failed` or
`check-failed`. The report has the full decision.

### `GET /submissions/{id}/report`

```json
{
  "submission": { "...": "as GET /submissions/{id}" },
  "evaluations": [{
    "...": "the evaluation, as on the submission",
    "seed": 20261118,
    "decision": {
      "outcome": "qualifies", "score": 0.10, "reason": null, "stage": null, "reasons": null,
      "entry_S": 14891.6, "champion_S": 146334.9, "champion_sha256": "14329f1e…", "seed": 20261118,
      "fullcheck": {
        "verdict": "passed",
        "functions": {"exp": {"chunks_covered": 4096, "words_checked": 4294967296}},
        "targets": {"rv32im": {"exp": {"chunks_covered": 4096, "words_checked": 4294967296}}},
        "required_targets": ["rv32im"]
      }
    },
    "job_results": [
      {"kind": "compare", "function": null, "target": null, "chunks": null, "state": "completed",
       "compare": {
         "scores": {
           "rv32im": {"S": 12326.2, "bytes": 16600, "size_factor": 1.058, "reported_only": false,
                      "functions": {"exp": {"geo_mean_instr": 38988.8, "tail_factor": 1.0}}},
           "thumbv7em": {"S": 6830.4, "...": "...", "reported_only": true},
           "overall": {"S": 14891.6, "bytes": null, "size_factor": null, "functions": null,
                       "reported_only": false}
         },
         "inputs_sha256": "064b9220…",
         "images": {"rv32im": "fe7cefc2…", "thumbv6m": "f9500ac4…", "thumbv7em": "fe4b4999…"},
         "boards": {"overall": {"entry_S": 14891.6, "champion_S": 146334.9,
                                "champion_sha256": "14329f1e…", "ratio": 0.10, "beats": true}}
       },
       "check": null},
      {"kind": "fullcheck", "function": "exp", "target": null, "chunks": [0, 512],
       "state": "completed", "compare": null,
       "check": {"verdict": "passed", "stage": null, "reasons": [], "words_checked": 536870912,
                 "mismatches": 0, "examples": [], "seconds": 547.2, "budget_seconds": 3840}},
      {"kind": "targetcheck", "function": "exp", "target": "rv32im", "chunks": [0, 512],
       "state": "queued", "compare": null, "check": null}
    ]
  }]
}
```

- **Per evaluation**: the plan's `seed` and the coordinator's whole decision.
- **Per job**, every job linked to the evaluation, in the order the coordinator created them:
  the request's `kind`, `function`, `target` and `chunks` (`[lo, hi)` of the 4,096 chunks of
  2^20 inputs) and the job's `state`. The compare job measures the entry against every board's
  champion at once, so it appears under each board.
- **`compare`**, from a completed compare job: the entry's scores per target and `overall`
  (`thumbv7em` is measured but `reported_only`), the digest of the inputs drawn from the seed,
  the entry's compiled images, and each board's comparison. `boards` and `scores` are null when
  the entry's measurement was rejected; the decision then has the `stage` and `reasons`.
- **`check`**, from a completed `fullcheck` (host) or `targetcheck` (QEMU) shard: its verdict,
  stage and reasons, the inputs checked, the wrong outputs (`mismatches`, and the first few as
  hex `input`/`got`/`want`), and the time it took against its budget.
- A job that is not `completed` shows no result. A rejected entry is a completed job with a
  rejected verdict, not a failed job.

Never published: the champions' own measurements in a compare report, harness fingerprints,
the hard-input pool, compiled image digests of check shards, job errors and lease fields. The
source route publishes a former champion's `entry.c` on the same terms as Tensor kernels: never
while it holds any board. It also withholds a source listed in the competition database's
`source_embargoes`: the coordinator lists a dethroned source there while it contains most of a
current champion's code (a sibling build of the same file, say), in the same transaction as the
promotion that dethroned it. The API reads only `source_hash`, and only where the table exists;
the other kernel competitions have none and are unchanged.

## Configuration

```dotenv
COMPETITIONS_ENABLED=1
COMPETITIONS=torchlean,tensor-kernels,exact-integer-kernels
COMPETITION_EXACT_INTEGER_KERNELS_DATABASE_URL=postgresql+psycopg://platform_api:URL_ENCODED_PASSWORD@exact-integer-kernels-db:5432/exact_integer_kernels
EXACT_INTEGER_KERNELS_SUBMISSIONS_ENABLED=1
EXACT_INTEGER_KERNELS_MAX_SOURCE_BYTES=262144
EXACT_INTEGER_KERNELS_REGISTRATION_CUTOFF=2026-11-23T00:00:00Z
```

These are Tensor kernels' settings with the `EXACT_INTEGER_KERNELS_` prefix, checked the same
way. Uploads need the slug listed in `COMPETITIONS` and a timezone-qualified cutoff.
`EXACT_INTEGER_KERNELS_MAX_SOURCE_BYTES` may be lowered, never raised above 262,144.
`EXACT_INTEGER_KERNELS_DEV_SKIP_REGISTRATION` is refused in production, at startup and again
per request. The database URL must differ from every other competition's. `SUBMISSIONS_PAUSED`
pauses this intake too.

## Rollout

The code ships dark. Until `exact-integer-kernels` is listed in `COMPETITIONS`, every route
under `/v1/competitions/exact-integer-kernels` answers 404 and the competition is absent from
`/v1/competitions`. The switch is a configuration change, in this order:

1. **Competition database**: create it and migrate it to the coordinator's head
   (`python -m coordinator.migrate`). Then apply the coordinator's
   `coordinator/migrations/platform_api.sql` to it as the database owner. That is Tensor
   kernels' grant list without the audit tables, and it changes privileges only. Do not run a
   grant script with `PLATFORM_API_PASSWORD` set: it rotates the password of the cluster-wide
   `platform_api` role, which the other kernel competitions' URLs also use. The suite runs
   against copies of both files (`tests/fixtures/eik_schema`); refresh them, and
   `coordinator_rows.json`, when the coordinator's schema or output changes.
2. **Grants before the slug.** `/readyz` checks every configured competition database, and a
   deploy that fails it rolls back. A database the role cannot read yet is a failed deploy, so
   set `COMPETITION_EXACT_INTEGER_KERNELS_DATABASE_URL` and add the slug to `COMPETITIONS` only
   once step 1 is done.
3. **Open intake** with `EXACT_INTEGER_KERNELS_SUBMISSIONS_ENABLED=1` and the cutoff, once the
   coordinator and workers are running. Until then the reads serve an empty competition and
   uploads answer 403 `SUBMISSIONS_CLOSED`.
