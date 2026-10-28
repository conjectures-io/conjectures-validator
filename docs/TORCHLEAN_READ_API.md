# TorchLean dashboard API

Base: `/v1/competitions/torchlean`. Public reads; no registration or session required.
No pagination. Disabled/unconfigured competitions return 404; unreachable or incompatible
competition databases return 503. The OpenAPI snapshot describes the exact response schemas.

| GET path | Response |
|---|---|
| empty path | Competition overview; `boards`, upload limit/open state, policy and queue depth. Also listed in `/v1/competitions`. |
| `/health` | `ok`, `policy_ready`, counts of queued/proving/waiting/benchmarking submissions, published rank shares and pause policy. |
| `/boards` | Active board definitions: model weights, environment weights/identities, promotion bar, guard, reward share, current promotion and pause information. |
| `/leaderboard?board=mlp` | Current reference and challengers evaluated against it under the active benchmark configuration. |
| `/leaderboard?board=mlp&promotion_id=21` | Challenger history against that promotion on that board. Optional `config_hash` selects a particular benchmark configuration. |
| `/champions?board=mlp` | All promotions newest first, current status, disqualification, reward rank/share, fraction of the competition allocation and source availability. |
| `/submissions?state=queued&limit=25` | Submission list, newest first, one page at a time: `limit` 1–100 (default 25); pass the response's `next_cursor` back as `cursor` with the same `state`, until it is null. Optional processing-state filter. Includes identity, reception time, source availability and per-board progress. |
| `/submissions/45` | One submission, proof state/stage/reason and evaluations with job counts and stored decisions. |
| `/submissions/45/report` | Submission plus per-evaluation policy, per-device/seed measurements, calibrated repetitions, paired cell timings/checksums, stored scores and guards. |
| `/submissions/45/source` | Raw `Kernel.lean` attachment, only for former champions whose source is no longer current on any board; otherwise 403 `SOURCE_WITHHELD`. |

`board` defaults to `overall`. Unknown boards/promotions/submissions return 404. Board names
come from coordinator configuration. `promotion_id` is a promotion ID, not a submission ID.
`generation` is an evaluation retry/replanning number, not a global board version.

Leaderboard results never combine different references or benchmark configurations. For a historical
reference the latest available configuration is selected by default; use the configuration hash
from submission evaluations to inspect an earlier one. Removed boards retain champion history and
explicit historical leaderboard reads. The shipped reference has null submission/promotion IDs
and `shipped: true`; no fake baseline submission is created.

Proof states are `queued`, `running`, `proved`, `rejected`. Processing states are `queued`,
`proving`, `waiting` (proved but awaiting board planning), `benchmarking`, `completed`, `rejected`, `disqualified` (operator source exclusion).
A completed evaluation has a recorded decision; completion does not imply promotion. Superseded
evaluations stay visible in submission reports but are omitted from leaderboard challengers.

Admission fields (`outcome`, `score`, `gain_lower`, `guard_passed`, optional `reason`) are stored
coordinator decisions. `gain_lower` is a one-sided bound, not a two-sided confidence interval.
Pending data is null, empty lists mean no records. The API never runs bootstrap or re-scores timings.

Reward ranks/shares come from the current coordinator publication. `competition_fraction` is
board allocation × rank share, or zero when that board is paused with treasury policy. It is
not an on-chain payment guarantee: registration eligibility and chain publication are separate.
`competition_share` is null because the current coordinator does not publish its fraction of
validator weight. No hardcoded 20% is presented as authoritative.

Sources are checked by hash across all board histories, including retained inactive-board history.
Never-champion submissions and any code still holding a board remain private, even through another
hotkey/submission. Downloads use `Cache-Control: no-store`; a disqualification can restore an older
champion and withhold its source again. Reports expose only explicit public fields, never raw proof
logs, worker credentials, claim tokens, machine paths or raw worker error payloads.

Each read pins a single PostgreSQL repeatable-read, read-only snapshot. No transaction spans the
central and competition databases.

## Rollout

Deploy the companion competition changes that publish each board’s `config_hash` and
`initial_reference`, and re-run `deploy/platform/grant.sh` from that repository. The API role also
needs read access to coordinator public configuration and source-disqualification status; it still
cannot change proofs, results, promotions or leadership. Current leaderboards return an explicit
503 until the coordinator has published board hashes. No new database schema migration is needed
for these read endpoints; intake still requires `tensor_0002`.
