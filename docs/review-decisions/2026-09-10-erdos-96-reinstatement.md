# Erdős 96 reinstatement — September 10, 2026

The maintainer reversed the September 8 quarantine and explicitly retained the original
August 5 bounty opening date. The disputed Khopkar claim was not established as an accepted
solution by the prior review. Erdős Problems continues to list the problem as open:
https://www.erdosproblems.com/96. This decision does not adjudicate the disputed paper.

Restore `Erdos96.erdos_96` and its logical negation from the previously published task source
at Formal Conjectures `8432eac998110a563e03df65a28c117e97c8c142`. The trusted task payloads
are unchanged; manifests adopt the current 10 MiB proof limit and corresponding new task IDs.
The 518 existing task bundles and their commitments remain unchanged. The pool becomes
260 targets / 520 bundles: 236 Erdős and 24 Green targets across 224 source files.

Both modes retain `fc-target:Erdos96.erdos_96`. Read-only production queries confirmed its
`bounty_tasks.opened_at` is `2026-08-05T16:07:00Z`, with no submissions or reward claims.
No database age reset, new reward identity, fixed price, or bounty-policy change is required.

At the September 10 12:43 UTC snapshot, the treasury held 73,638.951237686 Alpha and
61,211.625724288 Alpha was reserved for outstanding submissions. The remaining balance was
12,427.325513398 Alpha. The restored target's age weight of 36 places it at the 33% cap:
4,101.017419421 Alpha, approximately $2,715.18. The actual Alpha amount locks at submission;
its dollar display depends on market price. The public pool's total balance includes reserved
funds and must not be used directly as the available balance for a hypothetical quote.

The September 8 decision remains in the historical review and git history. The active retirement
name/type denylist and retired display index remove only Erdős 96. The retired-display generator
recognizes explicit current admissions when traversing historical deletions, while still failing
on unexplained deletions or a target simultaneously recorded as active and retired.

Validation: both restored challenges compiled and passed independent TaskInspector checks in the
unchanged production verifier image `sha256:5f215f809aedc278cca94b45da6e02fdabd8f810aecf5c2895e4049f15ab0ca3`.
All 520 bundles passed registry admission, and all 518 preexisting allowlist entries matched
byte for byte. The 17 pool/retirement tests and three retired-display generator tests passed;
the retired display regeneration check passed with 21 remaining display records.
