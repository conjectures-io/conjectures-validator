# Competition API reference

The `/v1/competitions` surface of the conjectures platform API: every endpoint's inputs (name, location, type, whether required, constraints) and outputs (status and field-by-field response types). It serves one competition today, `lz77`, so `{slug}` is `lz77` in every path.

Generated on 2026-09-25 from the OpenAPI schema of `deploy/dev-main-20260924` (dev merged with main), the version running on DEV. Access rules, refusal codes and the three responses without a response model come from the route handlers (`submission_api/routers/competitions.py`, `competition_reads.py`, `competitions_admin.py`). Examples are real DEV responses from the same day, trimmed to one array item, with the slug and name rewritten for the competition's rename from `miniz-oxide` ("miniz_oxide DEFLATE") to `lz77` ("LZ77 parsing"). The slug is part of every path and of the signed submit message, so a client still using `miniz-oxide` gets 404 `NOT_FOUND`.

## Conventions

- **Base path:** `/v1/competitions`. All bodies are JSON unless noted; submits are `multipart/form-data`.
- **Availability:** every route answers **503 `COMPETITIONS_UNAVAILABLE`** when the deployment has competitions switched off, and **503 `COMPETITION_SCHEMA_UNAVAILABLE`** when the competition database is older than migration 0011 of conjectures-optimisation-lz77.
- **Scoring snapshots:** score fields come from the latest scoring pass the competition's weight setter published, or from the one named by `snapshot_id`. Before the first pass, `context.status` is `"not_ready"` and rankings are empty.
- **Paging:** paged lists take `limit` (1–100, default 25) and an opaque `cursor`; pass back `next_cursor` unchanged. A cursor is bound to the filters and snapshot it was issued for.
- **Rate limit:** the global per-IP `/v1` limit applies (default 120 requests per 60 s), reported in `RateLimit-Limit`, `RateLimit-Remaining` and `RateLimit-Reset` headers. The two submit routes also count, in Postgres across every replica, a per-address budget before any signature is checked and a per-hotkey budget once the hotkey is proven (see each route's refusals).
- **Ids** are strings in responses (`"submission": "42"`) and integers in paths.
- **Times** are ISO 8601 strings in UTC with an offset (`2026-09-24T14:07:50.192134+00:00`); the schema types them only as `string`.
- **Hotkeys** are ss58 addresses.
- **Errors** are `application/problem+json`:

| Field | Type | Meaning |
| --- | --- | --- |
| `type` | string | Always `about:blank` |
| `title` | string | Short summary |
| `status` | integer | The HTTP status |
| `detail` | string | Human-readable explanation |
| `reason_code` | string | Stable machine code, listed per endpoint below |

A request that fails FastAPI's own validation (wrong type, missing header) gets **422** with `HTTPValidationError` instead.

## Endpoints

| Method | Path | Access |
| --- | --- | --- |
| GET | `/v1/competitions` | Public, no authentication |
| GET | `/v1/competitions/{slug}` | Public, no authentication |
| POST | `/v1/competitions/{slug}/submissions` | Hotkey signature in headers (see [Signing a submission](#signing-a-submission)) |
| POST | `/v1/competitions/{slug}/submissions/session` | Browser session cookie only |
| GET | `/v1/competitions/{slug}/submissions` | Public, no authentication |
| GET | `/v1/competitions/{slug}/me/submissions` | Signed-in account: browser cookie or CLI bearer token |
| GET | `/v1/competitions/{slug}/submissions/{submission_id}` | Public, no authentication |
| GET | `/v1/competitions/{slug}/submissions/{submission_id}/admission` | Public, no authentication |
| GET | `/v1/competitions/{slug}/submissions/{submission_id}/report` | Public, no authentication |
| GET | `/v1/competitions/{slug}/submissions/{submission_id}/source` | Public, no authentication |
| GET | `/v1/competitions/{slug}/submissions/{submission_id}/source/{filename}` | Public, no authentication |
| GET | `/v1/competitions/{slug}/pareto` | Public, no authentication |
| GET | `/v1/competitions/{slug}/leaderboard` | Public, no authentication |
| GET | `/v1/competitions/{slug}/weights/current` | Public, no authentication |
| GET | `/v1/competitions/{slug}/admin/queue` | ADMIN role, browser session |
| GET | `/v1/competitions/{slug}/admin/submissions/{submission_id}` | ADMIN role, browser session |
| POST | `/v1/competitions/{slug}/admin/submissions/{submission_id}/requeue` | ADMIN role, browser session, write |

## Discover

### GET `/v1/competitions`

List the competitions this deployment serves. Exactly one today.

**Access:** Public, no authentication.

**Response**

200: [Index](#schema-index)

<details><summary>Example: <code>GET /v1/competitions</code> on DEV</summary>

```json
{
  "items": [
    {
      "slug": "lz77",
      "name": "LZ77 parsing",
      "submissions_open": true,
      "queue_depth": 0,
      "current_snapshot_id": "4797",
      "description": "Formally verified compression competition",
      "files": [
        {
          "name": "parse.rs",
          "max_bytes": 524288
        },
        "…"
      ],
      "metric_definitions": [
        {
          "key": "balanced_time_ratio",
          "label": "Mean file time / incumbent",
          "unit": "ratio",
          "better": "lower"
        },
        "…"
      ],
      "policy": {
        "method": "local-global-improvement-space-log",
        "version": "compression-policy-8e27894cabfbec95",
        "speed_floor": 10.0,
        "pareto_share": 0.6,
        "max_ratio_pct": 40.0,
        "scoring_method": "local-global-improvement-space-log",
        "bootstrap_draws": 2000,
        "confidence_level": 0.95,
        "required_corpora": [
          "corpus-stage1",
          "…"
        ],
        "competition_share": 0.2,
        "improvement_decay": 0.6,
        "improvement_share": 0.4,
        "improvement_window": 10,
        "improvement_threshold": 0.0025,
        "max_balanced_time_ratio": 10.0,
        "admission_policy_version": "fixed-corpus-speed-bounds-v2",
        "max_mean_file_compression_pct": 40.0
      },
      "policy_status": "ready",
      "execution_limits": {
        "benchmark_timeout_seconds": null,
        "gate_timeout_seconds": null
      },
      "context": {
        "snapshot_id": "4797",
        "computed_at": "2026-09-25T08:06:57.900177+00:00",
        "policy_version": "compression-policy-8e27894cabfbec95",
        "status": "ready",
        "freshness": "unknown",
        "freshness_reason": "Recorded scoring pass; live policy changes require a new pass."
      }
    }
  ]
}
```

</details>

### GET `/v1/competitions/{slug}`

One competition: whether submissions are open, queue depth, file limits, metric definitions and the scoring policy of the current snapshot.

**Access:** Public, no authentication.

**Parameters**

| Name | In | Type | Required | Constraints |
| --- | --- | --- | --- | --- |
| `slug` | path | string | yes |  |

**Response**

200: [Competition](#schema-competition)

<details><summary>Example: <code>GET /v1/competitions/lz77</code> on DEV</summary>

```json
{
  "slug": "lz77",
  "name": "LZ77 parsing",
  "submissions_open": true,
  "queue_depth": 0,
  "current_snapshot_id": "4797",
  "description": "Formally verified compression competition",
  "files": [
    {
      "name": "parse.rs",
      "max_bytes": 524288
    },
    "…"
  ],
  "metric_definitions": [
    {
      "key": "balanced_time_ratio",
      "label": "Mean file time / incumbent",
      "unit": "ratio",
      "better": "lower"
    },
    "…"
  ],
  "policy": {
    "method": "local-global-improvement-space-log",
    "version": "compression-policy-8e27894cabfbec95",
    "speed_floor": 10.0,
    "pareto_share": 0.6,
    "max_ratio_pct": 40.0,
    "scoring_method": "local-global-improvement-space-log",
    "bootstrap_draws": 2000,
    "confidence_level": 0.95,
    "required_corpora": [
      "corpus-stage1",
      "…"
    ],
    "competition_share": 0.2,
    "improvement_decay": 0.6,
    "improvement_share": 0.4,
    "improvement_window": 10,
    "improvement_threshold": 0.0025,
    "max_balanced_time_ratio": 10.0,
    "admission_policy_version": "fixed-corpus-speed-bounds-v2",
    "max_mean_file_compression_pct": 40.0
  },
  "policy_status": "ready",
  "execution_limits": {
    "benchmark_timeout_seconds": null,
    "gate_timeout_seconds": null
  },
  "context": {
    "snapshot_id": "4797",
    "computed_at": "2026-09-25T08:06:57.900177+00:00",
    "policy_version": "compression-policy-8e27894cabfbec95",
    "status": "ready",
    "freshness": "unknown",
    "freshness_reason": "Recorded scoring pass; live policy changes require a new pass."
  }
}
```

</details>

**Refusals**

- 404 `NOT_FOUND`: unknown slug

## Submit

### POST `/v1/competitions/{slug}/submissions`

Queue a `parse.rs` + `Parse.lean` pair for the gate, signed by a registered subnet hotkey. Idempotent per (hotkey, sha256 of both files): a repeat returns the same submission.

**Access:** Hotkey signature in headers (see [Signing a submission](#signing-a-submission)). No cookie or bearer token.

**Parameters**

| Name | In | Type | Required | Constraints |
| --- | --- | --- | --- | --- |
| `slug` | path | string | yes | max length 64 |
| `X-Conjectures-Hotkey` | header | string | yes |  |
| `X-Conjectures-Timestamp` | header | integer | yes |  |
| `X-Conjectures-Signature` | header | string | yes |  |

**Request body** (`multipart/form-data`)

| Field | Type | Required |
| --- | --- | --- |
| `parse.rs` | file | yes |
| `Parse.lean` | file | yes |

Exactly these parts and nothing else; each file 1 byte to 512 KiB.

**Response**

201 for a new submission; **200** with `created: false` when the same files were already queued: [Accepted](#schema-accepted)

**Refusals**

- 503 `SUBMISSIONS_PAUSED`: submissions are paused platform-wide
- 429 `RATE_LIMITED`: more than `COMPETITION_IP_RATE_PER_MINUTE` (default 30) submits from this client address in the current minute, counted whether or not the request is signed
- 401 `SIGNATURE_EXPIRED`: `X-Conjectures-Timestamp` is more than 300 s from server time
- 400 `MALFORMED_REQUEST`: missing or extra form parts, an empty file, or an unparseable form
- 413 `BUNDLE_TOO_LARGE`: a file over 512 KiB
- 401 `SIGNATURE_INVALID`: bad address, bad hex, or the signature does not match the message
- 429 `RATE_LIMITED`: more than `COMPETITION_RATE_PER_MINUTE` (default 10) signed submits for this hotkey in the current minute; only a valid signature spends a hotkey's budget
- 402 `NOT_REGISTERED`: no recorded subnet registration for this hotkey
- 402 `NO_ENTITLEMENT`: queued submissions already use every unspent registration
- 404 `NOT_FOUND`: unknown slug

### POST `/v1/competitions/{slug}/submissions/session`

Queue a submission as the signed-in account, without a hotkey signature. The account's linked submission coldkey must be the coldkey that registered the named hotkey. The row records the account id.

**Access:** Browser session cookie only; a CLI bearer token is refused 403 `BROWSER_SESSION_REQUIRED`. A cookie write also needs an allowlisted `Origin` or `Sec-Fetch-Site: same-origin` (403 `CROSS_SITE_WRITE_REFUSED`). **Cross-origin calls currently fail CORS preflight**: `X-Conjectures-Hotkey` is not in the allowed request headers.

**Parameters**

| Name | In | Type | Required | Constraints |
| --- | --- | --- | --- | --- |
| `slug` | path | string | yes | max length 64 |
| `X-Conjectures-Hotkey` | header | string | yes |  |

**Request body** (`multipart/form-data`)

| Field | Type | Required |
| --- | --- | --- |
| `parse.rs` | file | yes |
| `Parse.lean` | file | yes |

Exactly these parts and nothing else; each file 1 byte to 512 KiB.

**Response**

201 for a new submission; **200** with `created: false` for a repeat: [Accepted](#schema-accepted)

**Refusals**

- 503 `SUBMISSIONS_PAUSED`: submissions are paused platform-wide
- 429 `RATE_LIMITED`: more than `COMPETITION_IP_RATE_PER_MINUTE` (default 30) submits from this client address in the current minute
- 402 `NO_SUBMISSION_COLDKEY`: the account has no linked submission coldkey
- 403 `HOTKEY_NOT_YOURS`: the hotkey was registered by a different coldkey
- 429 `RATE_LIMITED`: more than `COMPETITION_RATE_PER_MINUTE` (default 10) submits for this hotkey in the current minute, counted only once the account is shown to own it
- 400 `MALFORMED_REQUEST`: missing or extra form parts, an empty file, or an unparseable form
- 413 `BUNDLE_TOO_LARGE`: a file over 512 KiB
- 402 `NOT_REGISTERED`: no recorded subnet registration for this hotkey
- 402 `NO_ENTITLEMENT`: queued submissions already use every unspent registration
- 404 `NOT_FOUND`: unknown slug

## Read submissions

### GET `/v1/competitions/{slug}/submissions`

Public feed of miner and baseline submissions, newest first, with gate status, metrics, admission and score.

**Access:** Public, no authentication.

**Parameters**

| Name | In | Type | Required | Constraints |
| --- | --- | --- | --- | --- |
| `slug` | path | string | yes |  |
| `limit` | query | integer | no | ≥ 1, ≤ 100, default `25` |
| `cursor` | query | string \| null | no | max length 256 |
| `hotkey` | query | string \| null | no |  |
| `kind` | query | `"miner"` \| `"baseline"` \| null | no |  |
| `gate_status` | query | `"queued"` \| `"running"` \| `"passed"` \| `"failed"` \| `"error"` \| `"unknown"` \| null | no |  |
| `admission_outcome` | query | `"passed"` \| `"not_required"` \| `"inconclusive"` \| `"dominated"` \| `"excluded"` \| null | no |  |
| `on_frontier` | query | boolean \| null | no |  |
| `snapshot_id` | query | integer \| null | no | ≥ 1 |

**Response**

200: [SubmissionPage](#schema-submissionpage)

<details><summary>Example: <code>GET /v1/competitions/lz77/submissions?limit=1</code> on DEV</summary>

```json
{
  "context": {
    "snapshot_id": "4797",
    "computed_at": "2026-09-25T08:06:57.900177+00:00",
    "policy_version": "compression-policy-8e27894cabfbec95",
    "status": "ready",
    "freshness": "unknown",
    "freshness_reason": "Recorded scoring pass; live policy changes require a new pass."
  },
  "items": [
    {
      "id": "2",
      "kind": "miner",
      "hotkey": "5HjJX7k4nhyqTjCJkvC6Jf1GxBQ7AwjgjHUa7wNqDWPsExHb",
      "baseline_name": null,
      "submitted_at": "2026-09-24T14:07:50.192134+00:00",
      "gate_status": "passed",
      "aggregation_id": "4",
      "metrics": {
        "balanced_time_ratio": 12.271011338965709,
        "mean_file_compression_pct": 34.6629079634651,
        "total_compression_seconds": 9.591954104000001,
        "lz77_seconds": 9.216629456,
        "byte_weighted_compression_pct": 31.562401129943503
      },
      "admission": {
        "decision_id": "4",
        "status": "recorded",
        "outcome": "excluded",
        "admitted": false,
        "reason_code": "outside-scoring-bounds",
        "explanation": "The submission is outside the scoring limits.",
        "reference_submission_id": null,
        "freshness": "unknown"
      },
      "score": {
        "snapshot_id": "4797",
        "on_frontier": false,
        "pareto_weight": 0.0,
        "improvement_weight": 0.0,
        "combined_weight": 0.0,
        "payable_weight": 0.0,
        "payment_eligible": false,
        "unpaid_reason": "scoring-bounds:time-ratio-limit"
      }
    }
  ],
  "next_cursor": "Y29tcHJlc3Npb24tZmVlZC40Nzk3LjE3OTAyNTg4NzAxOTIxMzRfMi43MGQ3MDkwYWJjMTU2MjU2NDBjNQ.77eaSv3ONE6lI8-kjN5xOA"
}
```

</details>

**Refusals**

- 400 `INVALID_CURSOR`: the cursor is forged, expired or was issued for other filters
- 404 `SNAPSHOT_NOT_FOUND`: `snapshot_id` names no published snapshot
- 409 `SCORING_NOT_READY`: `admission_outcome` or `on_frontier` filter before any scoring snapshot exists
- 404 `NOT_FOUND`: unknown slug

### GET `/v1/competitions/{slug}/me/submissions`

The same feed, limited to the signed-in account's submissions. Only submissions made through `POST …/submissions/session` carry an account id, so hotkey-signed CLI submissions do not appear.

**Access:** Signed-in account: browser cookie or CLI bearer token. 401 when not signed in.

**Parameters**

| Name | In | Type | Required | Constraints |
| --- | --- | --- | --- | --- |
| `slug` | path | string | yes |  |
| `limit` | query | integer | no | ≥ 1, ≤ 100, default `25` |
| `cursor` | query | string \| null | no | max length 256 |
| `gate_status` | query | `"queued"` \| `"running"` \| `"passed"` \| `"failed"` \| `"error"` \| `"unknown"` \| null | no |  |
| `admission_outcome` | query | `"passed"` \| `"not_required"` \| `"inconclusive"` \| `"dominated"` \| `"excluded"` \| null | no |  |
| `on_frontier` | query | boolean \| null | no |  |
| `snapshot_id` | query | integer \| null | no | ≥ 1 |

**Response**

200: [SubmissionPage](#schema-submissionpage)

**Refusals**

- 400 `INVALID_CURSOR`: the cursor is forged, expired or was issued for other filters
- 404 `SNAPSHOT_NOT_FOUND`: `snapshot_id` names no published snapshot
- 404 `NOT_FOUND`: unknown slug

### GET `/v1/competitions/{slug}/submissions/{submission_id}`

One submission with its pipeline stages (static, Lean, benchmark, aggregation), current evidence ids and links to its report, source and admission.

**Access:** Public, no authentication.

**Parameters**

| Name | In | Type | Required | Constraints |
| --- | --- | --- | --- | --- |
| `slug` | path | string | yes |  |
| `submission_id` | path | integer | yes | ≥ 1 |
| `snapshot_id` | query | integer \| null | no | ≥ 1 |

**Response**

200: [Detail](#schema-detail)

<details><summary>Example: <code>GET /v1/competitions/lz77/submissions/1</code> on DEV</summary>

```json
{
  "submission": {
    "id": "1",
    "kind": "miner",
    "hotkey": "5CvyPx3q4kC4jao42Hif7YUkpSm7wLSXn4L65DreofiKHYRZ",
    "baseline_name": null,
    "submitted_at": "2026-09-23T15:34:09.825314+00:00",
    "gate_status": "passed",
    "aggregation_id": "3",
    "metrics": {
      "balanced_time_ratio": 7.901788434999682,
      "mean_file_compression_pct": 34.996934896898736,
      "total_compression_seconds": 6.075678299,
      "lz77_seconds": 5.6948619009999994,
      "byte_weighted_compression_pct": 31.98187696170747
    },
    "admission": {
      "decision_id": "3",
      "status": "recorded",
      "outcome": "not_required",
      "admitted": true,
      "reason_code": "no-slower-better-compressing-neighbor",
      "explanation": "No speed comparison is required for this contribution.",
      "reference_submission_id": null,
      "freshness": "unknown"
    },
    "score": {
      "snapshot_id": "4797",
      "on_frontier": true,
      "pareto_weight": 0.6,
      "improvement_weight": 0.4,
      "combined_weight": 1.0,
      "payable_weight": 1.0,
      "payment_eligible": true,
      "unpaid_reason": null
    }
  },
  "digest": "7915373e8c682c5f25405b7be1e2b9477c28a0ca8c081a59cea0a123a324692a",
  "source_sha256": "dffa021768ed0ac356f35e480d2d50e05f41b540391d370605664d8c728f3e3b",
  "pipeline_observed_at": "2026-09-25T08:07:07.960032+00:00",
  "pipeline": {
    "static": {
      "status": "passed",
      "started_at": null,
      "finished_at": "2026-09-24T15:25:18.218795+00:00",
      "reason_code": null,
      "message": null
    },
    "lean": {
      "status": "passed",
      "started_at": null,
      "finished_at": "2026-09-24T15:26:03.327178+00:00",
      "reason_code": null,
      "message": null
    },
    "benchmark": {
      "status": "passed",
      "started_at": null,
      "finished_at": null,
      "reason_code": null,
      "message": null
    },
    "aggregation": {
      "status": "passed",
      "started_at": null,
      "finished_at": null,
      "reason_code": null,
      "message": null
    }
  },
  "current_evidence": {
    "aggregation_id": "3",
    "admission_check_id": "3"
  },
  "bounds": {
    "eligible": true,
    "max_balanced_time_ratio": 10.0,
    "max_mean_file_compression_pct": 40.0,
    "observed_balanced_time_ratio": 7.901788434999682,
    "observed_mean_file_compression_pct": 34.996934896898736,
    "violations": []
  },
  "context": {
    "snapshot_id": "4797",
    "computed_at": "2026-09-25T08:06:57.900177+00:00",
    "policy_version": "compression-policy-8e27894cabfbec95",
    "status": "ready",
    "freshness": "unknown",
    "freshness_reason": "Recorded scoring pass; live policy changes require a new pass."
  },
  "links": {
    "report": "/v1/competitions/lz77/submissions/1/report",
    "source": "/v1/competitions/lz77/submissions/1/source",
    "admission": "/v1/competitions/lz77/submissions/1/admission"
  }
}
```

</details>

**Refusals**

- 404 `NOT_FOUND`: no such public submission
- 404 `NOT_IN_SNAPSHOT`: `snapshot_id` names a snapshot that does not contain it
- 404 `SNAPSHOT_NOT_FOUND`: `snapshot_id` names no published snapshot

### GET `/v1/competitions/{slug}/submissions/{submission_id}/admission`

The admission decision and, when one ran, the statistical speed test.

**Access:** Public, no authentication.

**Parameters**

| Name | In | Type | Required | Constraints |
| --- | --- | --- | --- | --- |
| `slug` | path | string | yes |  |
| `submission_id` | path | integer | yes | ≥ 1 |
| `snapshot_id` | query | integer \| null | no | ≥ 1 |

**Response**

200: [AdmissionResponse](#schema-admissionresponse)

<details><summary>Example: <code>GET /v1/competitions/lz77/submissions/1/admission</code> on DEV</summary>

```json
{
  "context": null,
  "admission": {
    "decision_id": "3",
    "status": "recorded",
    "outcome": "not_required",
    "admitted": true,
    "reason_code": "no-slower-better-compressing-neighbor",
    "explanation": "No speed comparison is required for this contribution.",
    "reference_submission_id": null,
    "freshness": "unknown"
  },
  "decision": {
    "id": "3",
    "submission_id": "1",
    "created_at": "2026-09-25T07:39:52.320030+00:00",
    "outcome": "not_required",
    "admitted": true,
    "reason_code": "no-slower-better-compressing-neighbor",
    "explanation": "No speed comparison is required for this contribution.",
    "policy_version": "fixed-corpus-speed-bounds-v2",
    "candidate_aggregation_id": "3",
    "reference_submission_id": null,
    "reference_aggregation_id": null,
    "bounds": {
      "eligible": true,
      "max_balanced_time_ratio": 10.0,
      "max_mean_file_compression_pct": 40.0,
      "observed_balanced_time_ratio": 7.901788434999682,
      "observed_mean_file_compression_pct": 34.996934896898736,
      "violations": []
    },
    "speed_test": null
  }
}
```

</details>

**Refusals**

- 404 `NOT_FOUND`: no such public submission
- 404 `SNAPSHOT_NOT_FOUND`: `snapshot_id` names no published snapshot

### GET `/v1/competitions/{slug}/submissions/{submission_id}/report`

The gate's stage-by-stage report. URLs, absolute paths and `password=` / `token=` style values are redacted.

**Access:** Public, no authentication.

**Parameters**

| Name | In | Type | Required | Constraints |
| --- | --- | --- | --- | --- |
| `slug` | path | string | yes |  |
| `submission_id` | path | integer | yes | ≥ 1 |

**Response**

`200` (JSON; no response model in the OpenAPI schema)

| Field | Type | Always present | Meaning |
| --- | --- | --- | --- |
| `submission_id` | string | yes | The submission id |
| `report` | string \| null | yes | Redacted report text; null before the gate has run |

<details><summary>Example: <code>GET /v1/competitions/lz77/submissions/1/report</code> on DEV</summary>

```json
{
  "submission_id": "1",
  "report": "verifying [internal-path]\nworkspace: [internal-path]\n\n0 intake      ok — 1: parse.rs, Parse.lean\n1 policy      ok — source prefilters passed\n2 static      ok — …"
}
```

</details>

**Refusals**

- 404 `NOT_FOUND`: no such public submission

### GET `/v1/competitions/{slug}/submissions/{submission_id}/source`

Both source files of an **accepted** submission. A miner's source is withheld while it is on the latest snapshot's Pareto frontier, or before a scoring pass has placed it, so the current best cannot be copied; it is published once another submission beats it. Baselines are always visible (they are the public `miner/examples`).

**Access:** Public, no authentication.

**Parameters**

| Name | In | Type | Required | Constraints |
| --- | --- | --- | --- | --- |
| `slug` | path | string | yes |  |
| `submission_id` | path | integer | yes | ≥ 1 |

**Response**

`200` (JSON; no response model in the OpenAPI schema)

| Field | Type | Always present | Meaning |
| --- | --- | --- | --- |
| `id` | string | yes | The submission id |
| `parse_rs` | string | yes | `parse.rs`, UTF-8 |
| `proof_lean` | string | yes | `Parse.lean`, UTF-8 |

**Refusals**

- 404: no published source (the submission is not accepted, or does not exist)
- 403 `SOURCE_WITHHELD`: a miner submission on the current Pareto frontier, or not yet placed by a scoring pass

### GET `/v1/competitions/{slug}/submissions/{submission_id}/source/{filename}`

One source file of an accepted submission, as plain text. Withheld under the same rule as the pair above.

**Access:** Public, no authentication.

**Parameters**

| Name | In | Type | Required | Constraints |
| --- | --- | --- | --- | --- |
| `slug` | path | string | yes |  |
| `filename` | path | `"parse.rs"` \| `"Parse.lean"` | yes |  |
| `submission_id` | path | integer | yes | ≥ 1 |

**Response**

`200`: `text/plain; charset=utf-8`: the file's bytes

**Refusals**

- 404: source unavailable
- 403 `SOURCE_WITHHELD`: a miner submission on the current Pareto frontier, or not yet placed by a scoring pass
- 422: `filename` is not `parse.rs` or `Parse.lean`

## Scoring

### GET `/v1/competitions/{slug}/pareto`

Every scored point on the time × size plane from the snapshot; frontier points carry `frontier_order`.

**Access:** Public, no authentication.

**Parameters**

| Name | In | Type | Required | Constraints |
| --- | --- | --- | --- | --- |
| `slug` | path | string | yes |  |
| `limit` | query | integer | no | ≥ 1, ≤ 100, default `25` |
| `cursor` | query | string \| null | no | max length 256 |
| `snapshot_id` | query | integer \| null | no | ≥ 1 |

**Response**

200: [ParetoPage](#schema-paretopage)

<details><summary>Example: <code>GET /v1/competitions/lz77/pareto?limit=1</code> on DEV</summary>

```json
{
  "context": {
    "snapshot_id": "4797",
    "computed_at": "2026-09-25T08:06:57.900177+00:00",
    "policy_version": "compression-policy-8e27894cabfbec95",
    "status": "ready",
    "freshness": "unknown",
    "freshness_reason": "Recorded scoring pass; live policy changes require a new pass."
  },
  "axes": {
    "x": "balanced_time_ratio",
    "y": "mean_file_compression_pct"
  },
  "bounds": {
    "eligible": null,
    "max_balanced_time_ratio": 10.0,
    "max_mean_file_compression_pct": 40.0,
    "observed_balanced_time_ratio": null,
    "observed_mean_file_compression_pct": null,
    "violations": []
  },
  "items": [
    {
      "id": "1",
      "kind": "miner",
      "hotkey": "5CvyPx3q4kC4jao42Hif7YUkpSm7wLSXn4L65DreofiKHYRZ",
      "baseline_name": null,
      "submitted_at": "2026-09-23T15:34:09.825314+00:00",
      "gate_status": "passed",
      "aggregation_id": "3",
      "metrics": {
        "balanced_time_ratio": 7.901788434999682,
        "mean_file_compression_pct": 34.996934896898736,
        "total_compression_seconds": 6.075678299,
        "lz77_seconds": 5.6948619009999994,
        "byte_weighted_compression_pct": 31.98187696170747
      },
      "admission": {
        "decision_id": "3",
        "status": "recorded",
        "outcome": "not_required",
        "admitted": true,
        "reason_code": "no-slower-better-compressing-neighbor",
        "explanation": "No speed comparison is required for this contribution.",
        "reference_submission_id": null,
        "freshness": "unknown"
      },
      "score": {
        "snapshot_id": "4797",
        "on_frontier": true,
        "pareto_weight": 0.6,
        "improvement_weight": 0.4,
        "combined_weight": 1.0,
        "payable_weight": 1.0,
        "payment_eligible": true,
        "unpaid_reason": null
      },
      "frontier_order": 0,
      "timing_interval": {
        "metric": "balanced_time_ratio",
        "estimate": 7.901788434999682,
        "lower": 7.767071115766546,
        "upper": 8.014685338460836,
        "confidence_level": 0.95,
        "method": "paired-per-file-compression-bootstrap-v4",
        "draws": 2000
      },
      "bounds": {
        "eligible": true,
        "max_balanced_time_ratio": 10.0,
        "max_mean_file_compression_pct": 40.0,
        "observed_balanced_time_ratio": 7.901788434999682,
        "observed_mean_file_compression_pct": 34.996934896898736,
        "violations": []
      }
    }
  ],
  "next_cursor": "Y29tcHJlc3Npb24tcGFyZXRvLjQ3OTcuMS5kZWNjMDNmOWM5OWQ5ZWM1Y2I3Zg.snEBT6AC84Th5c1g04trag"
}
```

</details>

**Refusals**

- 400 `INVALID_CURSOR`: the cursor is forged, expired or was issued for other filters
- 404 `SNAPSHOT_NOT_FOUND`: `snapshot_id` names no published snapshot
- 404 `NOT_FOUND`: unknown slug

### GET `/v1/competitions/{slug}/leaderboard`

Per-hotkey ranking by summed payable weight in the snapshot.

**Access:** Public, no authentication.

**Parameters**

| Name | In | Type | Required | Constraints |
| --- | --- | --- | --- | --- |
| `slug` | path | string | yes |  |
| `limit` | query | integer | no | ≥ 1, ≤ 100, default `25` |
| `cursor` | query | string \| null | no | max length 256 |
| `snapshot_id` | query | integer \| null | no | ≥ 1 |

**Response**

200: [Leaderboard](#schema-leaderboard)

<details><summary>Example: <code>GET /v1/competitions/lz77/leaderboard</code> on DEV</summary>

```json
{
  "context": {
    "snapshot_id": "4797",
    "computed_at": "2026-09-25T08:06:57.900177+00:00",
    "policy_version": "compression-policy-8e27894cabfbec95",
    "status": "ready",
    "freshness": "unknown",
    "freshness_reason": "Recorded scoring pass; live policy changes require a new pass."
  },
  "ranking": [
    {
      "rank": 1,
      "hotkey": "5CvyPx3q4kC4jao42Hif7YUkpSm7wLSXn4L65DreofiKHYRZ",
      "submission_ids": [
        "1"
      ],
      "pareto_weight": 0.6,
      "improvement_weight": 0.4,
      "combined_weight": 1.0,
      "payable_weight": 1.0
    },
    "…"
  ],
  "next_cursor": null
}
```

</details>

**Refusals**

- 400 `INVALID_CURSOR`: the cursor is forged, expired or was issued for other filters
- 404 `SNAPSHOT_NOT_FOUND`: `snapshot_id` names no published snapshot
- 404 `NOT_FOUND`: unknown slug

### GET `/v1/competitions/{slug}/weights/current`

The competition's weight vector: each hotkey's payable fraction of the competition share, and whether the chain accepted it.

**Access:** Public, no authentication.

**Parameters**

| Name | In | Type | Required | Constraints |
| --- | --- | --- | --- | --- |
| `slug` | path | string | yes |  |
| `snapshot_id` | query | integer \| null | no | ≥ 1 |

**Response**

200: [Weights](#schema-weights)

<details><summary>Example: <code>GET /v1/competitions/lz77/weights/current</code> on DEV</summary>

```json
{
  "competition": "lz77",
  "context": {
    "snapshot_id": "4797",
    "computed_at": "2026-09-25T08:06:57.900177+00:00",
    "policy_version": "compression-policy-8e27894cabfbec95",
    "status": "ready",
    "freshness": "unknown",
    "freshness_reason": "Recorded scoring pass; live policy changes require a new pass."
  },
  "weights": {
    "5CvyPx3q4kC4jao42Hif7YUkpSm7wLSXn4L65DreofiKHYRZ": 1.0
  },
  "payable_competition_weight": 1.0,
  "unpaid_competition_weight": 0.0,
  "competition_share": 0.2,
  "weight_set_id": "4797",
  "dry_run": false,
  "chain_accepted": false
}
```

</details>

**Refusals**

- 404 `SNAPSHOT_NOT_FOUND`: `snapshot_id` names no published snapshot
- 404 `NOT_FOUND`: unknown slug

## Operator

### GET `/v1/competitions/{slug}/admin/queue`

Stuck work: errored rows, and `verifying` rows whose claim is older than `COMPETITION_STALE_CLAIM_SECONDS` (7200).

**Access:** ADMIN role, browser session. Responses carry `Cache-Control: no-store`.

**Parameters**

| Name | In | Type | Required | Constraints |
| --- | --- | --- | --- | --- |
| `slug` | path | string | yes | max length 64 |
| `limit` | query | integer | no | ≥ 1, ≤ 100, default `25` |
| `cursor` | query | string \| null | no | max length 256 |

**Response**

200: [CursorPage<OperatorSubmission>](#schema-cursorpage-operatorsubmission)

**Refusals**

- 403 `ROLE_NEEDS_BROWSER`: called with a CLI bearer token
- 403 `ROLE_REQUIRED`: the account lacks the ADMIN role
- 503 `OPERATOR_ADAPTER_UNAVAILABLE`: **always, on the current schema** (the adapter refuses any database with `submissions.verification_attempt`, which the public routes require)
- 400 `INVALID_CURSOR`: the cursor is forged, expired or was issued for other filters

### GET `/v1/competitions/{slug}/admin/submissions/{submission_id}`

One submission with its queue bookkeeping (worker, attempts, report).

**Access:** ADMIN role, browser session. `Cache-Control: no-store`.

**Parameters**

| Name | In | Type | Required | Constraints |
| --- | --- | --- | --- | --- |
| `slug` | path | string | yes | max length 64 |
| `submission_id` | path | integer | yes | ≥ 1 |

**Response**

200: [OperatorSubmission](#schema-operatorsubmission)

**Refusals**

- 403 `ROLE_NEEDS_BROWSER`: called with a CLI bearer token
- 403 `ROLE_REQUIRED`: the account lacks the ADMIN role
- 503 `OPERATOR_ADAPTER_UNAVAILABLE`: **always, on the current schema** (the adapter refuses any database with `submissions.verification_attempt`, which the public routes require)
- 404 `NOT_FOUND`

### POST `/v1/competitions/{slug}/admin/submissions/{submission_id}/requeue`

Move an `error` or stuck `verifying` row back to `queued`, with a reason.

**Access:** ADMIN role, browser session, write. `Cache-Control: no-store`.

**Parameters**

| Name | In | Type | Required | Constraints |
| --- | --- | --- | --- | --- |
| `slug` | path | string | yes | max length 64 |
| `submission_id` | path | integer | yes | ≥ 1 |
| `reason` | query | string \| null | no | max length 200 |

**Response**

200: [Requeued](#schema-requeued)

**Refusals**

- 403 `ROLE_NEEDS_BROWSER`: called with a CLI bearer token
- 403 `ROLE_REQUIRED`: the account lacks the ADMIN role
- 503 `OPERATOR_ADAPTER_UNAVAILABLE`: **always, on the current schema** (the adapter refuses any database with `submissions.verification_attempt`, which the public routes require)
- 404 `NOT_FOUND`

## Signing a submission

`POST /v1/competitions/{slug}/submissions` authenticates by an sr25519 signature from the subnet hotkey over these five lines, joined by `\n` with no trailing newline:

```
conjectures-competition-submit-v1
competition: <slug>
digest: <digest>
hotkey: <hotkey ss58>
timestamp: <unix seconds>
```

- `digest` is lowercase hex of sha256 over the `parse.rs` bytes immediately followed by the `Parse.lean` bytes.
- `timestamp` is the same integer sent in `X-Conjectures-Timestamp`; it must be within 300 s of server time.
- `X-Conjectures-Signature` is the signature as hex, with or without `0x`.
- The server rebuilds the message from the slug in the path and the bytes it received, so any mismatch is `SIGNATURE_INVALID`.
- Reference client: `python miner/submit.py submit <dir> --hotkey <hotkey file> --url https://<api>` in the conjectures-optimisation-lz77 repository.

## Schemas

Every response model referenced above, field by field. "Required" means always present in the JSON; a type ending in `null` may be null.

<a id="schema-index"></a>

### Index

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `items` | array of [Competition](#schema-competition) | yes |  |

<a id="schema-competition"></a>

### Competition

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `slug` | string | yes |  |
| `name` | string | yes |  |
| `submissions_open` | boolean | yes |  |
| `queue_depth` | integer | yes |  |
| `current_snapshot_id` | string \| null | yes | Scoring snapshot the reads use by default |
| `description` | string | yes |  |
| `files` | array of object | yes | Each `{name: string, max_bytes: integer}`: `parse.rs` and `Parse.lean`, 524288 bytes each |
| `metric_definitions` | array of object | yes | Each `{key, label, unit, better}` (all strings): `balanced_time_ratio` (ratio) and `mean_file_compression_pct` (percent), both `better: "lower"` |
| `policy` | object \| null | yes | The snapshot's scoring policy: `method`, `version`, `speed_floor`, `max_balanced_time_ratio`, `max_mean_file_compression_pct`, `max_ratio_pct`, `pareto_share`, `improvement_share`, `improvement_window`, `improvement_threshold`, `improvement_decay`, `competition_share`, `alpha_total_submission_bounty`, `required_corpora` (array of string), `bootstrap_draws`, `confidence_level`, `admission_policy_version`. Null before the first snapshot |
| `policy_status` | string | yes | `"ready"` once a snapshot has published a policy |
| `execution_limits` | object | yes | `{benchmark_timeout_seconds, gate_timeout_seconds}`, both integer or null (null today) |
| `context` | [Context](#schema-context) | yes |  |

<a id="schema-accepted"></a>

### Accepted

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `competition` | string | yes |  |
| `submission` | string | yes | Submission id |
| `state` | string | yes | `"queued"` for a new submission; the current state for a repeat |
| `digest` | string | yes | sha256 hex of `parse.rs` bytes followed by `Parse.lean` bytes |
| `created` | boolean | yes | False when the same files were already queued by this hotkey |
| `slots_remaining` | integer | yes | Unspent registrations left for more submissions |
| `status_url` | string | yes | Path of the submission's detail endpoint |

<a id="schema-submissionpage"></a>

### SubmissionPage

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `context` | [Context](#schema-context) | yes |  |
| `items` | array of [Submission](#schema-submission) | yes |  |
| `next_cursor` | string \| null | no |  |

<a id="schema-detail"></a>

### Detail

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `submission` | [Submission](#schema-submission) | yes |  |
| `digest` | string | yes |  |
| `source_sha256` | string \| null | yes |  |
| `pipeline_observed_at` | string | yes | When the pipeline state was read |
| `pipeline` | map of string → [Stage](#schema-stage) | yes | Keys `static`, `lean`, `benchmark`, `aggregation` |
| `current_evidence` | map of string → string \| null | yes | Keys `aggregation_id`, `admission_check_id` |
| `bounds` | [Bounds](#schema-bounds) \| null | yes |  |
| `context` | [Context](#schema-context) | yes |  |
| `links` | map of string → string | yes | Keys `report`, `source`, `admission`: paths of those endpoints |

<a id="schema-admissionresponse"></a>

### AdmissionResponse

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `context` | [Context](#schema-context) \| null | yes |  |
| `admission` | [Admission](#schema-admission) | yes |  |
| `decision` | [AdmissionDecision](#schema-admissiondecision) \| null | yes |  |

<a id="schema-paretopage"></a>

### ParetoPage

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `context` | [Context](#schema-context) | yes |  |
| `axes` | map of string → string | yes |  |
| `bounds` | [Bounds](#schema-bounds) | yes |  |
| `items` | array of [ParetoPoint](#schema-paretopoint) | yes |  |
| `next_cursor` | string \| null | no |  |

<a id="schema-leaderboard"></a>

### Leaderboard

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `context` | [Context](#schema-context) | yes |  |
| `bounty_limit_alpha` | number \| null | no | The most alpha any one (hotkey, submission) pair is paid over its lifetime; null before the competition recorded bounties |
| `ranking` | array of [Ranking](#schema-ranking) | yes |  |
| `next_cursor` | string \| null | no |  |

<a id="schema-weights"></a>

### Weights

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `competition` | string | yes |  |
| `context` | [Context](#schema-context) | yes |  |
| `weights` | map of string → number | yes | Hotkey ss58 → payable fraction of the competition share (only hotkeys with weight > 0) |
| `payable_competition_weight` | number \| null | no |  |
| `unpaid_competition_weight` | number \| null | no |  |
| `competition_share` | number \| null | no | Share of the validator's vector the competition controls (0.2) |
| `weight_set_id` | string \| null | no |  |
| `dry_run` | boolean \| null | no | True when the weight setter did not submit |
| `chain_accepted` | boolean \| null | no | Whether the chain accepted this vector. Usually false: the weight setter records a scoring pass every poll (about 13 s) and submits only one per epoch, so the latest pass is normally one that was not submitted |

<a id="schema-cursorpage-operatorsubmission"></a>

### CursorPage<OperatorSubmission>

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `items` | array of [OperatorSubmission](#schema-operatorsubmission) | yes |  |
| `next_cursor` | string \| null | no |  |

<a id="schema-operatorsubmission"></a>

### OperatorSubmission

A submission with the queue bookkeeping the public view omits.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `competition` | string | yes |  |
| `id` | integer | yes |  |
| `hotkey` | string | yes |  |
| `digest` | string | yes |  |
| `submitted_at` | string | yes |  |
| `state` | string | yes |  |
| `exit_code` | integer \| null | no |  |
| `attempts` | integer | yes |  |
| `worker_id` | string \| null | no |  |
| `claimed_at` | string \| null | no |  |
| `finished_at` | string \| null | no |  |
| `account_id` | string \| null | no |  |
| `has_sources` | boolean | yes |  |
| `report` | string \| null | no |  |

<a id="schema-requeued"></a>

### Requeued

What a requeue did. `requeued` is false when the row was not one to requeue.

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `competition` | string | yes |  |
| `id` | integer | yes |  |
| `requeued` | boolean | yes |  |
| `state` | string | yes |  |

<a id="schema-context"></a>

### Context

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `snapshot_id` | string \| null | no |  |
| `computed_at` | string \| null | no |  |
| `policy_version` | string \| null | no |  |
| `status` | `"ready"` \| `"not_ready"` | no | default `"not_ready"` |
| `freshness` | `"current"` \| `"stale"` \| `"unknown"` | no | default `"unknown"` |
| `freshness_reason` | string \| null | no |  |

<a id="schema-submission"></a>

### Submission

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string | yes |  |
| `kind` | `"miner"` \| `"baseline"` | yes |  |
| `hotkey` | string \| null | yes |  |
| `baseline_name` | string \| null | yes | Set for operator baselines, which are never paid |
| `submitted_at` | string | yes |  |
| `gate_status` | `"queued"` \| `"running"` \| `"passed"` \| `"failed"` \| `"error"` \| `"unknown"` | yes | `passed` = accepted by the gate |
| `aggregation_id` | string \| null | no |  |
| `metrics` | [Metrics](#schema-metrics) \| null | no |  |
| `admission` | [Admission](#schema-admission) | yes |  |
| `score` | [Score](#schema-score) \| null | no |  |

<a id="schema-stage"></a>

### Stage

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `status` | `"pending"` \| `"running"` \| `"passed"` \| `"failed"` \| `"stale"` \| `"unknown"` | yes |  |
| `started_at` | string \| null | no |  |
| `finished_at` | string \| null | no |  |
| `reason_code` | string \| null | no |  |
| `message` | string \| null | no |  |

<a id="schema-bounds"></a>

### Bounds

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `eligible` | boolean \| null | no |  |
| `max_balanced_time_ratio` | number \| null | no |  |
| `max_mean_file_compression_pct` | number \| null | no |  |
| `observed_balanced_time_ratio` | number \| null | no |  |
| `observed_mean_file_compression_pct` | number \| null | no |  |
| `violations` | array of string | no | Which bounds were exceeded, e.g. `time-ratio-limit`; default `[]` |

<a id="schema-admission"></a>

### Admission

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `decision_id` | string \| null | no |  |
| `status` | `"pending"` \| `"recorded"` \| `"invalid"` \| `"unknown"` | no | default `"pending"` |
| `outcome` | `"passed"` \| `"not_required"` \| `"inconclusive"` \| `"dominated"` \| `"excluded"` \| null | no |  |
| `admitted` | boolean \| null | no |  |
| `reason_code` | string \| null | no |  |
| `explanation` | string \| null | no |  |
| `reference_submission_id` | string \| null | no |  |
| `freshness` | `"current"` \| `"stale"` \| `"unknown"` | no | default `"unknown"` |

<a id="schema-admissiondecision"></a>

### AdmissionDecision

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string \| null | yes |  |
| `submission_id` | string | yes |  |
| `created_at` | string \| null | no |  |
| `outcome` | string | yes |  |
| `admitted` | boolean | yes |  |
| `reason_code` | string \| null | yes |  |
| `explanation` | string | yes |  |
| `policy_version` | string \| null | yes |  |
| `candidate_aggregation_id` | string \| null | yes |  |
| `reference_submission_id` | string \| null | yes |  |
| `reference_aggregation_id` | string \| null | yes |  |
| `bounds` | [Bounds](#schema-bounds) \| null | yes |  |
| `speed_test` | [SpeedTest](#schema-speedtest) \| null | yes |  |

<a id="schema-paretopoint"></a>

### ParetoPoint

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `id` | string | yes |  |
| `kind` | `"miner"` \| `"baseline"` | yes |  |
| `hotkey` | string \| null | yes |  |
| `baseline_name` | string \| null | yes |  |
| `submitted_at` | string | yes |  |
| `gate_status` | `"queued"` \| `"running"` \| `"passed"` \| `"failed"` \| `"error"` \| `"unknown"` | yes |  |
| `aggregation_id` | string \| null | no |  |
| `metrics` | [Metrics](#schema-metrics) \| null | no |  |
| `admission` | [Admission](#schema-admission) | yes |  |
| `score` | [Score](#schema-score) \| null | no |  |
| `frontier_order` | integer \| null | no |  |
| `timing_interval` | [TimingInterval](#schema-timinginterval) \| null | no |  |
| `bounds` | [Bounds](#schema-bounds) \| null | no |  |

<a id="schema-ranking"></a>

### Ranking

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `rank` | integer | yes |  |
| `hotkey` | string | yes |  |
| `submission_ids` | array of string | yes |  |
| `pareto_weight` | number | yes |  |
| `improvement_weight` | number | yes |  |
| `combined_weight` | number | yes |  |
| `payable_weight` | number | yes | Summed over the hotkey's submissions |
| `bounty_earned_alpha` | number \| null | no | Sum of `bounty_earned_alpha` over the hotkey's submissions; each submission is capped separately |

<a id="schema-metrics"></a>

### Metrics

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `balanced_time_ratio` | number \| null | no | Mean over files of (file time / incumbent file time) |
| `mean_file_compression_pct` | number \| null | no | Mean over files of compressed size as % of original |
| `total_compression_seconds` | number \| null | no |  |
| `lz77_seconds` | number \| null | no |  |
| `byte_weighted_compression_pct` | number \| null | no |  |

<a id="schema-score"></a>

### Score

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `snapshot_id` | string | yes |  |
| `on_frontier` | boolean | yes |  |
| `pareto_weight` | number | yes |  |
| `improvement_weight` | number | yes |  |
| `combined_weight` | number | yes |  |
| `payable_weight` | number | yes | Fraction of the competition share this submission is paid |
| `payment_eligible` | boolean | yes |  |
| `unpaid_reason` | string \| null | no | Why a scored point is not paid, e.g. outside scoring bounds, or `bounty-cap` once it has reached the submission bounty |
| `bounty_earned_alpha` | number \| null | no | Alpha this submission had received when the pass ran, toward the submission bounty (`policy.alpha_total_submission_bounty`, default 3600). Null for baselines and for passes before the competition recorded bounties |
| `bounty_capped` | boolean \| null | no | True once the submission has received, or would pass, the bounty: it is paid nothing more, permanently, and its share goes to the treasury |

<a id="schema-speedtest"></a>

### SpeedTest

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `estimated_gain` | number | yes |  |
| `lower_confidence_bound` | number | yes |  |
| `confidence_level` | number | yes |  |
| `threshold` | number | yes |  |
| `comparison` | string | yes |  |
| `passed` | boolean | yes |  |
| `method` | string | yes |  |
| `draws` | integer | yes |  |
| `resampling` | string | yes |  |
| `file_count` | integer \| null | yes |  |
| `corpora` | array of string | yes |  |
| `limitations` | array of string | yes |  |
| `candidate_timing` | [TimingInterval](#schema-timinginterval) \| null | no |  |
| `reference_timing` | [TimingInterval](#schema-timinginterval) \| null | no |  |
| `gain_display_interval` | [DisplayInterval](#schema-displayinterval) \| null | no |  |
| `comparison_range` | [DisplayInterval](#schema-displayinterval) \| null | no |  |
| `candidate` | object \| null | no |  |
| `reference` | object \| null | no |  |
| `frontier_before` | array of object | no | default `[]` |

<a id="schema-timinginterval"></a>

### TimingInterval

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `metric` | string | yes |  |
| `estimate` | number | yes |  |
| `lower` | number | yes |  |
| `upper` | number | yes |  |
| `confidence_level` | number | yes |  |
| `method` | string | yes |  |
| `draws` | integer | yes |  |

<a id="schema-displayinterval"></a>

### DisplayInterval

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `lower` | number | yes |  |
| `upper` | number | yes |  |
| `confidence_level` | number | yes |  |
| `method` | string | yes |  |
| `sidedness` | string | yes |  |
