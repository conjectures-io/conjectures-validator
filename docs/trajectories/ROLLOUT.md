# Server migration and rollout

The chat-log implementation was recovered from the source edits in “Build proof chat bonus”
(`01a131bd-d4ad-750a-bc83-55fea9efa0d8`, implementation chat
`01a131bf-e203-7407-977e-127e52c780db`) and its final remediation pass. The server now owns
these checkouts. Original validator base: `ddc02f1473a269507572c4bcd0035fb11ad98905`, with
PR119 CI fixes carried forward; web base: `82c3cdc8d8c80f78df4c75b77d5d4a5dd6be0745`.
No production migration, task activation, transfer or announcement has occurred.

## Approved policy

- Admit P34/P35: 34 and 35 equal circles in a unit square, exact radical global upper bounds.
  The frozen definitions and owner decision are in `staged/packing-20261012/`.
  Disk cases 15/16/17/18/20 stay excluded. Admission does not certify novelty or prove optimality.
- Each newly admitted reward target offers 2.5% of the total finalized Alpha treasury.
  Quotes change with the treasury, not age; submission locks the amount. Competition for the
  same target reserves the maximum competing lock. New exposure cannot exceed available funds.
- New-policy full bounties require accepted authentic user-visible chat logs. The DB trigger
  enforces the exact proof, target, current human decision and recorded policy snapshot when
  creating or changing an instruction. Existing commands/notifications recheck that evidence;
  chain reconciliation still records transfers that actually occurred after a review correction.
- Qualifying full-bounty proofs submitted before the configured launch cutoff can receive an
  additional $500 USD for Erdős/Green, $250 for other source families, for accepted logs.
  Existing proof payouts remain independent. Source classification uses the original bundle.
  The retrospective claim window is 365 days from cutoff; required new-bounty evidence is exempt.
- Commercial rights remain a separate optional election. Submission terms advance to v8;
  published v7 is preserved in `docs/policies/submission-terms-v7.md`.

## Deployment sequence

1. Coordinate with PR119 and the unmerged task/source release work. Keep writes paused across
   the migration/application switch; an old application must not write new-policy submissions.
2. Before V045, request `/v1/catalog/meta` from the old API and verify that all currently
   published stable reward-target IDs are present in `bounty_tasks`. That endpoint prices and
   registers the complete current catalog. Preserve/export this boundary for rollback review.
   V045 additionally backfills every previously submitted target, including retired targets,
   before marking existing rows `legacy`. Never prime P34/P35 as legacy.
3. Apply V044 and V045 through the normal migration process. V044 is an unpublished draft from
   the migrated feature; any private environment that applied an earlier draft must be rebuilt
   or separately reconciled rather than assuming Flyway accepts modified checksums.
4. Preserve original task versions used by historical submissions. A missing/corrupt bundle
   blocks tier classification; it must not silently fall back to $250. Account-less historical
   submissions still need verified ownership migration before private account intake.
5. Complete the inherited intake launch requirements in README: finalized review/optional
   license text and pinned assent, private storage/access/backup retention, review staffing and
   purge scheduling. Production refuses draft terms. Historical bonus settlement and commercial
   activation still require the separate settlement ledger/adapter described there.
6. Configure an explicit timezone-aware cutoff, backend intake flag and matching frontend flag.
   Rebuild the frontend; assets reside in `data/trajectory` and are served only while enabled.
   Review-only evidence must remain eligible regardless of commercial-rights election.
7. Release P34/P35 with real generated task bundles, immutable source/type hashes, the source
   catalog, tier selection evidence and a verifier image containing that exact source. The
   frozen packet here is an admission record and compile-checked mathematical source, not a
   replacement for the task-pool release manifest. Extend the research-family registry explicitly;
   do not relabel these as existing Math15/Math30 targets or copy unregistered tasks into production.
8. Verify public quotes/terms, owner upload, reviewer state transitions and an unsigned payout
   instruction in staging before production activation. Use synthetic data only in tests.

Rollback must preserve accepted quotes and consent history. Do not drop V044/V045 tables or
reassign a target's policy after accepting submissions. Pause intake/new targets and roll forward
with a reviewed fix when commitments exist. Never treat evidence approval as proof of payment.
