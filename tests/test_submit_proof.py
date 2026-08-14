from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


TASK_DIGEST = "sha256:" + "ab" * 32
PROOF_DIGEST = "sha256:" + "cd" * 32
HOTKEY = "5" * 48


def load_client():
    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "submit_proof_reference_client", root / "scripts" / "submit_proof.py"
    )
    assert spec and spec.loader
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)
    return client


def arguments(tmp_path):
    return SimpleNamespace(
        api="https://validator.test",
        bundle=str(tmp_path / "submission.zip"),
        task=tmp_path / "task",
        task_id="fc-test-formalized-v1",
        task_sha256=TASK_DIGEST,
        payment_ref="123-4",
        idempotency_key="1b94787a-9dc3-4aa9-9574-aeb923ef07c8",
        skip_local_verification=False,
        allow_insecure_local_verification=False,
        credit_name=None,
        credit_url=None,
        credit_orcid=None,
    )


def keypair():
    return SimpleNamespace(ss58_address=HOTKEY, sign=lambda message: b"signature")


def test_submit_does_not_open_the_network_when_local_lean_rejects(
    monkeypatch, tmp_path, capsys
):
    client = load_client()
    parsed = SimpleNamespace(proof=SimpleNamespace(sha256=PROOF_DIGEST))
    monkeypatch.setattr(client, "read_bundle_file", lambda path: b"exact bundle")
    monkeypatch.setattr(client, "load_proof_bundle", lambda raw: parsed)
    monkeypatch.setattr(client, "preflight", lambda *args: None)
    monkeypatch.setattr(
        client,
        "call",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("network request must not be opened")
        ),
    )

    assert client.submit(arguments(tmp_path), keypair()) == 1
    assert "HTTP" not in capsys.readouterr().out


def test_submit_signs_and_sends_the_same_bundle_that_passed_full_preflight(
    monkeypatch, tmp_path
):
    client = load_client()
    events = []
    parsed = SimpleNamespace(proof=SimpleNamespace(sha256=PROOF_DIGEST))
    checked = SimpleNamespace(bundle=parsed)
    monkeypatch.setattr(client, "read_bundle_file", lambda path: b"exact bundle")
    monkeypatch.setattr(client, "load_proof_bundle", lambda raw: parsed)

    def preflight(*args):
        events.append("preflight")
        assert args[2] == b"exact bundle"
        return checked

    def call(url, *, method, headers, data):
        events.append("network")
        assert data == b"exact bundle"
        assert headers["X-Conjectures-Proof-Sha256"] == PROOF_DIGEST
        return 201, {"submission_id": "submission-1"}

    monkeypatch.setattr(client, "preflight", preflight)
    monkeypatch.setattr(client, "call", call)

    assert client.submit(arguments(tmp_path), keypair()) == 0
    assert events == ["preflight", "network"]


def test_reference_client_sends_credit_covered_by_the_server_digest(monkeypatch, tmp_path):
    from conjectures_subnet.attribution import decode_public_credit_header, public_credit
    from conjectures_subnet.db.submissions import canonical_request_digest as server_digest

    client = load_client()
    args = arguments(tmp_path)
    args.credit_name = "Hypatia Research Group"
    args.credit_url = "https://example.org/hypatia"
    args.credit_orcid = "0000-0002-1825-0097"
    parsed = SimpleNamespace(proof=SimpleNamespace(sha256=PROOF_DIGEST))
    monkeypatch.setattr(client, "read_bundle_file", lambda path: b"exact bundle")
    monkeypatch.setattr(client, "load_proof_bundle", lambda raw: parsed)
    monkeypatch.setattr(client, "preflight", lambda *unused: SimpleNamespace(bundle=parsed))

    captured = {}

    def call(url, *, method, headers, data):
        captured.update(headers)
        return 201, {"submission_id": "submission-1"}

    monkeypatch.setattr(client, "call", call)
    assert client.submit(args, keypair()) == 0

    credit = public_credit(args.credit_name, args.credit_url, args.credit_orcid)
    assert credit is not None
    assert decode_public_credit_header(
        captured["X-Conjectures-Public-Credit"]
    ) == credit
    assert client.canonical_request_digest(
        hotkey=HOTKEY,
        task_id=args.task_id,
        task_bundle_sha256=args.task_sha256,
        proof_sha256=PROOF_DIGEST,
        payment_reference=args.payment_ref,
        idempotency_key=args.idempotency_key,
        public_credit=credit,
    ) == server_digest(
        hotkey=HOTKEY,
        task_id=args.task_id,
        task_bundle_sha256=args.task_sha256,
        proof_sha256=PROOF_DIGEST,
        payment_reference=args.payment_ref,
        idempotency_key=args.idempotency_key,
        public_credit=credit,
    )
