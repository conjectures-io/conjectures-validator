# Miner guide

How to get one Lean proof submitted, verified, and paid. Everything here is doable with the
scripts in this repository and a Bittensor wallet.

The short version: **pick a task, write `Main.lean`, check it locally, pay 0.5 TAO, submit the
bundle, poll the status.** A submission is only created once the transfer is confirmed on
finalized chain state, so a failed request costs nothing but a refused one costs the transfer.
Check locally before you pay.

---

## 0. Before you start

You need a Bittensor wallet whose **coldkey pays** and whose **hotkey signs** the request. The
validator requires that the paying coldkey owns the submitting hotkey.

Install the client dependencies:

```bash
pip install -e '.[service,subnet]'
```

Set the endpoint once:

```bash
export CONJECTURES_API=https://<validator-host>
```

---

## 1. Pick a task

```bash
curl -s "$CONJECTURES_API/v1/tasks" | python3 -m json.tool
```

```json
{
  "repository_commit": "e923379e609b9d5987011a1d1f06ec22ea25cd20",
  "bundle_format": "conjectures-submission/v1",
  "max_bundle_bytes": 2097152,
  "submission_price_rao": 500000000,
  "payment_recipient": "5C4h…",
  "tasks": [
    {
      "task_id": "fc-e923379e-erdos1094-erdos-1094-e88b987211-formalized-v1",
      "problem_id": "fc-problem-v1:…",
      "mode": "formalized",
      "tier": "tier-1",
      "source_theorems": ["Erdos1094.erdos_1094"],
      "task_bundle_sha256": "sha256:c70c6d…",
      "target_type_sha256s": ["sha256:304f28…"]
    }
  ]
}
```

Keep the `task_id` and its `task_bundle_sha256`: both go into your submission, and the
validator refuses anything that does not match the published commitment.

The task itself is in this repository, at `tasks/pool/<tier>/<task_id>/`. `Challenge.lean` is the
statement you must prove; `SolutionHeader.lean.txt` and `SolutionFooter.lean.txt` are what your
file gets wrapped in.

## 2. Write your proof

One file, `Main.lean`, UTF-8, at most 1,000,000 bytes. It is inserted between the trusted
header and footer, so write only the declarations you need — no `import` lines.

```lean
theorem target : type_of% Erdos1094.erdos_1094 := by
  ...
```

These are all rejected: `sorry`, `admit`, `axiom`, `import`, `set_option`, `native_decide`,
`macro`/`syntax`/`notation`, `instance`, attributes, and any reference to the source theorem
itself. The full list is in [README.md](../README.md#submission-policy-and-verification-stages).

## 3. Check it locally — do this before paying

```bash
python3 scripts/build_submission_bundle.py \
  --proof Main.lean \
  --task-id  <task_id> \
  --task-sha256 <task_bundle_sha256> \
  --hotkey   <your hotkey ss58> \
  --output   submission.zip

python3 -m verifier bundle scan --bundle submission.zip
```

`"admitted": true` means the archive and the static Lean policy both pass. Anything else prints
a `reason_code` and costs you nothing to fix. The builder also prints the `proof_sha256` you
will need next.

You can go further and run the real verifier locally, which is the same check the validator
runs:

```bash
python3 -m verifier verify \
  --task tasks/pool/<tier>/<task_id> \
  --submission Main.lean \
  --expected-task-sha256 <task_bundle_sha256>
```

## 4. Pay

Transfer exactly **0.5 TAO** (`500000000` rao) from your coldkey to the `payment_recipient`
from step 1. Wait for the block to finalize, then keep the canonical **`block-index` extrinsic
reference** (for example `4210031-0002`) — you cannot
submit without it, and it can fund only one submission ever.

Payment buys one verification attempt. It does not change Lean's verdict and does not guarantee
a reward.

## 5. Submit

```bash
python3 scripts/submit_proof.py \
  --api "$CONJECTURES_API" \
  --bundle submission.zip \
  --task-id <task_id> \
  --task-sha256 <task_bundle_sha256> \
  --payment-ref <extrinsic reference> \
  --wallet <wallet name> --hotkey <hotkey name>
```

On success you get `201`, a `submission_id`, and a `bounty` quote. Save them. The quote is the
direct TAO amount frozen for this submission; later configuration changes cannot reprice it.

The script signs the canonical request digest with your hotkey. If you'd rather build the
request yourself, the headers and the exact digest construction are in
[API.md](API.md#authentication).

## 6. Watch it

```bash
python3 scripts/submit_proof.py --api "$CONJECTURES_API" \
  --status <submission_id> --wallet <wallet name> --hotkey <hotkey name>
```

Three statuses move independently — none of them implies another:

| Field | Values | Meaning |
| --- | --- | --- |
| `verification_status` | `UNVERIFIED` → `VERIFIED` / `REJECTED` | What Lean decided |
| `manual_review_status` | `UNREVIEWED` → `APPROVED` / `REJECTED` | Reward-policy review, if enabled |
| `reward_status` | `INELIGIBLE` → `ELIGIBLE` → `REWARDED` / `FAILED` | Payout |

The `reward` object also shows whether this submission won its shared proof/counterexample
`problem_id`, plus the payout status, amount, finalized block, and extrinsic reference. A valid
submission can remain unpayable when its opposite-mode sibling already won the problem.

Once `verification_status` leaves `UNVERIFIED` the immutable verifier report is available:

```bash
curl -s ... "$CONJECTURES_API/v1/submissions/<submission_id>/report"
```

A rejection tells you which gate failed — `LEAN_KERNEL_REJECTED`, `STATEMENT_MISMATCH`,
`UNPERMITTED_AXIOM`, `TIMEOUT`, and so on.

`REJECTED` is terminal. Manual review can never turn a Lean-invalid proof into a valid one.

---

## Rules that will bite you

- **One proof, once.** Proof bytes are globally unique. Resubmitting the same file, even under a
  new payment, is `409 DUPLICATE_PROOF`.
- **One payment, one submission.** Reusing an extrinsic reference is `409 DUPLICATE_PAYMENT`.
- **Retries are safe, but only with the same idempotency key.** Same key and same request
  returns your original submission with `200`. A different request under a used key is `409`.
- **Your hotkey must appear in the bundle manifest** and must match the one that signs.
- **The archive shape is exact**: two entries, `submission.json` then `Main.lean`, nothing else.
  Use the builder and this is automatic.

## When something is refused

| `reason_code` | What to do |
| --- | --- |
| `PAYMENT_NOT_FINALIZED` | Wait for finality, or check the recipient, amount, and that your coldkey owns the hotkey |
| `SIGNATURE_INVALID` | Use the reference client; the operation domain, request digest, and exact timestamp header are all signed |
| `TASK_NOT_ALLOWED` | Re-read `/v1/tasks`; the pool changes between releases |
| `DUPLICATE_PROOF` / `DUPLICATE_PAYMENT` | Already used; nothing to retry |
| `IDEMPOTENCY_CONFLICT` | Use a fresh UUID for a genuinely new submission |
| `BUNDLE_*` | Rebuild with the builder and re-run `bundle scan` |
| `SUBMISSION_POLICY_VIOLATION` | Your Lean file uses something prohibited; the detail names it |

Every refusal is recorded on the validator side with its reason code and your payment
reference, so a paid-and-refused request can be looked up in support.

## More detail

- [SUBMISSION_BUNDLE.md](SUBMISSION_BUNDLE.md) — the exact bundle format and every admission rule
- [API.md](API.md) — endpoints, headers, the signature scheme, status codes
- [../SECURITY.md](../SECURITY.md) — what acceptance does and does not mean
- [SUBNET.md](SUBNET.md) — the whole validator contract
