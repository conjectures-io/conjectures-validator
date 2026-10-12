# Chat logs and treasury-share bounties: staged implementation

New targets offer 2.5% of gross finalized Alpha treasury and require accepted authentic chat logs.
For qualifying proofs submitted before launch, accepted logs earn an optional additional
**$500 USD for Erdős/Green source problems, $250 USD otherwise**. Existing proof payouts retain their terms.
Upload creates a private review record. Historical-log acceptance creates only a manual-payment review record. Required-log acceptance
unlocks the existing full-bounty payout worker and signer notifications. Neither action signs a
transfer or activates a commercial license.

The delivered flow includes the full [commercial license draft](commercial-license-draft-v1.md)
and separately pinned [private review terms](review-terms-v1.md) and [assent wording](assent-statements-v1.json), with an explicit commercial election inside intake. No contributor must negotiate or sign another
agreement if the final package, rights, terms and scope match their approved in-flow election.
The owner approved these economics and P34/P35 admission on October 12, 2026.
Commercial-license wording and operational launch requirements remain as described below.

## Recorded policy and launch settings

| Decision | Implemented proposal |
| --- | --- |
| Bonus amount | Historical: $500 Erdős/Green, $250 other; required new-bounty evidence: no extra cash bonus |
| What it buys | Accepted package delivery plus only separately elected and cleared rights, no additional license fee |
| Commercial choice | Optional; review-only packages can qualify. Making a permitted license mandatory needs new versioned terms and assent |
| Enrollment | Explicit timezone-aware `TRAJECTORY_BONUS_START_AT`, required to enable. It is a proof-submission cutoff; no guessed retroactivity |
| Claim window | Historical claims: 365 days from the launch cutoff; new required evidence: no retrospective cutoff |
| Rights duration | Proposed five years from activation, limited further by actual source rights |
| Size | UTF-8 JSON up to 32 MiB, 10,000 events, 100 runs, 1,000 artifacts, 1,048,576 Unicode characters per event/artifact (up to 4 MiB UTF-8), still within the overall byte limit |
| Proof bytes | Authoritative submission digest reference allowed; existing proofs up to 30 MiB need not be embedded twice |
| Revisions | At most three package versions, requested changes or withdrawal before replacement |
| Retention | Private content 90 days from first upload, no extension on revision; minimal audit/election/fingerprints seven years |

No live settings, contract, deployment, payout or data sale has been made by this work.

## Contributor instructions

1. Sign in to the account owning the proof. It must be verified and have a current human
   `REVIEW_APPROVED` full-bounty decision. `FORMALIZATION_DEFECT_AWARD`, automatic approvals,
   rejected/prior-established proofs and a different account's proof fail closed. Account-less
   legacy submissions require a separately verified ownership migration before this intake works.
2. Export relevant conversations through supported provider controls or legitimate API/client
   logs. Preserve actual chronological user-visible user/assistant messages, tool calls/results,
   errors, failed attempts, revisions and output artifacts. Never reconstruct or request hidden
   chain-of-thought, inaccessible internal reasoning or unavailable system instructions. Legitimately
   exportable reasoning summaries already shown to the user may be included as visible messages;
   do not attempt to retrieve internal reasoning behind them.
3. Fill [template.json](template.json) using [trajectory.schema.json](trajectory.schema.json).
   The template deliberately fails validation until its empty arrays and placeholders are replaced.
   Its false attestations are illustrative; the intake form separately records your explicit assent. [synthetic-example.json](synthetic-example.json) is a complete teaching example,
   explicitly synthetic with false authenticity attestations and rejected by intake.
   [synthetic-rights-example.json](synthetic-rights-example.json) demonstrates every source-rights field with unknown rights, which cannot be cleared. Do not relabel it as authentic evidence.
4. Identify each provider/model/version/interface, generation settings, export method and original
   export digest, with honest null/reason pairs for unknown metadata. Record the actual account
   agreement and terms reference; output ownership is not universal permission to resell/train.
5. Link the submission UUID, task ID/bundle hash and final proof SHA-256. Include code/config/log
   artifacts or explain omissions. Hash embedded UTF-8 bytes exactly. `authoritative_submission`
   references are permitted only for the linked final proof. Artifact paths are labels, never
   filesystem destinations. Nothing is downloaded, extracted or executed by intake.
6. Pin Lean, Mathlib, task and code revisions; supply environment, commands, expected result and
   reproducibility limitations. Record missing/redacted fields and their reproduction impact.
   For empty tool output use `content: ""` with `empty_content: true`. For branches/regenerations
   include the earlier `parent_event_id`; preserve chronological export order and explain missing
   branches. Images/binary attachments must be listed as omitted hashed artifacts with an honest
   reason and impact; never invent text or fetch external attachments automatically.
   Do not silently truncate long runs; ask for an approved alternative if 32 MiB is insufficient.
7. Remove credentials, keys, unrelated account history and unnecessary PII locally. Establish
   third-party authorization or remove the material. Mark every redaction. Store originals
   securely for a requested provenance check; never upload an entire provider account archive.
8. Accept narrow private review/storage permission. Separately choose review-only or conditional
   commercial purposes in the intake form. There is no default commercial election in the UI.
   For a conditional grant, `commercial_election.sources` must name `run:<id>` and `artifact:<id>`
   for every source, with its agreement/license, evidence, rights status, permitted purposes,
   restrictions and third-party rights. Unknowns are honest disclosures and block commercial
   clearance, not automatically package-review eligibility.

The machine schema validates shapes. Runtime validation also checks chronology, tool references,
artifact hashes, proof linkage, duplicate JSON keys and bounded nesting. The UTF-8 file is sent
unchanged inside a JSON-string envelope; separate checkbox/election fields are validated, then
merged into the canonical reviewed record. The original-file digest and server-side consent time,
account, exact terms/assent digests are retained. Assent must match displayed static bytes and
server pins. No IP address or user agent is added to the consent record.
Omission disclosures still require human matching against missing artifacts and tool results; a
nonempty disclosure list alone is not proof of completeness. It cannot establish
authenticity or intellectual-property rights. A modified fabricated transcript can pass a hash
check; provenance and semantic duplicate review remain mandatory human work.

## Human acceptance checklist

Every approval requires an independent reviewer, never the contributor, to attest that:

- The authentic export and source provenance were inspected and chronology matches actual history.
- All relevant visible messages, tools/results, failures and revisions are accounted for.
- The final proof matches the accepted proof, pinned dependencies and reproduction results.
- Unknowns, omissions and redactions are honest and no material gap defeats completeness.
- Proof ownership, review permission, third-party authorization and privacy checks pass.
- The full bounty is valid under the proof’s recorded policy. Under policy v4, prior-established
  proofs are rejected with `NOT_NOVEL`; no other hidden novelty marker exists. Defect-only awards
  never qualify by default. Older submissions retain their recorded policies.
- Exact and semantic duplicates, copied histories, manipulated metadata and replay were checked.
- Narrow private review/storage permission is sufficient.

The separate rights review must inspect each source's actual agreement, region/account terms,
contributor/employer/collaborator rights and every selected use. It records `NOT_CLEARED` or
`CLEARED_CONDITIONAL`, with a reason and reviewer identity. Unknown/prohibited/missing rights or
scopes beyond source permissions fail clearance. All restrictions must accompany a buyer license.
Commercial clearance alone does not activate rights. Declining or failing rights clearance does
not reject an otherwise accepted package under the proposed optional-license policy.

## Architecture and safety boundaries

- New `trajectory_claims`, `trajectory_fingerprints` and `trajectory_access_log` tables in migration V044. V045 adds the durable target pricing policy and a reward-event trigger that requires reviewed
  evidence for new-policy full bounties. Evidence review itself never transfers funds.
- Session-cookie owner upload with the existing origin guard; CLI tokens cannot upload or review.
  Account ownership is checked before body consumption, then eligibility is rechecked after the
  bounded stream under the same proof-row lock used by proof corrections. The precheck transaction
  is rolled back before the stream (no idle connection/row lock). A worker admits one upload at a
  time, at most one per account, with three successful uploads per hour and six failed attempts per 15 minutes and a 60-second body
  deadline. Parsing, validation and hashing run in a bounded worker thread. The raw-file limit is
  32 MiB; the transport envelope allows 64 MiB + 64 KiB for JSON escaping, with the same file and
  canonical-size checks. Per-worker limits need a shared edge limit across replicas.
- Reviewer queue/download uses `REVIEWER`; decisions add the existing role-write and origin guard.
  Queue/status responses contain metadata, never package text. Raw downloads are JSON attachment,
  `nosniff`, sandbox CSP and `no-store`; the UI never renders a transcript or follows its URLs.
  Download commits a claim/actor/time access record before releasing bytes. Updates to access
  records are blocked by a DB trigger; records expire with the claim audit row. No package text
  is included. Metadata reads defer bytea/provenance columns and project only necessary fields.
- Duplicate-proof and submission keys are unique. Approved claims have a unique stable reward
  target; fingerprints remain through all revisions. Canonical exact retry is idempotent.
  Identical evidence repackaged with different submission metadata collides. Semantic clones and
  deliberately altered fabricated content need human review.
- A changed/revoked underlying proof approval produces `BLOCKED_PROOF_CHANGED` on reads. A future
  payment adapter must recheck current proof and bonus decisions under locks before any payment.
  Claims snapshot proof review and bounty policy versions. Every mutation requires the current state version. An independent reviewer may rebind an unchanged-policy, same-outcome proof approval while retaining the original evidence review. An independent reviewer can revoke a
  blocked approval (`REVOKED_PROOF_CHANGED`), releasing its approved-target reservation, then
  explicitly reopen for fresh review while content exists. Rejected claims can likewise be
  reopened by a reviewer. These actions require a revision and owner-visible explanation.
- PostgreSQL statements hide bound parameters; invalid exports return at most 20 schema-owned field paths and standard
  error types, never Pydantic input, arbitrary unknown-key names or raw bytes. Do not enable HTTP-body, SQL-parameter or proxy-body logging.
- Withdrawal while pending removes current bytes immediately. Changes requested permit another
  version, bounded to three. The contributor can independently withdraw commercial permission
  in any current claim status before activation; the durable marker and audit entry survive
  exact upload retries. This does not change bonus eligibility under the optional-license draft.
  Expired pending claims display `EXPIRED`; purge persists it, and reviewers can reject expired
  claims. Approval/reopening requires available unexpired content. Revisions never extend expiry.
- Packages are private API data, not attached to public results, indexed for search, or forwarded
  to external model reviewers. Browser local persistence is not used. Existing session security
  and deployment-wide IP rate limits apply; each qualifying proof bounds accepted uploads to three.
  Reviewer decisions, revocation/reopening, owner status and withdrawal remain available if intake
  is paused; each claim retains its enrollment cutoff and state version independently of later configuration changes.

## Operational launch blockers and manual settlement design

The economic choices above are approved. Before production enablement, complete the following:

1. Merge the dependency policy work and coordinate V044 and V045; this branch starts on validator
   `ddc02f1473a269507572c4bcd0035fb11ad98905`, the deployed but unmerged PR119 policy baseline.
   The task-policy reference is PR18 `5fe7720f8aeb4423c0329a51601be720575c991f`. The separate packing admission record preserves the withdrawn disk targets.
2. Finalize legal counterparty/jurisdiction, versioned license text, privacy disclosures, applicable
   provider agreements and optional license wording. Changing draft wording requires a new version,
   regenerated SHA-256, matched frontend bytes and fresh assent. Stale frontend/server terms fail
   closed; neither side may replace a contributor's unseen terms automatically. `APP_MODE=PROD`
   refuses enabled intake while either terms version contains `draft`. Final approved terms also
   require an explicit `TRAJECTORY_LAUNCH_APPROVED_TERMS_SHA256` matching the pinned commercial
   digest. This guard cannot be satisfied by a flag change alone in this delivery.
3. Configure encrypted storage/backups, least-privilege DB/API access, reviewer roles, audited
   download access, TLS, request timeouts and proxy limits aligned with the file/envelope bounds.
   Migration V044 explicitly revokes PUBLIC access. Inspect `pg_default_acl` and relation grants
   for actual analytics/replica/read roles and remove unintended grants; no role names are guessed.
   Load-test the one-upload-per-worker memory budget before selecting replica/edge limits. The
   current repository uses one DB role; it does not provide cryptographic separation from other
   processes holding that credential. Dedicated role/storage isolation is a launch requirement.
4. Schedule `python scripts/purge_trajectories.py` daily with an explicit `DATABASE_URL` and alert
   on missed/failing runs. Test its deletion and restore procedure on a private fixture. Access
   already expires at 90 days even if purge fails. Purge removes bytes and full rights-provenance
   text, leaving only version/digests/choice/purposes and each source ID/status/hash. This applies
   even to cleared elections; the review database is not commercial delivery storage. Backups require an approved finite deletion
   schedule and a restore-time purge before service traffic; deleting the live row alone does
   not erase backup copies. The job also removes audit/fingerprints after seven years; the
   one-year claim window closes before that deletion and prevents a new claim for the old proof.
5. **Settlement/activation adapter is not implemented.** This is a launch blocker for paying
   bonuses or selling/using datasets. An authorized finance operator must record a unique claim
   payment with currency, actual amount, locked USD conversion source/time/rounding if applicable,
   transaction/reference, destination verification, paid-at time and independent confirmation.
   A second authorized operator must activate the exact elected license only after rechecking
   current full-bounty decision, bonus approval, novelty, no duplicate award, nonwithdrawal,
   commercial source clearance, exact consent/assent/terms digests and package digest. Activation
   must be before review-content expiry, with `commercial_withdrawn_at IS NULL`, a still-available
   exact package and full source evidence. After expiry this draft election cannot be activated. The future durable settlement
   ledger must uniquely bind the claim/proof/target and record activation scope, restrictions,
   actors and expiry; this cannot be a note or a fabricated `REWARDED` status. No such ledger or
   route exists here, so historical bonus responses explicitly say `NOT_ISSUED` (required evidence says `NOT_APPLICABLE`) and
   `INACTIVE_NO_PAYMENT_OR_ACTIVATION`. Do not infer payment from the existing proof reward.
6. Implement buyer agreements, restriction propagation, permitted delivery/storage, access and
   deletion/rights-dispute handling before any sale. Before the 90-day review purge, an authorized
   activation must transfer only the cleared package and accepted restrictions to approved delivery
   storage with disclosed finite retention. The review system never silently extends retention.
   For the proposed five-year license activated within 90 days, seven-year audit retention from
   first upload covers its duration; longer retention/use needs revised disclosures and terms. No substantive extra contributor agreement
   is required beyond this in-flow election if approved terms/scope stay identical; operational
   settlement and activation evidence is still required.
7. Only then select the explicit offer cutoff and enable both `TRAJECTORY_BONUS_ENABLED=true`
   and frontend `NEXT_PUBLIC_TRAJECTORY_BONUS=1`. Defaults remain off. Reviewer access and owner
   withdrawal remain available for existing records even if intake is later disabled.

## Provider sources (checked 2026-10-12; recheck before launch)

Official sources distinguish output ownership, export access, account agreements and downstream
use restrictions. None establishes a blanket resale/training right for a submitted trajectory.

- [OpenAI Terms of Use](https://openai.com/policies/terms-of-use/) and
  [Services Agreement](https://openai.com/policies/services-agreement/): conditional output rights
  do not remove competing-model restrictions, third-party rights or applicable exceptions.
- [ChatGPT export controls](https://help.openai.com/en/articles/7260999-exporting-your-chatgpt-history-and-data):
  use supported exports; organization-managed access differs. Remove unrelated account data.
- [Anthropic Consumer Terms](https://www.anthropic.com/legal/consumer-terms) and
  [Commercial Terms](https://www.anthropic.com/legal/commercial-terms): output provisions coexist
  with competing-product/model restrictions. Verify region and actual account agreement.
- [Claude export controls](https://support.claude.com/en/articles/9450526-export-your-claude-data):
  individual and organization export permissions differ; export access does not license resale.

Independent implementation review uses code and synthetic fixtures only. Private contributor
packages must not be sent to external reviewers without separate, appropriate authorization.

## Validation

`python -m pytest tests/test_trajectory_schema.py tests/test_api_trajectories.py` covers structure,
ownership, CSRF/role gates, eligibility, replay, revision, expiry, rights separation, no transfer,
and changed proof decisions. DB tests require the repo's explicit private-fixture identity guard.
`scripts/check_schema_drift.py` compares the new SQL and ORM tables. Frontend tests pin the shown
review/commercial/assent digests, preserve raw file text, require explicit elections and reject synthetic/oversized/mislinked packages.
`python scripts/trajectory_assets.py` regenerates schema/example/template; copy those and the
exact license/review Markdown, assent JSON and terms manifest to frontend `data/trajectory/` (served only by the enabled `/trajectory/[asset]` route) and verify their hashes together.

## Additional gates identified in the independent xhigh review

Before enabling required-evidence bounties, publish the deadline and an appeal/forfeiture rule
for logs that are never accepted, implement its audited target/reservation release, and test the
competing-proof case. No deadline or new disqualification ground is invented in this disabled
build. New-policy quotes are unavailable while intake is disabled.

Before opening historical claims, persist the owner-approved cutoff independently of environment
configuration and refuse changes for new enrollments. Production must reject future cutoffs.
The current per-claim snapshot already keeps accepted claims independent of later configuration.

The v8 submission contract remains an inactive draft; v7 is still bundled and published. Finalize
v8 with the actual activation date and a distinct immutable version/digest before enabling the
new program. The recovered `*-draft-v1` artifacts have never collected real assent; their edits
are permissible only in these disposable staging checkouts. Any prior private test environment
must be rebuilt. Never overwrite terms that collected contributor assent.

Payout roles must have no SELECT/UPDATE grants on private trajectory tables. They call only the
`bounty_trajectory_eligible(uuid,text)` security-definer boolean projection with a pinned search
path. PUBLIC execute is revoked; explicitly grant EXECUTE on that function to the real API,
notifier and command-rendering database roles during deployment, without granting claim-table
access. The function owner must be a trusted migration role, never a public/analytics role. The authorization trigger uses the same projection and locks only the public submission;
all claim writers and the purge job take that submission lock first. Retain minimal evidence
audit rows while a reward event references their submission; private content still expires.
