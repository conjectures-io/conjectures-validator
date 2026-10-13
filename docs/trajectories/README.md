# Proof chat logs: required with new bounties, bonus for earlier proofs

New targets start at 2.5% of the available Alpha treasury, growing to 12.5% over 15 days like
every bounty, and require the AI chat logs that
produced the proof, **uploaded before the proof is submitted** and accepted by a reviewer.
For qualifying proofs submitted before launch, accepted logs earn an additional
**$500 USD for Erdős problems, $250 USD for everything else**. Existing proof payouts keep
their terms. Every accepted log is licensed to Conjectures for sale to buyers; logs are never
published openly. The owner approved these rules on October 12, 2026.

Contributor terms are one plain-language page, [chat-log-terms-v1.md](chat-log-terms-v1.md)
(owner-reviewed October 13, 2026), with a single pinned assent statement
([assent-statements-v2.json](assent-statements-v2.json)). They take effect when intake opens.
`python scripts/trajectory_assets.py` regenerates the package schema, the synthetic
`example-package.zip`, the terms manifest and `submission_api/trajectory_terms.py` digests; copy
`docs/trajectories/*.json`, `example-package.zip`, the terms Markdown and `agent-instructions.md`
to the frontend's `data/trajectory/` and verify the hashes together.

## Owner policy

| Decision | Rule |
| --- | --- |
| Acceptance standard | The original chat logs that produced the proof, not a recreation of them |
| New bounties | Logs are deposited before the proof; the proof is refused without them (no credit spent); the deposit lasts 24 hours; no later addition or replacement |
| Missing or failed logs | Rejected or withdrawn logs forfeit the bounty and reopen the target; there is no grace period |
| Earlier proofs | $500 Erdős, $250 every other problem (Green's included), by the original task bundle, paid in Subnet 66 Alpha worth that USD amount at payment time; only proofs submitted before intake opened and approved for the full bounty; the chat must show the proof before its submission time; up to three versions; claims close 365 days after opening |
| Cutoff | The moment intake opens, stamped by `scripts/open_chat_log_intake.py` in `chat_log_program`. If something goes wrong it can be corrected (`--correct-to`); the database accepts a change only with a permanent `chat_log_program_corrections` record (previous moment, who, why) and refuses deletion. Claims keep the cutoff they were made under |
| License | Required for every paid log: permanent, worldwide, non-exclusive right to use, copy, sell and license to buyers (including AI training/evaluation), effective on acceptance; ends if acceptance is withdrawn because the proof is disqualified |
| Buyers | May use, including for training and evaluation, but may not publish the logs openly |
| Retention | Accepted logs kept for sale; others deleted after 90 days; unsubmitted deposits after 24 hours; minimal records seven years, or longer while they back a payout or accepted logs |
| Format | One .zip package up to 64 MiB (512 MiB unpacked, 20,000 files): README.md, events.jsonl, episodes.jsonl, sample.html, artifact-index.json, artifacts/, manifest.json, validation.json, as described in agent-instructions.md; the proof itself may be referenced rather than included |

## Contributor instructions

1. **New bounty:** on the submit page, choose the proof file first. The page shows the exact
   `task_id`, `task_bundle_sha256` and `final_proof_sha256` the logs must name, with
   `submission_id: null`. Upload the logs, then submit the proof within 24 hours.
   **Earlier proof:** sign in to the account owning a proof with a current human
   `REVIEW_APPROVED` decision and upload from that submission's page with its `submission_id`.
2. Give an AI agent [agent-instructions.md](agent-instructions.md) (the site's copy-paste
   message does this) with the sessions or export, the proof file and the values above. It builds
   `chat-log-package.zip`: the full observable record (messages, tool calls and results, failed
   attempts, files, subagents, usage), selected episodes and a readable sample.
   [chat-log-package.schema.json](chat-log-package.schema.json) describes the checked parts and
   [example-package.zip](example-package.zip) shows the layout; the example is refused by intake.
3. The final proof is one `artifact-index.json` entry linked to a dated event: that is when the
   chat produced the proof.

The server reads the zip in memory and never extracts it. It refuses unsafe paths, links,
encrypted entries, more than 20,000 files or 512 MiB unpacked, and checks: the required files
are present; `manifest.json` has the format and the four linkage values; `events.jsonl` parses
with unique ids, known roles, and timestamps no later than export; `artifact-index.json` entries
link real events and included files match their hashes; exactly one final proof matches the
proof digest and is linked to a dated event (before the submission time for earlier proofs).
README.md, episodes.jsonl and sample.html are not inspected and never rendered by the site. Timestamps in a file can be forged, so provenance (provider
export digest or share link) and duplicate review remain human work.

## Review

Reviewers attest two things before accepting (`Checklist` in `routers/trajectories.py`):
these are the original chat logs that produced the proof, not a recreation; and the
contributor owns the proof with no credentials or others' personal information left in. Accepting also requires a current human `REVIEW_APPROVED`
decision on the proof, which the claim binds.

- New-bounty logs can be accepted or rejected, never sent back for changes. Rejection waits
  until automatic verification finishes. If the proof is approved for the full bounty, or not
  yet decided, rejection records a binding `CHAT_LOGS_NOT_ACCEPTED` rejection of the proof
  through the ordinary decision functions, so the reward hold, treasury reservation and payout
  guards move exactly as for any rejected proof. Contributor withdrawal does the same. A
  formalization-defect award never needed logs and is unaffected.
- Appeal: an independent reviewer may reopen rejected logs while their content exists, unless
  another proof for the target has since qualified (`TRAJECTORY_TARGET_REASSIGNED`). Restoring
  the bounty also needs the proof decision corrected back to approval through review
  corrections, which re-checks the target holder.
- A changed proof decision blocks an acceptance (`BLOCKED_PROOF_CHANGED`). Same-outcome
  corrections can be rebound; otherwise the reviewer revokes, which ends the license and
  restarts the 90-day deletion clock.
- Historical claims keep `NEEDS_CHANGES` revisions (at most three).

## Architecture and safety boundaries

- `trajectory_claims`, `trajectory_fingerprints` and `trajectory_access_log` (V045). A deposit
  is a claim with `status = 'DEPOSITED'` and no submission; the bind sets the submission in the
  same transaction as the credit spend. V046 adds the durable target pricing policy and the
  reward-event trigger requiring accepted evidence for new-policy full bounties.
- Session-cookie owner upload with the existing origin guard; CLI tokens cannot upload or
  review. Prechecks are released before the bounded body stream. One upload per worker at a
  time, at most one per account, three successful uploads per hour and six failed attempts per
  15 minutes, 60-second body deadline. Per-worker limits need a shared edge limit across replicas.
- Reviewer queue/download uses `REVIEWER`; decisions add the role-write and origin guard.
  Downloads are attachment, `nosniff`, sandbox CSP and `no-store`, and commit an access record
  before releasing bytes. Queue/status responses never contain package text.
- Proof digest and fingerprints are unique; one approved claim per reward target. Lock order is
  payout rows, then submission, then claim, matching reconciliation and review corrections.
- PostgreSQL statements hide bound parameters; invalid exports return at most 20 schema-owned
  field paths. Do not enable HTTP-body, SQL-parameter or proxy-body logging.
- Payout roles never read claim tables. They call only the `bounty_trajectory_eligible(uuid,text)`
  security-definer projection; see ROLLOUT for owner and EXECUTE grants.

## Retention job

Schedule `python scripts/purge_trajectories.py` daily with an explicit `DATABASE_URL` and alert
on failures. It deletes unsubmitted deposits after 24 hours, removes package bytes of
non-accepted claims after 90 days (pending ones become `EXPIRED`), and deletes minimal records
of non-accepted claims after seven years unless a required-evidence record backs a reward
event. Accepted logs are never purged: they are the licensed sale inventory. Backups need the
same schedule, with a restore-time purge before serving traffic.

## Go-live (when the staging run passes)

The owner chose no advance date: the terms' effective date and the bonus cutoff are both the
go-live moment.

1. Deploy per ROLLOUT with intake off; set `TRAJECTORY_LAUNCH_APPROVED_TERMS_SHA256` to the
   `TERMS_SHA256` in `submission_api/trajectory_terms.py`. Run the staging check end to end.
2. Publish final v8 submission terms with the same effective date (see ROLLOUT).
3. Run `DATABASE_URL=... python scripts/open_chat_log_intake.py --opened-by "<name>" --confirm`.
   A second run changes nothing and prints the original moment. If something goes wrong, run
   it with `--correct-to <time> --corrected-by "<name>" --reason "<why>" --confirm`; each
   correction is recorded permanently.
4. Immediately set `TRAJECTORY_BONUS_ENABLED=true` and frontend `NEXT_PUBLIC_TRAJECTORY_BONUS=1`.
   Intake stays closed (503) until both the flag and the opening record exist.

## Fulfillment and remaining launch preparation

1. **Historical bonus payments are implemented.** V048 records a reserved bonus, its exact USD/Alpha
   conversion and destination evidence, and confirmation against the finalized treasury ledger.
   It never signs or sends money. See [FULFILLMENT.md](FULFILLMENT.md) for preparation,
   cancellation, duplicate prevention and confirmation.
2. **Private buyer delivery is implemented.** V048 binds a buyer agreement to the accepted package,
   records the buyer's assent and every export, and blocks changed/revoked approvals. Buyers use
   `/account/chat-log-deliveries`. The administrator queue tracks takedowns; agreement negotiation,
   contacting buyers and checking deletion remain explicit operator actions.
3. **Inventory.** Original historical task bundles (a missing bundle blocks tier selection) and
   account-less historical submissions (need verified ownership migration).
4. **P34/P35 release.** Generated task bundles, registry/catalog entries, source/type commitments
   and a pinned verifier release; see `staged/packing-20261012/`. New-policy targets exist only
   once something new is published.

Operator note: provider terms differ on whether outputs may be resold or used to train other
models. The contributor promises they may share the logs; buyers should still get the provider
and model recorded for each run.

## Validation

`python -m pytest tests/test_trajectory_schema.py tests/test_api_trajectories.py
tests/test_new_bounty_policy.py tests/test_bounty_trajectory_payouts.py` covers structure, the
logs-before-proof requirement, deposit expiry, after-the-fact refusal, review states, forfeiture
releasing the target, appeals against a competing proof, retention of accepted logs and payout
authorization. DB tests require the repo's private-fixture identity guard.
`scripts/check_schema_drift.py` compares SQL and ORM. Frontend tests pin the shown terms and
assent digests and the deposit linkage checks.
