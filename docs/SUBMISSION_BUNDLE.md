# Submission bundle format: `conjectures-submission/v1`

A miner submits one ZIP archive. It carries the candidate Lean proof together with the task
it claims to solve, so the whole submission is a single content-addressed artifact that the
miner signs and the validator stores, logs, and references in the verifier report.

Validate a bundle locally before spending a payment on it:

```bash
python3 -m verifier bundle scan --bundle submission.zip
```

That prints the same verdict and the same `reason_code` the API would return, so a rejected
bundle costs nothing to diagnose.

## Layout

Exactly two entries, in exactly this order:

```
submission.json     the manifest, at most 16 KiB
Main.lean           the candidate proof, at most 1,000,000 bytes
```

No directories, no third file, no nested archive, and no other names. The whole archive must
be at most 2 MiB.

## `submission.json`

```json
{
  "schema_version": 1,
  "format": "conjectures-submission/v1",
  "task_id": "fc-e923379e-erdos89-erdos-89-918868c888-formalized-v1",
  "task_bundle_sha256": "sha256:9f2c…",
  "proof_path": "Main.lean",
  "proof_sha256": "sha256:8a73…",
  "proof_bytes": 1234,
  "miner_hotkey": "5Grw…",
  "solver": { "name": "my-solver", "version": "1.2.3" }
}
```

| Field | Rule |
| --- | --- |
| `schema_version` | Exactly `1` |
| `format` | Exactly `conjectures-submission/v1` |
| `task_id` | An allowlisted task id from `GET /v1/tasks` |
| `task_bundle_sha256` | That task's published digest, `sha256:` + 64 lowercase hex |
| `proof_path` | Exactly `Main.lean` |
| `proof_sha256` | Digest of the archived `Main.lean`, recomputed and compared server-side |
| `proof_bytes` | Length of the archived `Main.lean`, also recomputed and compared |
| `miner_hotkey` | The submitting hotkey's SS58 address; must equal the authenticated hotkey |
| `solver` | Optional. Both `name` and `version` must match `[A-Za-z0-9._-]{1,64}`. Recorded for audit only |

Unknown fields, missing fields, duplicate JSON keys, and the JSON constants `NaN`,
`Infinity`, and `-Infinity` are all rejected. Declaring a `proof_sha256` or `proof_bytes`
that disagrees with the archived bytes is a `BUNDLE_DIGEST_MISMATCH`, which turns a silent
truncation into a specific error.

## `Main.lean`

The proof is the only untrusted content that reaches Lean. It must be a single valid UTF-8
document with no NUL bytes, within the task's `max_submission_bytes` (1,000,000 for
production tasks), and it must pass the static Lean policy scanner described in
[`../README.md`](../README.md#submission-policy-and-verification-stages). Admission runs
that scanner immediately so a policy violation is reported at submission time rather than
after verification.

## Archive requirements

The archive is admitted against an exact shape rather than sanitized, because refusing
everything except one known-good structure is far easier to get right than enumerating
attacks. A bundle is accepted only if all of the following hold.

- Only the `stored` (0) and `deflate` (8) compression methods are used.
- No encryption, no data descriptors, no patched or masked headers. Only the UTF-8 name flag
  and the deflate level hints may be set.
- No archive comment and no per-entry comments; the end-of-central-directory record is the
  final 22 bytes of the file.
- Nothing before the first local file header and nothing after the EOCD, which rules out
  self-extracting stubs, appended second archives, and polyglot files.
- Not split or spanned across disks.
- No entry is a directory, a symlink, a device, a FIFO, or a socket, and no entry is marked
  executable, setuid, setgid, or sticky.
- Extra fields are at most 256 bytes per entry, so ordinary extended-timestamp records from
  `zip(1)` are fine.
- Each entry's local header agrees with the central directory on name, method, flags, CRC,
  and both sizes. A disagreement is refused rather than resolved, because that is exactly
  the condition under which two ZIP readers see two different archives.
- Every entry's CRC-32 matches its decompressed bytes, and nothing decompresses past its
  limit. The limit is enforced by a bounded read, not by trusting the declared size, so a
  bomb is stopped whatever its header claims. A ratio above 200:1 is refused as well.

Archives written by `zip`, `zip -X`, Python's `zipfile`, and other ordinary tools satisfy all
of this; the interoperability tests in
[`../tests/test_bundle.py`](../tests/test_bundle.py) cover those writers explicitly.

Note that the archive is **never extracted**. Entry names are only ever compared against the
two-name allowlist and are never used as filesystem paths, and the proof is written out by
the validator under a name it chooses. Path traversal, symlink, and special-file attacks are
therefore not filtered — they are unrepresentable.

There is deliberately no antivirus step. The only content admitted is one UTF-8 Lean source
file and one small JSON object; neither is ever executed by the API, and the proof is only
ever compiled inside the one-shot Landlock/seccomp container described in
[`../SECURITY.md`](../SECURITY.md). A signature scanner would add a network-updating
dependency to a deliberately pinned, offline trust base while detecting nothing the
allowlist does not already exclude.

## Rejection reasons

| `reason_code` | Meaning |
| --- | --- |
| `BUNDLE_TOO_LARGE` | The archive, the manifest, or the proof is over its limit |
| `BUNDLE_MALFORMED` | Not a well-formed archive: truncated, bad CRC, corrupt deflate stream |
| `BUNDLE_POLICY_VIOLATION` | Well-formed but outside the admitted shape |
| `BUNDLE_MANIFEST_INVALID` | `submission.json` is not strict, complete, and exact |
| `BUNDLE_DIGEST_MISMATCH` | The manifest's declared proof digest or length is wrong |
| `SUBMISSION_NOT_UTF8` | `Main.lean` is not valid UTF-8 |
| `SUBMISSION_POLICY_VIOLATION` | `Main.lean` failed the static Lean policy scanner |
| `TASK_COMMITMENT_MISMATCH` | The manifest names a different task digest than the request |

## Building one

Use [`../scripts/build_submission_bundle.py`](../scripts/build_submission_bundle.py), which
is stdlib-only and can be copied into a miner without adding dependencies:

```bash
python3 scripts/build_submission_bundle.py \
  --proof Main.lean \
  --task-id fc-e923379e-erdos89-erdos-89-918868c888-formalized-v1 \
  --task-sha256 sha256:9f2c… \
  --hotkey 5Grw… \
  --output submission.zip
```

It prints the `bundle_sha256` you need for the request headers and the signature.

By hand, the equivalent is:

```python
import hashlib, json, zipfile

proof = open("Main.lean", "rb").read()
digest = lambda data: f"sha256:{hashlib.sha256(data).hexdigest()}"
manifest = json.dumps({
    "schema_version": 1,
    "format": "conjectures-submission/v1",
    "task_id": TASK_ID,
    "task_bundle_sha256": TASK_SHA256,
    "proof_path": "Main.lean",
    "proof_sha256": digest(proof),
    "proof_bytes": len(proof),
    "miner_hotkey": HOTKEY,
}, indent=2, sort_keys=True).encode()

with zipfile.ZipFile("submission.zip", "w", zipfile.ZIP_DEFLATED) as archive:
    archive.writestr("submission.json", manifest)   # order matters
    archive.writestr("Main.lean", proof)
```

See [`API.md`](API.md) for how to sign and submit it.

## Why a ZIP

A bare `.lean` upload would leave the task binding, the proof digest, and the solver
provenance stranded in HTTP headers, where they are not part of the stored artifact. A ZIP
makes the submission one self-describing, signable blob whose digest is what gets recorded
end to end, and leaves room to add optional files later without changing the transport.

A tar was considered and rejected. Tar has no central directory, so the entry set cannot be
checked without streaming the entire payload, and tar entries carry owners, device nodes,
hardlinks, PAX attributes, and GNU sparse records — far more metadata to defend against. A
ZIP's complete structure can be enumerated from its central directory before any entry data
is decompressed, which is what the admission checks above rely on.
