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
  "repository_commit": "379fc0298dc146df549e7061c3ede0353a5bb51f",
  "bundle_format": "conjectures-submission/v1",
  "max_bundle_bytes": 2097152,
  "submission_price_rao": 500000000,
  "payment_recipient": "5C4h…",
  "tasks": [
    {
      "task_id": "fc-379fc029-erdos1094-erdos-1094-1ec3e802ca-formalized-v1",
      "task_bundle_sha256": "sha256:74937b…",
      "target_type_sha256s": ["sha256:304f28…"]
    }
  ]
}
```

Keep the `task_id` and its `task_bundle_sha256`: both go into your submission, and the
validator refuses anything that does not match the published commitment.

The task itself is in the separately checked-out `conjectures-tasks` repository, at
`../conjectures-tasks/pool/<tier>/<task-directory>/`. The directory is a readable name; use the
opaque `task_id` from its `manifest.json` in the protocol. `Challenge.lean` is the statement you
must prove; `SolutionHeader.lean.txt` and `SolutionFooter.lean.txt` are what your file gets wrapped
in.

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
will need next. This is deliberately only a fast guardrail check; it does not compile the proof.

Now run the real verifier locally. This unpacks the proof in memory and sends it through the same
task reconstruction, Comparator, statement, axiom, and Lean kernel checks as the validator:

```bash
python3 -m verifier bundle verify \
  --task ../conjectures-tasks/pool/<tier>/<task-directory> \
  --bundle submission.zip
```

Do not pay unless this reports `"accepted": true` and `"lean_kernel_passed": true`. On a local
machine without the production Linux sandbox, add `--allow-insecure-development`. That changes
only local process isolation: the full Lean/Comparator proof checks still run, and the validator
always verifies again in its hardened environment.

## 4. Pay

Transfer exactly **0.5 TAO** (`500000000` rao) from your coldkey to the `payment_recipient`
from step 1. Wait for the block to finalize, then keep the **extrinsic reference** — you cannot
submit without it, and it can fund only one submission ever.

Payment buys one verification attempt. It does not change Lean's verdict and does not guarantee
a reward. The catalog bounty is live before submission; acceptance fixes a fresh quote for your
attempt (`locked: true`). That amount is conditional on verification, review, and winning the
reward target—an earlier successful proof can still solve the target while yours is queued.
Submissions accepted before the V012 activation remain under their original payout-time policy.

## 5. Submit

```bash
python3 scripts/submit_proof.py \
  --api "$CONJECTURES_API" \
  --bundle submission.zip \
  --task ../conjectures-tasks/pool/<tier>/<task-directory> \
  --task-id <task_id> \
  --task-sha256 <task_bundle_sha256> \
  --payment-ref <extrinsic reference> \
  --credit-name "Your Name or Team" \
  --credit-url "https://example.org/your-profile" \
  --credit-orcid "0000-0002-1825-0097" \
  --wallet <wallet name> --hotkey <hotkey name>
```

On success you get `201` and a `submission_id`. Save it.

The three `--credit-*` flags are optional; URL and ORCID require a name. If supplied, the exact
credit is covered by your hotkey signature and published beside the hotkey after Lean verification.
It is a permanent snapshot for this submission, not your account display name. Omit all three to
receive public credit by hotkey only.

Before opening the network request, the script requires `--task` and repeats the full local
verification against the bundle bytes it will send. A rejection is printed locally and nothing is
submitted. `--skip-local-verification` is available for exceptional setups, but removes that
protection. If your machine needs the development sandbox shim, pass
`--allow-insecure-local-verification`.

The script then signs the canonical request digest with your hotkey. If you'd rather build the
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
| `SIGNATURE_INVALID` | Sign the request digest, not the bundle; check the hotkey matches |
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
