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
| 2026-08-06 | Green 29 | `82ab85ee` | [`REVIEW_APPROVED`](2026-08-06-green-29.md) | Draft |

Submissions rejected at Lean verification never reach manual review and have no decision file.

## Open items across the current drafts

- No draft has the independent agent assessments or the human reviewer signature the policy
  requires. None may be published as binding until those are recorded.
- Three targets are already retired in the task repository (`Green42.green_42`,
  `Erdos939.erdos_939`, `Erdos10.erdos_10.variants.grechuk`), taking the pool to 137 targets and
  274 bundles. `Green29.green_29` should follow once its bounty is paid.
- The `CohnElkiesOptimal` and `Nat.Full` sibling sweeps proposed in earlier drafts are **not
  needed**: those declarations exist in the catalog but were never admitted to the task pool.
- Outstanding audit: 76 of the 137 live targets are stated through a non-Mathlib definition, across
  63 source files. All three confirmed defects had that shape, so it is the right place to look —
  but Green 29 was on the list and came back clean, so the shape flags risk rather than predicting a
  defect. No target should be retired on that basis alone.
- Open policy gap: `v1` has no code for "published as open, but the informal result is already a
  theorem." Erdős 10 is approved because of it, correctly. Green 29 may raise it a second time.
- `nanoda_enabled` is `false` on every verification run in this period, so each approval rests on a
  single kernel implementation.
