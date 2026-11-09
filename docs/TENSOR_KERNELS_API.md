# Tensor kernels API

Tensor kernels is TorchLean's successor. It runs the same competition code (signed
`Kernel.lean` intake, proof gate, model-family boards, paired benchmarks) with more kernels,
a `recurrent` board, real-model workloads and a baseline yardstick. The central API serves it
exactly like TorchLean, under its own slug and from its own database:

    /v1/competitions/tensor-kernels/...

Every route, request and response shape is TorchLean's. See [TORCHLEAN_API.md](TORCHLEAN_API.md)
for intake and [TORCHLEAN_READ_API.md](TORCHLEAN_READ_API.md) for reads. The two competitions
share nothing at request time: not the database, not the switches, not the request-rate budget.
A signature made for one is refused by the other, because the slug is part of the signed message.

| Method and path (relative to `/v1/competitions/tensor-kernels`) | Purpose |
|---|---|
| `POST /submissions` | Signed upload of one `Kernel.lean` |
| `GET` (the base path) | Overview, upload state, queue depth, boards |
| `GET /health` | Policy readiness and processing counts |
| `GET /boards` | Active boards, weights, thresholds, references |
| `GET /leaderboard?board=overall` | Challengers against a board's reference |
| `GET /champions?board=overall` | Promotion history and reward ranks |
| `GET /submissions` | Paged submissions, optionally by `state` |
| `GET /submissions/{id}` | Proof status and per-board decisions |
| `GET /submissions/{id}/report` | Timing evidence and recorded decisions |
| `GET /submissions/{id}/source` | `Kernel.lean`, once it is a former champion on every board |

## Replacing TorchLean

Tensor kernels takes over TorchLean's whole share of the subnet weights. Its weight setter
replaces TorchLean's: there is exactly one `set_weights` caller per hotkey. The API publishes
no weights, so this needs no API change. At the switch, close TorchLean's intake
(`TORCHLEAN_SUBMISSIONS_ENABLED=0`) and keep `torchlean` in `COMPETITIONS`. Its routes keep
serving its history from its own database, read-only, until the operators retire it.

## Configuration

```dotenv
COMPETITIONS_ENABLED=1
COMPETITIONS=torchlean,tensor-kernels
COMPETITION_TENSOR_KERNELS_DATABASE_URL=postgresql+psycopg://platform_api:URL_ENCODED_PASSWORD@tensor-kernels-db:5432/tensor_kernels
TENSOR_KERNELS_SUBMISSIONS_ENABLED=1
TENSOR_KERNELS_MAX_SOURCE_BYTES=1048576
TENSOR_KERNELS_REGISTRATION_CUTOFF=2026-11-16T00:00:00Z
```

These are TorchLean's settings with the `TENSOR_KERNELS_` prefix, and they are checked the
same way. Uploads need a timezone-qualified cutoff. `TENSOR_KERNELS_DEV_SKIP_REGISTRATION` is
refused in production. The database URL must differ from every other competition's.
`SUBMISSIONS_PAUSED` pauses this intake too.

## What the season adds to the reads

Every addition is optional. TorchLean's responses carry them as null (or `shared: false`).

- **Decision** (`evaluations[].decision`): `suite_scores` holds the time ratio per benchmark
  suite (`synthetic`, `real`). `score` combines them by the plan's suite weights.
- **Report, per evaluation**: `suites`, `suite_weights`, `real_workloads` (id, family, model,
  dataset, adapter, asset revision, asset hashes, sample counts; never file paths) and
  `baseline_sha256`.
- **Report, per measurement**:
  - `shared`: the job timed the entry once for several boards (a comparison bundle). The rounds,
    repetitions and evidence shown are the ones against *this* board's reference. When a shared
    job fails, boards that had finished keep their full results and no `timeout`. The board that
    was running shows its partial evidence and the timeout. Boards the job had not reached show
    nothing.
  - `suite_scores` and `real_family_scores`: this environment/seed's part of the decision.
  - `baseline`: the baseline source's unpaired per-cell medians and `candidate_to_baseline` /
    `reference_to_baseline` ratios. This is a yardstick only. Admission compares the candidate
    with the reference.
  - `real`: per real workload, candidate/reference/baseline medians and ratios; per family, the
    equal-weight geometric means.
  - `guards` may now contain `real:<workload>` keys.

## Rollout prerequisites

- **Competition repository**: the miner client and the gate must sign the slug `tensor-kernels`.
  Today `COMPETITION` in `miner/sig.py` and `validator/service/sig.py` is still `torchlean`.
- **Competition database**: a new database, not TorchLean's. Apply migrations through
  `tensor_0007` and run that repository's `deploy/platform/grant.sh` for `platform_api`.
- **Not served yet**: live benchmark progress (`benchmark_job_progress`, `tensor_0005`).
  `platform_api` has no grant on it.
