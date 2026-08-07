# Miner guide

How to get one Lean proof submitted, verified, and paid.

A submission is only created once the transfer is confirmed on finalized chain state, so a failed
request costs nothing but a refused one costs the transfer. **Check locally before you pay.**

---

## Start here: the CLI

[`conjectures-miner`](https://github.com/conjectures-io/conjectures-miner) does everything on this
page, and is the supported way to mine.

```bash
git clone https://github.com/conjectures-io/conjectures-miner && cd conjectures-miner
./install.sh
conjectures config set wallet_name my-wallet     # names only -- no key material, ever
conjectures config set wallet_hotkey my-hotkey
```

Then the whole flow:

```bash
conjectures tasks sync                                  # cache the task list
conjectures tasks challenge erdos1094                   # what you have to prove
                                                        # ... write Main.lean ...
conjectures build --proof Main.lean --task erdos1094    # seals submission.zip
conjectures check                                       # free; last step before money moves
conjectures pay                                         # 0.5 TAO, coldkey -> treasury
conjectures submit
conjectures submissions show <id> --watch
```

`check` asks whether the envelope is acceptable. Whether the **proof** is correct is a different
question, and only the verifier answers it — `verify` builds this repository's verifier on your
machine and runs it:

```bash
conjectures verify --setup                              # once: ~20 GB, 30-60 minutes
conjectures verify --proof Main.lean --task erdos1094   # exit 0 correct, 1 rejected
```

Linux only (WSL2 on Windows). It runs the development sandbox rather than the isolation a
validator applies to a proof it did not write, so it answers whether the proof is correct — not
whether the submission will be accepted.

**The rest of this page is the same flow by hand**, for anyone not installing the CLI. It needs a
checkout of this repository and a Bittensor wallet.

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
`../conjectures-tasks/pool/<tier>/<task_id>/`. `Challenge.lean` is the statement you must prove;
`SolutionHeader.lean.txt` and `SolutionFooter.lean.txt` are what your file gets wrapped in.

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

You can go further and run the real verifier, which is the same check the validator runs. This
needs a built checkout — `scripts/bootstrap.sh --miner`, about an hour — which is what
`conjectures verify --setup` does for you:

```bash
python3 -m verifier verify \
  --task ../conjectures-tasks/pool/<tier>/<task_id> \
  --submission Main.lean \
  --expected-task-sha256 <task_bundle_sha256> \
  --allow-insecure-development
```

Without `--allow-insecure-development` this refuses to start on any host below Landlock ABI 4,
which is most of them. The flag changes the isolation, not the verdict, and the report names
which one ran.

## 4. Pay

Transfer exactly **0.5 TAO** (`500000000` rao) from your coldkey to the `payment_recipient`
from step 1. Wait for the block to finalize, then keep the **extrinsic reference** — you cannot
submit without it, and it can fund only one submission ever.

Payment buys one verification attempt. It does not change Lean's verdict and does not guarantee
a reward. The bounty shown before or after submission is a live estimate (`locked: false`); if an
earlier proof establishes the same reward target while yours is queued, that bounty is solved.

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

On success you get `201` and a `submission_id`. Save it.

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
