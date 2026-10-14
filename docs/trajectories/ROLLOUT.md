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
- Each newly admitted reward target starts at 2.5% of the available Alpha treasury (its
  stored starting share, V044) and grows to 12.5% over 15 days, the same curve as every target.
  Submission locks the amount. Competition for the same target reserves the maximum competing
  lock.
- New-policy full bounties require the chat logs that produced the proof, uploaded before the
  proof is submitted (the proof is refused without them) and accepted by a reviewer. Rejected
  or withdrawn logs forfeit the bounty through a binding `CHAT_LOGS_NOT_ACCEPTED` proof
  rejection, which reopens the target. The DB trigger
  enforces the exact proof, target, current human decision and recorded policy snapshot when
  creating or changing an instruction. Existing commands/notifications recheck that evidence;
  chain reconciliation still records transfers that actually occurred after a review correction.
- Qualifying full-bounty proofs submitted before the configured launch cutoff can receive an
  additional $500 USD for Erdős problems, $250 for every other source family, for accepted logs.
  Existing proof payouts remain independent. Source classification uses the original bundle.
  The retrospective claim window is 365 days from cutoff; required new-bounty evidence is exempt.
  The chat must show the proof before that proof's submission time.
- Every paid log carries a required license to sell it to buyers, effective on acceptance; logs
  are never published openly, by us or buyers. Terms are plain language (`chat-log-terms-v1.md`).
  Historical bonuses are paid in Subnet 66 Alpha worth the USD amount at payment time.
- The bonus cutoff and the terms' effective date are the go-live moment, stamped in
  `chat_log_program` by `scripts/open_chat_log_intake.py`. It can be corrected with
  `--correct-to` only alongside a permanent correction record; claims keep their own cutoff.
  Published submission terms remain v7;
  v8 is `docs/policies/submission-terms-v8.md`, published at go-live through `SUBMISSION_TERMS_PATH`,
  `SUBMISSION_TERMS_VERSION=v8` and `SUBMISSION_TERMS_EFFECTIVE_FROM` set to the go-live date.

## Prepared, not published

The release is being prepared only. No production migrations, intake opening, merges, task
activation, transfer, buyer delivery or announcement are authorized by this preparation.
The consolidated handoff is [the release checklist](../releases/2026-10-13.md).

## Deployment sequence

1. Coordinate with PR119 and the unmerged task/source release work. Keep writes paused across
   the migration/application switch; an old application must not write new-policy submissions.
2. Before V046, request `/v1/catalog/meta` from the old API and verify that all currently
   published stable reward-target IDs are present in `bounty_tasks`. That endpoint prices and
   registers the complete current catalog. Preserve/export this boundary for rollback review.
   V046 additionally backfills every previously submitted target, including retired targets,
   before marking existing rows `legacy`. Never prime P34/P35 as legacy.
3. Apply V044–V049 through the normal migration process. Before restarting ANY API,
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
   requirement, not an intake-enable step. V045 and V046 are unpublished drafts from
   the migrated feature; any private environment that applied an earlier draft must be rebuilt
   or separately reconciled rather than assuming Flyway accepts modified checksums.
4. Preserve original task versions used by historical submissions. A missing/corrupt bundle
   blocks tier classification; it must not silently fall back to $250. Account-less historical
   submissions still need verified ownership migration before private account intake.
5. Pin the approved chat-log terms digest, configure private storage/access/backup retention,
   review staffing and purge scheduling. V048 adds the historical payment ledger and private
   buyer delivery; follow [FULFILLMENT.md](FULFILLMENT.md). Grant the watcher EXECUTE on
   `trajectory_bonus_transfer_reserved(text,text,text,bigint,timestamptz)` under its trusted
   function owner before restarting reconciliation. Keep that owner able to SELECT
   `trajectory_payments`; payout roles still get no private package/claim access.
6. When staging passes, run `scripts/open_chat_log_intake.py` (the cutoff), then set the
   backend intake flag and matching frontend flag. Rebuild the frontend; assets reside in
   `data/trajectory` and are served only while enabled.
7. Release P34/P35 with real generated task bundles, immutable source/type hashes, the source
   catalog, tier selection evidence and a verifier image containing that exact source. The
   frozen packet here is an admission record and compile-checked mathematical source, not a
   replacement for the task-pool release manifest. Extend the research-family registry explicitly;
   do not relabel these as existing Math15/Math30 targets or copy unregistered tasks into production.
8. Verify public quotes/terms, owner upload, reviewer state transitions and an unsigned payout
   instruction in staging before production activation. Use synthetic data only in tests.

Rollback must preserve accepted quotes and consent history. Do not drop V045/V046 tables or
reassign a target's policy after accepting submissions. Pause intake/new targets and roll forward
with a reviewed fix when commitments exist. Never treat evidence approval as proof of payment.

## Cutover and rollback constraints

The owner-approved default applies to every target first published after this cutover, including
pending releases such as the 259-target release. A review date alone does not make an unpublished
target legacy. Before V046, export and review the exact currently published target list and its
`bounty_tasks` rows, including all submitted retired targets. Record pending releases explicitly:
those released after the boundary start at 2.5% and require logs; any exception needs a separate owner
policy decision before admission. Catalog reads register that approved default durably. Production
must use the default pricer; `new_target_policy="legacy"` is a test/old-binary compatibility option,
not a production cutover override. UPDATE of target identity/policy and DELETE are rejected.

Deploy with intake disabled first: new-policy catalog quotes return
`CHAT_LOG_REVIEW_UNAVAILABLE` and no submission fee is consumed. The extrinsic-only endpoint
refuses new-policy targets even after launch; contributors must use an authenticated account
submission endpoint to retain private evidence ownership. Legacy extrinsic submissions still work.

Before enabling intake, follow the README go-live steps and complete the historical
source/ownership inventory. The missing-log rule is
settled: logs are due before the proof, so there is no deadline to track. Publish final v8 through an explicit versioned `SUBMISSION_TERMS_PATH`, version and
actual effective date; coordinate matching frontend assent and archived v7. Do not change the
bundled v7 contract merely to deploy disabled code.

Once V046 or any new-policy submission exists, do not roll the application/notifier back to an
older binary: its batch does not understand mandatory evidence. Pause new intake and roll forward
with a reviewed fix; preserve all policy, consent and payout rows.

The watcher also needs EXECUTE on `public.trajectory_bonus_oldest_pending(text,text)` under
the same trusted owner with SELECT on `trajectory_payments`. This opens chain tracking when a
historical bonus is the only outstanding obligation.

## Every open target requires logs (October 13, 2026)

Owner decision after launch: chat logs are required for every open target, not only new ones.

- V050 moves every `legacy` row in `bounty_tasks` to `linear-age-v3-chat-logs`, keeping
  `opened_at` and the starting share, so bounty amounts do not change. Submissions keep the
  policy recorded on them. The V046 immutability trigger is disabled for that one statement only.
- Coldkey-signed submissions (`POST /v1/submissions`, the `conjectures` CLI) take the logs the
  signer's linked account deposited, refusing before the payment is spent. A CLI bearer token may
  deposit logs (`POST /v1/me/chat-log-deposits`); the bonus upload and withdrawal stay browser-only.
- Publish `docs/policies/submission-terms-v9.md` (`SUBMISSION_TERMS_VERSION=v9`) and
  `chat-log-terms-v2.md`; set `TRAJECTORY_LAUNCH_APPROVED_TERMS_SHA256` to the v2 digest in
  `submission_api/trajectory_terms.py`, and ship the web's `data/trajectory` copy with it.

## Requested revisions of required logs (October 14, 2026)

Owner decision: incomplete required logs can be sent back instead of rejected.

- A reviewer may decide `NEEDS_CHANGES` on required logs below the third version; the target
  stays held. The contributor uploads a new version (`POST /v1/me/submissions/{id}/trajectory`,
  from the browser or a CLI token, by the account that deposited the logs), which must still
  show the proof before the submission's time. The third version must be approved or rejected.
- V051 replaces `trajectory_required_logs_fixed` with `trajectory_deposit_first_version`.
- Publish `docs/policies/submission-terms-v10.md` (`SUBMISSION_TERMS_VERSION=v10`,
  `SUBMISSION_TERMS_EFFECTIVE_FROM=2026-10-14`) and `chat-log-terms-v3.md`, with
  `TRAJECTORY_LAUNCH_APPROVED_TERMS_SHA256` set to the v3 digest and the web copy shipped with it.

## Reasoning kept where readable (October 14, 2026)

Owner decision: packages keep the model's reasoning wherever the session stored it as readable
text, shown to the user or not. Reasoning a tool kept encrypted or withheld stays a `redacted`
event, and the packaging agent never decrypts, reconstructs or regenerates it. Publish
`chat-log-terms-v4.md` and `docs/policies/submission-terms-v11.md` (`SUBMISSION_TERMS_VERSION=v11`),
set `TRAJECTORY_LAUNCH_APPROVED_TERMS_SHA256` to the v4 digest, and ship the web's
`data/trajectory` copy (instructions, terms, manifest) with it.
