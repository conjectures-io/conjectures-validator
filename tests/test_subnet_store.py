from __future__ import annotations

import os
import stat
from pathlib import Path

import bittensor as bt
import pytest

from frontier_subnet.commitments import verify_proof_commitment, verify_proof_reveal
from frontier_subnet.protocol import TaskReference
from frontier_subnet.store import (
    CommitmentNotFound,
    SubmissionStore,
    SubmissionUnavailable,
)


GENESIS_HASH = "0x" + "34" * 32
TASK = TaskReference(
    task_id="fc-store-test-positive-v1",
    task_bundle_sha256="sha256:" + "bc" * 32,
)
SUBMISSION = b"theorem Bounty.target : True := by\n  trivial\n"
SALT = b"\x5a" * 32


def _miner():
    return bt.sp_core.Keypair.create_from_uri("//Alice")


def _commit(
    store: SubmissionStore,
    *,
    round_start_block: int = 200,
    salt_factory=lambda size: SALT,
):
    return store.create_commitment(
        genesis_hash=GENESIS_HASH,
        netuid=7,
        round_start_block=round_start_block,
        reveal_after_block=round_start_block + 10,
        expires_at_block=round_start_block + 20,
        task=TASK,
        wallet=_miner(),
        salt_factory=salt_factory,
    )


def _reveal(store: SubmissionStore, commitment):
    return store.reveal(
        genesis_hash=GENESIS_HASH,
        netuid=7,
        round_start_block=commitment.round_start_block,
        task=TASK,
        miner_hotkey=commitment.miner_hotkey,
        commitment_sha256=commitment.commitment_sha256,
    )


def test_store_commitment_is_idempotent_and_persists_across_reopen(tmp_path):
    database = tmp_path / "private" / "miner.sqlite3"
    store = SubmissionStore(database)
    imported = store.import_submission(
        task=TASK,
        submission=SUBMISSION,
        loaded_at_ns=123,
    )
    salt_calls = 0

    def first_salt(size: int) -> bytes:
        nonlocal salt_calls
        salt_calls += 1
        assert size == 32
        return SALT

    first = _commit(store, salt_factory=first_salt)

    def must_not_make_another_salt(_: int) -> bytes:
        raise AssertionError("an idempotent retry generated a new salt")

    retry = _commit(store, salt_factory=must_not_make_another_salt)
    assert salt_calls == 1
    assert retry == first
    assert verify_proof_commitment(first)
    assert imported.submission_sha256

    reveal = _reveal(store, first)
    assert reveal.submission_bytes() == SUBMISSION
    assert verify_proof_reveal(reveal)

    reopened = SubmissionStore(database)
    persisted_retry = _commit(
        reopened,
        salt_factory=must_not_make_another_salt,
    )
    persisted_reveal = _reveal(reopened, persisted_retry)
    assert persisted_retry == first
    assert persisted_reveal == reveal
    assert verify_proof_reveal(persisted_reveal)
    assert stat.S_IMODE(database.stat().st_mode) == 0o600


def test_round_locks_immutable_bytes_even_after_a_new_submission_is_imported(tmp_path):
    store = SubmissionStore(tmp_path / "miner.sqlite3")
    store.import_submission(task=TASK, submission=SUBMISSION)
    first_round = _commit(store, round_start_block=200)

    changed = SUBMISSION + b"-- a later local version\n"
    store.import_submission(task=TASK, submission=changed)

    same_round = _commit(
        store,
        round_start_block=200,
        salt_factory=lambda _: b"\xff" * 32,
    )
    next_round = _commit(
        store,
        round_start_block=300,
        salt_factory=lambda _: b"\xee" * 32,
    )

    assert same_round == first_round
    assert _reveal(store, same_round).submission_bytes() == SUBMISSION
    assert _reveal(store, next_round).submission_bytes() == changed


def test_gold_import_survives_source_file_edit_and_delete(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    task_dir = (
        project_root
        / "tasks/gold"
        / "fc-e923379e-balancedprimes-balanced-primes-order-45ebef4685-positive-v1"
    )
    submission_path = tmp_path / "Main.lean"
    original = b"theorem Bounty.target : True := by\n  trivial\n"
    submission_path.write_bytes(original)

    store = SubmissionStore(tmp_path / "miner.sqlite3")
    imported = store.import_gold_submission(
        task_dir=task_dir,
        submission_path=submission_path,
        allowlist_path=project_root / "gold/allowlist.json",
    )

    submission_path.write_bytes(b"-- replaced after import\n")
    submission_path.unlink()
    assert not submission_path.exists()

    commitment = store.create_commitment(
        genesis_hash=GENESIS_HASH,
        netuid=7,
        round_start_block=400,
        reveal_after_block=410,
        expires_at_block=420,
        task=imported.task,
        wallet=_miner(),
        salt_factory=lambda _: SALT,
    )
    reveal = store.reveal(
        genesis_hash=GENESIS_HASH,
        netuid=7,
        round_start_block=400,
        task=imported.task,
        miner_hotkey=commitment.miner_hotkey,
        commitment_sha256=commitment.commitment_sha256,
    )
    assert reveal.submission_bytes() == original
    assert verify_proof_reveal(reveal)


def test_store_uses_exact_task_and_commitment_identity(tmp_path):
    store = SubmissionStore(tmp_path / "miner.sqlite3")
    store.import_submission(task=TASK, submission=SUBMISSION)
    wrong_task = TaskReference(
        task_id=TASK.task_id,
        task_bundle_sha256="sha256:" + "cd" * 32,
    )

    with pytest.raises(SubmissionUnavailable):
        store.create_commitment(
            genesis_hash=GENESIS_HASH,
            netuid=7,
            round_start_block=200,
            reveal_after_block=210,
            expires_at_block=220,
            task=wrong_task,
            wallet=_miner(),
            salt_factory=lambda _: SALT,
        )

    commitment = _commit(store)
    with pytest.raises(CommitmentNotFound):
        store.reveal(
            genesis_hash=GENESIS_HASH,
            netuid=7,
            round_start_block=commitment.round_start_block,
            task=TASK,
            miner_hotkey=commitment.miner_hotkey,
            commitment_sha256="sha256:" + "ef" * 32,
        )


def test_database_path_cannot_be_a_symlink(tmp_path):
    target = tmp_path / "real.sqlite3"
    SubmissionStore(target)
    link = tmp_path / "linked.sqlite3"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("symlinks unavailable")

    with pytest.raises(RuntimeError, match="regular file"):
        SubmissionStore(link)


def test_database_path_cannot_be_a_dangling_symlink(tmp_path):
    link = tmp_path / "dangling.sqlite3"
    try:
        os.symlink(tmp_path / "missing.sqlite3", link)
    except OSError:
        pytest.skip("symlinks unavailable")

    with pytest.raises(RuntimeError, match="regular file"):
        SubmissionStore(link)
