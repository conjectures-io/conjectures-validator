#!/usr/bin/env python3
"""Sign and submit a proof bundle, or read a submission's status.

The miner-side reference client. Builds the canonical request digest exactly as the validator
does, signs it with your hotkey, and sends the bundle as the raw request body.

    # submit
    python3 scripts/submit_proof.py --api https://host \
      --bundle submission.zip --task /path/to/task --task-id <id> --task-sha256 sha256:… \
      --payment-ref <extrinsic> --wallet default --hotkey default

    # check status, then the report
    python3 scripts/submit_proof.py --api https://host --status <submission_id> \
      --wallet default --hotkey default
    python3 scripts/submit_proof.py --api https://host --report <submission_id> \
      --wallet default --hotkey default

Signing needs a keypair, so `--wallet/--hotkey` (a real Bittensor wallet) or `--uri` (a
development key such as `//Alice`) is required. See docs/MINER.md for the whole flow.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from bittensor.sp_core import Keypair
from bittensor.wallet import Wallet

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from conjectures_subnet.attribution import (  # noqa: E402
    PublicCredit,
    encode_public_credit_header,
    public_credit,
)
from verifier.bundle import load_proof_bundle, read_bundle_file
from verifier.errors import VerifierError
from verifier.preflight import BundlePreflight, verify_proof_bundle_bytes

READ_DOMAIN = "conjectures-read-v1"


def digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def canonical_request_digest(
    *,
    hotkey: str,
    task_id: str,
    task_bundle_sha256: str,
    proof_sha256: str,
    payment_reference: str,
    idempotency_key: str,
    public_credit: PublicCredit | None = None,
) -> str:
    """The message the hotkey signs.

    Kept byte-identical to `conjectures_subnet.db.submissions.canonical_request_digest`: sorted
    keys, no spaces, one trailing newline.
    """
    value = {
        "hotkey": hotkey,
        "idempotency_key": idempotency_key,
        "payment_reference": payment_reference,
        "proof_sha256": proof_sha256,
        "task_bundle_sha256": task_bundle_sha256,
        "task_id": task_id,
    }
    if public_credit is not None:
        value["public_credit"] = public_credit.to_dict()
    payload = (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    return digest(payload)


def load_keypair(args):
    if args.uri:
        return Keypair.create_from_uri(args.uri)
    if not (args.wallet and args.hotkey):
        raise SystemExit("provide --wallet and --hotkey, or --uri for a development key")
    wallet = Wallet(name=args.wallet, hotkey=args.hotkey, path=args.wallet_path)
    return wallet.hotkey


def call(url: str, *, method: str, headers: dict[str, str], data: bytes | None = None):
    request = urllib.request.Request(url, data=data, method=method)
    for key, value in headers.items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"raw": body.decode(errors="replace")}


def timestamp_ms() -> str:
    import time

    return str(int(time.time() * 1000))


def read_headers(keypair, submission_id: str) -> dict[str, str]:
    message = hashlib.sha256(
        f"{READ_DOMAIN}:{keypair.ss58_address}:{submission_id}".encode()
    ).digest()
    return {
        "X-Conjectures-Hotkey": keypair.ss58_address,
        "X-Conjectures-Timestamp": timestamp_ms(),
        "X-Conjectures-Signature": keypair.sign(message).hex(),
    }


def _preflight_failure(value: object) -> None:
    print("Local verification failed; the bundle was not submitted.", file=sys.stderr)
    print(json.dumps(value, indent=2, sort_keys=True), file=sys.stderr)


def preflight(args, keypair, raw: bytes) -> BundlePreflight | None:
    """Run the validator's full proof path before making a paid submission request."""
    try:
        result = verify_proof_bundle_bytes(
            raw=raw,
            task_dir=args.task,
            project_root=ROOT,
            expected_task_id=args.task_id,
            expected_task_sha256=args.task_sha256,
            expected_hotkey=keypair.ss58_address,
            allow_insecure_development=args.allow_insecure_local_verification,
        )
    except (OSError, ValueError, VerifierError) as exc:
        reason = getattr(getattr(exc, "reason", None), "value", "LOCAL_VERIFICATION_ERROR")
        _preflight_failure({"accepted": False, "reason_code": reason, "error": str(exc)})
        return None
    if not result.report.accepted:
        _preflight_failure(result.report.to_dict())
        return None
    print(
        f"Local Lean verification: {result.report.reason_code.value} "
        f"({result.report.duration_ms} ms)",
        file=sys.stderr,
    )
    return result


def submit(args, keypair) -> int:
    try:
        credit = public_credit(
            getattr(args, "credit_name", None),
            getattr(args, "credit_url", None),
            getattr(args, "credit_orcid", None),
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    try:
        bundle = read_bundle_file(Path(args.bundle))
        parsed = load_proof_bundle(bundle)
    except (OSError, VerifierError) as exc:
        reason = getattr(getattr(exc, "reason", None), "value", "BUNDLE_MALFORMED")
        _preflight_failure({"accepted": False, "reason_code": reason, "error": str(exc)})
        return 1

    if not args.skip_local_verification:
        checked = preflight(args, keypair, bundle)
        if checked is None:
            return 1
        parsed = checked.bundle
    proof_sha256 = parsed.proof.sha256
    key = args.idempotency_key or str(uuid.uuid4())
    request_digest = canonical_request_digest(
        hotkey=keypair.ss58_address,
        task_id=args.task_id,
        task_bundle_sha256=args.task_sha256,
        proof_sha256=proof_sha256,
        payment_reference=args.payment_ref,
        idempotency_key=key,
        public_credit=credit,
    )
    signature = keypair.sign(bytes.fromhex(request_digest.removeprefix("sha256:")))
    headers = {
        "Content-Type": "application/zip",
        "Content-Length": str(len(bundle)),
        "Idempotency-Key": key,
        "X-Conjectures-Hotkey": keypair.ss58_address,
        "X-Conjectures-Timestamp": timestamp_ms(),
        "X-Conjectures-Signature": signature.hex(),
        "X-Conjectures-Task-Id": args.task_id,
        "X-Conjectures-Task-Sha256": args.task_sha256,
        "X-Conjectures-Proof-Sha256": proof_sha256,
        "X-Conjectures-Payment-Ref": args.payment_ref,
    }
    if credit is not None:
        headers["X-Conjectures-Public-Credit"] = encode_public_credit_header(credit)
    status, body = call(
        f"{args.api.rstrip('/')}/v1/submissions", method="POST", headers=headers, data=bundle
    )
    print(f"HTTP {status}")
    print(json.dumps(body, indent=2, sort_keys=True))
    if status in {200, 201}:
        print(f"\nidempotency key (reuse this to retry safely): {key}")
        print(f"submission id: {body.get('submission_id')}")
        return 0
    return 1


def read(args, keypair, submission_id: str, *, report: bool) -> int:
    suffix = "/report" if report else ""
    status, body = call(
        f"{args.api.rstrip('/')}/v1/submissions/{submission_id}{suffix}",
        method="GET",
        headers=read_headers(keypair, submission_id),
    )
    print(f"HTTP {status}")
    print(json.dumps(body, indent=2, sort_keys=True))
    return 0 if status == 200 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", required=True, help="validator base URL")
    parser.add_argument("--wallet", help="Bittensor wallet name")
    parser.add_argument("--hotkey", help="hotkey name within that wallet")
    parser.add_argument("--wallet-path", default=None, help="override the wallet directory")
    parser.add_argument("--uri", help="development key such as //Alice; not for production")

    parser.add_argument("--bundle", help="the conjectures-submission/v1 zip to submit")
    local = parser.add_mutually_exclusive_group()
    local.add_argument(
        "--task",
        type=Path,
        help="local task bundle directory used for the required Lean preflight",
    )
    local.add_argument(
        "--skip-local-verification",
        action="store_true",
        help="submit without running the local Lean verifier (not recommended)",
    )
    parser.add_argument(
        "--allow-insecure-local-verification",
        action="store_true",
        help="run the full local proof checks with the development sandbox shim",
    )
    parser.add_argument("--task-id")
    parser.add_argument("--task-sha256", help="the published task_bundle_sha256")
    parser.add_argument("--payment-ref", help="finalized extrinsic reference for your 0.5 TAO")
    parser.add_argument(
        "--idempotency-key",
        help="reuse a previous key to retry the same submission safely (a UUID)",
    )
    parser.add_argument(
        "--credit-name",
        help="public author or team name to publish if the result verifies",
    )
    parser.add_argument(
        "--credit-url",
        help="optional https profile or project URL (requires --credit-name)",
    )
    parser.add_argument(
        "--credit-orcid",
        help="optional ORCID such as 0000-0002-1825-0097 (requires --credit-name)",
    )

    parser.add_argument("--status", metavar="SUBMISSION_ID", help="read a submission's status")
    parser.add_argument("--report", metavar="SUBMISSION_ID", help="read the verifier report")
    args = parser.parse_args(argv)

    keypair = load_keypair(args)
    if args.status:
        return read(args, keypair, args.status, report=False)
    if args.report:
        return read(args, keypair, args.report, report=True)
    missing = [
        name
        for name in ("bundle", "task_id", "task_sha256", "payment_ref")
        if getattr(args, name) is None
    ]
    if args.task is None and not args.skip_local_verification:
        missing.append("task")
    if missing:
        raise SystemExit(
            "submitting needs --" + ", --".join(name.replace("_", "-") for name in missing)
        )
    if args.allow_insecure_local_verification and args.task is None:
        raise SystemExit("--allow-insecure-local-verification requires --task")
    return submit(args, keypair)


if __name__ == "__main__":
    raise SystemExit(main())
