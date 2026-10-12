# Unsigned payout instruction cancellation

Use `scripts/manage_payout_instruction.py request.json` for a rollback-only preflight,
then refresh the chain evidence and use `--apply` to commit. This tool performs no wallet
signing or on-chain transaction. Cancelling a local instruction cannot revoke a command
already copied or an on-chain proposal; signers must honor its cancelled status.

The request names `command: "cancel-and-withdraw"`, `reward_event_id`, `submission_id`,
`expected_review_id`, `expected_action_id` (null for the first action), the authorized
reviewer account UUID as `reviewer`, `reason`, a published rejection `reason_code`, and
both internal `notes` and miner-visible `notes_public`.

`chain_evidence` must include an ISO UTC `checked_at` no more than 120 seconds old,
`network: "finney"`, `multisig_address`, `pending_multisig_count: 0`, the inspected
`finalized_block_hash`, and `no_matching_chain_transfer: true`. Preserve raw RPC/CLI
receipts alongside the request. These fields are an audited reviewer attestation, not
a substitute for querying the correct configured multisig and treasury chain state.
Stop if a pending proposal, matching transfer, stale evidence, or uncertain mapping exists.

The database also checks exact payout/submission mapping, current review and instruction
IDs, reviewer role, every transaction/failure field, matching observed treasury transfers,
and active notification leases. Cancellation and the superseding rejection are atomic.
No amount, destination, proof, old decision, or reward event is edited or deleted.

The new append-only `payout_instruction_actions` records CANCEL and RESTORE. The latest
action determines instruction state; the original reward event's PENDING field remains
its chain state, not permission to pay. Command generation, notification seeding/claiming,
and automatic chain matching exclude cancelled instructions. Later actual transfers are
still captured in the treasury ledger for explicit reconciliation.

To reverse a mistaken cancellation, first append a supported review correction restoring
approval after checking reward-target priority. Then use `command: "restore"`, naming
the current approval and cancellation action IDs, a new rationale and fresh chain evidence;
omit rejection-only fields. Restoration is explicit and appended to the audit ledger. It
does not restore approval itself or erase the cancelled/rejected history.

Never delete reward events: an eligible submission may otherwise generate another instruction.
Do not use FAILED to simulate cancellation, or disregard a treasury transfer to bypass a guard.
