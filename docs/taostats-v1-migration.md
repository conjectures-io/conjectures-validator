# Taostats v1 migration

Prepared 2026-10-12 against validator commit `4281663` in an isolated worktree.

## Provider contract

- [Migration guide](https://taostats.io/blog/api-migration-guide.md), published 2026-10-08:
  legacy `/api/` endpoints shut down **2026-11-09**. Existing Authorization keys work on v1.
- [OpenAPI](https://api.taostats.io/openapi.json): `/v1/price?asset=tao` returns
  `PriceEnvelope.data`, a one-element array with a decimal-string USD `price`.
- `/v1/subnets/pools?netuid=66&limit=1` returns `PoolsEnvelope.data`, an array with
  decimal-string Alpha/TAO `price`. No aggregate endpoint is needed for pricing.
- Parameters and the fields consumed by these callers remain unchanged. The raw Authorization
  header and explicit User-Agent remain unchanged.

Both paths returned HTTP 200 from the deployed notifier's environment using its existing key.
The responses each contained one row and a string price; the pool row identified netuid 66.
The key was kept inside the container and was neither printed nor copied locally.
Read-only inspection confirmed `conjectures_api` and `conjectures_payout_notifier` still have the
legacy URL constants and configured keys, with bounty netuid 66.

## Behavior

The API's bounty display and secondary TAO/USD invoice reader use the new routes, retaining
Decimal arithmetic, cache TTLs, and unavailable-result behavior. The notifier records the new
source URLs in newly quoted awards. Historical records are untouched.

A failure at the defect-award quote boundary logs the exception type and defers only those
awards. They remain eligible with no payout event and are retried on the next processing pass.
Locked full bounties and pending payout notifications continue. A successful pass shares one
fresh quote across the outstanding defect awards. The $750 policy, integer nano-Alpha conversion,
ROUND_HALF_UP rounding, pricing audit inputs, and event/delivery deduplication remain unchanged.
No stale-price or full-bounty fallback is introduced. Database failures are not suppressed.

## Validation

- 50 tests passed: `test_taostats.py`, `test_payout_pricing.py`, `test_payout_notifier.py`.
  These include actual PostgreSQL event/outbox tests, mocked notification delivery, both price
  legs, malformed/empty responses, invalid/nonfinite prices, HTTP errors, timeout, missing quoter,
  repeated outages, recovery, shared quotes and duplicate prevention.
- 35 tests passed: `test_rates.py` and `test_generate_payout_commands.py`.
- Repository-wide `ruff check .` with CI's Ruff 0.16.4 passed; `git diff --check` passed.
- Test dependencies used the relevant pins in `requirements-service.lock`. PostgreSQL 17 ran
  in a new disposable local container. The payout suite ran in Python 3.12 in the same Docker
  VM to avoid macOS/VM clock skew affecting pre-existing immediate-delivery tests.
- The full repository suite, Lean/toolchain checks and chain integration were not run for this
  scoped Python change. No production database writes, notifications, transfers, deployment,
  credential changes or merge were performed.

The validator checkout has unrelated local edits. None were imported or changed. No root
AGENTS.md or applicable `.agents/skills` existed in the inspected validator checkout; run and
CI instructions were read from `justfile`, `pyproject.toml`, and `.github/workflows/ci.yml`.
Local Codex memory stores on the validator and miner contained no matching Taostats/payout or
validator summaries relevant to this task.

## Rollout

1. Review/apply this commit to the intended release, preserving the deployed checkout's unrelated
   edits. Run release CI. There are no dependency, environment, credential or schema changes.
2. Through the normal approved release process, rebuild and roll out both API and payout-notifier
   images before November 9. This work does not deploy or merge them.
3. Confirm both running modules expose the v1 constants and both GETs return a unique positive
   price using the existing key. Check API display/invoice fallback and notifier logs.
4. Monitor deferred-defect warnings and confirm recovery creates one event per eligible award.
   Existing locked events must retain their recorded amounts and audit inputs.

No implementation blocker remains. Production rollout is still required.
