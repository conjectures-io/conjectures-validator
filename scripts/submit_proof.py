#!/usr/bin/env python3
"""Sign and submit a proof bundle, or read a submission's status.

The miner-side reference client. Builds the canonical request digest exactly as the validator
does, signs it with your hotkey, and sends the bundle as the raw request body.

    # submit
    python3 scripts/submit_proof.py --api https://host \
      --bundle submission.zip --task-id <id> --task-sha256 sha256:… \
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
import zipfile
from pathlib import Path

from bittensor.sp_core import Keypair
from bittensor.wallet import Wallet

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# The messages this script signs are built by the module the validator imports to rebuild and
# verify them, rather than reimplemented here. A local copy that drifted by one byte would make
# every submission this script sends fail authentication, and the transfer funding it would
# already be on chain.
from conjectures_subnet.signing import canonical_request_digest, read_digest  # noqa: E402


def digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


def load_keypair(args):
    if args.uri:
        return Keypair.create_from_uri(args.uri)
    if not (args.wallet and args.hotkey):
        raise SystemExit("provide --wallet and --hotkey, or --uri for a development key")
    wallet = Wallet(name=args.wallet, hotkey=args.hotkey, path=args.wallet_path)
    return wallet.hotkey


def proof_from_bundle(path: Path) -> bytes:
    """Read `Main.lean` out of the bundle so the digest we sign is the archived proof."""
    with zipfile.ZipFile(path) as archive:
        return archive.read("Main.lean")


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
    message = bytes.fromhex(
        read_digest(
            hotkey=keypair.ss58_address, submission_id=submission_id
        ).removeprefix("sha256:")
    )
    return {
        "X-Conjectures-Hotkey": keypair.ss58_address,
        "X-Conjectures-Timestamp": timestamp_ms(),
        "X-Conjectures-Signature": keypair.sign(message).hex(),
    }


def submit(args, keypair) -> int:
    bundle = Path(args.bundle).read_bytes()
    proof_sha256 = digest(proof_from_bundle(Path(args.bundle)))
    key = args.idempotency_key or str(uuid.uuid4())
    request_digest = canonical_request_digest(
        hotkey=keypair.ss58_address,
        task_id=args.task_id,
        task_bundle_sha256=args.task_sha256,
        proof_sha256=proof_sha256,
        payment_reference=args.payment_ref,
        idempotency_key=key,
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
    parser.add_argument("--task-id")
    parser.add_argument("--task-sha256", help="the published task_bundle_sha256")
    parser.add_argument("--payment-ref", help="finalized extrinsic reference for your 0.5 TAO")
    parser.add_argument(
        "--idempotency-key",
        help="reuse a previous key to retry the same submission safely (a UUID)",
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
    if missing:
        raise SystemExit(
            "submitting needs --" + ", --".join(name.replace("_", "-") for name in missing)
        )
    return submit(args, keypair)


if __name__ == "__main__":
    raise SystemExit(main())
