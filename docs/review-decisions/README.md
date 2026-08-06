# Review decisions

Published rationales for binding manual reward-review decisions, one file per submission, under the
[manual reward-review policy](../MANUAL_REVIEW_CRITERIA.md). Decisions are append-only: a correction
is a new file that supersedes an earlier one, and the original stays for audit.

| Date | Target | Submission | Outcome | Status |
| --- | --- | --- | --- | --- |
| 2026-08-05 | Erdős 15 | `f8fbf2ed` | [`FORMALIZATION_DEFECT_AWARD`](2026-08-05-erdos-15.md) | Paid; doc pending team sign-off |
| 2026-08-06 | Green 42 | `aa57d955` | [`FORMALIZATION_DEFECT_AWARD`](2026-08-06-green-42.md) | Draft |
| 2026-08-06 | Erdős 939 | `91915fc3` | [`FORMALIZATION_DEFECT_AWARD`](2026-08-06-erdos-939.md) | Draft |
| 2026-08-06 | Erdős 10 grechuk | `244ff2d0` | [`REVIEW_APPROVED`](2026-08-06-erdos-10-grechuk.md) | Draft |
| 2026-08-06 | Erdős 10 grechuk | `ce95887b` | [`DUPLICATE_OF_EARLIER_SUBMISSION`](2026-08-06-erdos-10-grechuk-duplicate.md) | Draft |

Submissions rejected at Lean verification never reach manual review and have no decision file.

## Open items across the current drafts

- No draft has the independent agent assessments or the human reviewer signature the policy
  requires. None may be published as binding until those are recorded.
- Four targets are proposed for quarantine: `Green42.green_42` plus its three
  `CohnElkiesOptimal` siblings, and both `Erdos939.erdos_939` modes.
- Two audit sweeps are proposed: the `CohnElkiesOptimal` family, and every target quantifying over
  `Nat.Full` without a positivity hypothesis (`Erdos939.*`, `Erdos940.*`).
- `nanoda_enabled` is `false` on every verification run in this period, so each approval rests on a
  single kernel implementation.
