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
- Commercial rights remain a separate optional election. Published submission terms remain v7;
  v8 is inactive in `docs/policies/submission-terms-v8-draft.md` until its release gates are met.

## Deployment sequence

1. Coordinate with PR119 and the unmerged task/source release work. Keep writes paused across
   the migration/application switch; an old application must not write new-policy submissions.
2. Before V045, request `/v1/catalog/meta` from the old API and verify that all currently
   published stable reward-target IDs are present in `bounty_tasks`. That endpoint prices and
   registers the complete current catalog. Preserve/export this boundary for rollback review.
   V045 additionally backfills every previously submitted target, including retired targets,
   before marking existing rows `legacy`. Never prime P34/P35 as legacy.
3. Apply V044 and V045 through the normal migration process. Before restarting ANY API,
   notifier or command-rendering process, even with intake disabled, list their actual DB roles
   and the migration owner. Keep BOTH `public.bounty_trajectory_eligible(uuid,text)` and
   `public.require_bounty_trajectory()` under the SAME trusted non-superuser owner. That owner
   needs SELECT on `public.submissions`, `public.review_decisions`, `public.trajectory_claims`,
   and UPDATE on at least one `submissions` column for `FOR UPDATE` (for example `UPDATE(id)`).
   Verify both owners and `SELECT has_function_privilege(proowner,
   'public.bounty_trajectory_eligible(uuid,text)', 'EXECUTE') FROM pg_proc WHERE oid =
   'public.require_bounty_trajectory()'::regprocedure`. Never transfer only one function or
   assume a SELECT-only owner can execute the row-locking trigger. For each actual caller,
   explicitly `GRANT EXECUTE ON FUNCTION public.bounty_trajectory_eligible(uuid,text)` to that
   role, then verify `has_function_privilege(role, 'public.bounty_trajectory_eligible(uuid,text)',
   'EXECUTE')`. Never grant private claim-table access to payout roles. Run the notifier startup
   probe and a read-only command query before resuming legacy payouts. This grant is a migration
   requirement, not an intake-enable step. V044 and V045 are unpublished drafts from
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

## Cutover and rollback constraints

The owner-approved default applies to every target first published after this cutover, including
pending releases such as the 259-target release. A review date alone does not make an unpublished
target legacy. Before V045, export and review the exact currently published target list and its
`bounty_tasks` rows, including all submitted retired targets. Record pending releases explicitly:
those released after the boundary use 2.5% and required logs; any exception needs a separate owner
policy decision before admission. Catalog reads register that approved default durably. Production
must use the default pricer; `new_target_policy="legacy"` is a test/old-binary compatibility option,
not a production cutover override. UPDATE of target identity/policy and DELETE are rejected.

Deploy with intake disabled first: new-policy catalog quotes return
`CHAT_LOG_REVIEW_UNAVAILABLE` and no submission fee is consumed. The extrinsic-only endpoint
refuses new-policy targets even after launch; contributors must use an authenticated account
submission endpoint to retain private evidence ownership. Legacy extrinsic submissions still work.

Before enabling intake, complete every README launch blocker, including the required-log deadline
and audited release policy, persisted historical cutoff, and real historical-source/ownership
inventory. Publish final v8 through an explicit versioned `SUBMISSION_TERMS_PATH`, version and
actual effective date; coordinate matching frontend assent and archived v7. Do not change the
bundled v7 contract merely to deploy disabled code.

Once V045 or any new-policy submission exists, do not roll the application/notifier back to an
older binary: its batch does not understand mandatory evidence. Pause new intake and roll forward
with a reviewed fix; preserve all policy, consent and payout rows.
