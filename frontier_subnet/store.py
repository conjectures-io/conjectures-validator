from __future__ import annotations

import os
import secrets
import sqlite3
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from frontier_subnet.commitments import (
    build_proof_commitment,
    build_proof_reveal,
    verify_proof_commitment,
    verify_proof_reveal,
)
from frontier_subnet.protocol import (
    ProofCommitment,
    ProofReveal,
    TaskReference,
    canonical_model_bytes,
)
from frontier_subnet.task_registry import GoldTaskRegistry
from verifier.hashing import sha256_bytes
from verifier.submission import load_submission
from verifier.task_loader import load_task_bundle


DATABASE_SCHEMA_VERSION = 1


class SubmissionUnavailable(LookupError):
    """The miner has no imported submission for the requested exact task."""


class CommitmentNotFound(LookupError):
    """No matching commitment exists for this miner, task, and round."""


class CommitmentConflict(RuntimeError):
    """An immutable round commitment exists with different timing."""


class StoreCorrupt(RuntimeError):
    """Persistent miner state failed an integrity check."""


@dataclass(frozen=True)
class ImportedSubmission:
    task: TaskReference
    submission_sha256: str
    submission_bytes: int


def _database_file_is_safe(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise StoreCorrupt("cannot inspect the miner database safely") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise StoreCorrupt("miner database must be a regular file, not a symlink")


class SubmissionStore:
    """SQLite-backed immutable submission blobs and round commitments."""

    def __init__(self, path: Path):
        self.path = Path(os.path.abspath(os.fspath(path.expanduser())))
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _database_file_is_safe(self.path)
        with self._connect() as connection:
            self._initialize(connection)
        os.chmod(self.path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        _database_file_is_safe(self.path)
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version not in {0, DATABASE_SCHEMA_VERSION}:
            raise StoreCorrupt(f"unsupported miner database schema version: {version}")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS blobs (
                submission_sha256 TEXT PRIMARY KEY,
                source BLOB NOT NULL
            );

            CREATE TABLE IF NOT EXISTS submissions (
                task_id TEXT NOT NULL,
                task_bundle_sha256 TEXT NOT NULL,
                submission_sha256 TEXT NOT NULL REFERENCES blobs(submission_sha256),
                loaded_at_ns INTEGER NOT NULL,
                PRIMARY KEY (task_id, task_bundle_sha256)
            );

            CREATE TABLE IF NOT EXISTS commitments (
                genesis_hash TEXT NOT NULL,
                netuid INTEGER NOT NULL,
                round_start_block INTEGER NOT NULL,
                task_id TEXT NOT NULL,
                task_bundle_sha256 TEXT NOT NULL,
                miner_hotkey TEXT NOT NULL,
                commitment_sha256 TEXT NOT NULL UNIQUE,
                submission_sha256 TEXT NOT NULL REFERENCES blobs(submission_sha256),
                salt BLOB NOT NULL,
                reveal_after_block INTEGER NOT NULL,
                expires_at_block INTEGER NOT NULL,
                commitment_json BLOB NOT NULL,
                reveal_json BLOB NOT NULL,
                PRIMARY KEY (
                    genesis_hash,
                    netuid,
                    round_start_block,
                    task_id,
                    task_bundle_sha256,
                    miner_hotkey
                )
            );
            """
        )
        connection.execute(f"PRAGMA user_version = {DATABASE_SCHEMA_VERSION}")
        connection.commit()

    def import_submission(
        self,
        *,
        task: TaskReference,
        submission: bytes,
        loaded_at_ns: int | None = None,
    ) -> ImportedSubmission:
        submission_sha256 = sha256_bytes(submission)
        timestamp = time.time_ns() if loaded_at_ns is None else loaded_at_ns
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO blobs(submission_sha256, source) VALUES (?, ?)",
                (submission_sha256, submission),
            )
            stored = connection.execute(
                "SELECT source FROM blobs WHERE submission_sha256 = ?",
                (submission_sha256,),
            ).fetchone()
            if stored is None or bytes(stored["source"]) != submission:
                raise StoreCorrupt("content-addressed submission blob mismatch")
            connection.execute(
                """
                INSERT INTO submissions(
                    task_id, task_bundle_sha256, submission_sha256, loaded_at_ns
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(task_id, task_bundle_sha256) DO UPDATE SET
                    submission_sha256 = excluded.submission_sha256,
                    loaded_at_ns = excluded.loaded_at_ns
                """,
                (task.task_id, task.task_bundle_sha256, submission_sha256, timestamp),
            )
            connection.commit()
        return ImportedSubmission(
            task=task,
            submission_sha256=submission_sha256,
            submission_bytes=len(submission),
        )

    def import_gold_submission(
        self,
        *,
        task_dir: Path,
        submission_path: Path,
        allowlist_path: Path,
    ) -> ImportedSubmission:
        registry = GoldTaskRegistry.load(allowlist_path)
        bundle = load_task_bundle(task_dir)
        allowed = registry.assert_bundle(bundle)
        submission = load_submission(submission_path, bundle.manifest.max_submission_bytes)
        return self.import_submission(
            task=TaskReference(
                task_id=allowed.task_id,
                task_bundle_sha256=allowed.task_bundle_sha256,
            ),
            submission=submission.raw,
        )

    def create_commitment(
        self,
        *,
        genesis_hash: str,
        netuid: int,
        round_start_block: int,
        reveal_after_block: int,
        expires_at_block: int,
        task: TaskReference,
        wallet: Any,
        salt_factory: Callable[[int], bytes] = secrets.token_bytes,
    ) -> ProofCommitment:
        signer = bt_signer_hotkey(wallet)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT commitment_json, reveal_json
                FROM commitments
                WHERE genesis_hash = ?
                  AND netuid = ?
                  AND round_start_block = ?
                  AND task_id = ?
                  AND task_bundle_sha256 = ?
                  AND miner_hotkey = ?
                """,
                (
                    genesis_hash,
                    netuid,
                    round_start_block,
                    task.task_id,
                    task.task_bundle_sha256,
                    signer,
                ),
            ).fetchone()
            if existing is not None:
                connection.rollback()
                commitment = ProofCommitment.model_validate_json(
                    bytes(existing["commitment_json"])
                )
                reveal = ProofReveal.model_validate_json(bytes(existing["reveal_json"]))
                if (
                    commitment.genesis_hash != genesis_hash
                    or commitment.netuid != netuid
                    or commitment.round_start_block != round_start_block
                    or commitment.task != task
                    or commitment.miner_hotkey != signer
                ):
                    raise StoreCorrupt(
                        "stored proof commitment does not match its database identity"
                    )
                if (
                    commitment.reveal_after_block != reveal_after_block
                    or commitment.expires_at_block != expires_at_block
                ):
                    raise CommitmentConflict(
                        "the active round already has different immutable timing"
                    )
                if (
                    not verify_proof_commitment(commitment)
                    or not verify_proof_reveal(reveal)
                    or reveal.commitment != commitment
                ):
                    raise StoreCorrupt("stored proof envelope failed its integrity check")
                return commitment

            selected = connection.execute(
                """
                SELECT s.submission_sha256, b.source
                FROM submissions AS s
                JOIN blobs AS b USING (submission_sha256)
                WHERE s.task_id = ? AND s.task_bundle_sha256 = ?
                """,
                (task.task_id, task.task_bundle_sha256),
            ).fetchone()
            if selected is None:
                connection.rollback()
                raise SubmissionUnavailable("no submission is available for this exact task")
            submission = bytes(selected["source"])
            submission_sha256 = str(selected["submission_sha256"])
            if sha256_bytes(submission) != submission_sha256:
                connection.rollback()
                raise StoreCorrupt("stored submission blob failed its SHA-256 check")

            salt = salt_factory(32)
            if not isinstance(salt, bytes) or len(salt) != 32:
                connection.rollback()
                raise ValueError("salt factory must return exactly 32 bytes")
            commitment = build_proof_commitment(
                genesis_hash=genesis_hash,
                netuid=netuid,
                round_start_block=round_start_block,
                reveal_after_block=reveal_after_block,
                expires_at_block=expires_at_block,
                task=task,
                submission_sha256=submission_sha256,
                salt=salt,
                wallet=wallet,
            )
            reveal = build_proof_reveal(
                commitment=commitment,
                submission=submission,
                salt=salt,
                wallet=wallet,
            )
            connection.execute(
                """
                INSERT INTO commitments(
                    genesis_hash,
                    netuid,
                    round_start_block,
                    task_id,
                    task_bundle_sha256,
                    miner_hotkey,
                    commitment_sha256,
                    submission_sha256,
                    salt,
                    reveal_after_block,
                    expires_at_block,
                    commitment_json,
                    reveal_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    genesis_hash,
                    netuid,
                    round_start_block,
                    task.task_id,
                    task.task_bundle_sha256,
                    commitment.miner_hotkey,
                    commitment.commitment_sha256,
                    submission_sha256,
                    salt,
                    reveal_after_block,
                    expires_at_block,
                    canonical_model_bytes(commitment),
                    canonical_model_bytes(reveal),
                ),
            )
            connection.commit()
            return commitment

    def reveal(
        self,
        *,
        genesis_hash: str,
        netuid: int,
        round_start_block: int,
        task: TaskReference,
        miner_hotkey: str,
        commitment_sha256: str,
    ) -> ProofReveal:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT reveal_json
                FROM commitments
                WHERE genesis_hash = ?
                  AND netuid = ?
                  AND round_start_block = ?
                  AND task_id = ?
                  AND task_bundle_sha256 = ?
                  AND miner_hotkey = ?
                  AND commitment_sha256 = ?
                """,
                (
                    genesis_hash,
                    netuid,
                    round_start_block,
                    task.task_id,
                    task.task_bundle_sha256,
                    miner_hotkey,
                    commitment_sha256,
                ),
            ).fetchone()
        if row is None:
            raise CommitmentNotFound("proof commitment was not found")
        reveal = ProofReveal.model_validate_json(bytes(row["reveal_json"]))
        commitment = reveal.commitment
        if (
            commitment.genesis_hash != genesis_hash
            or commitment.netuid != netuid
            or commitment.round_start_block != round_start_block
            or commitment.task != task
            or commitment.miner_hotkey != miner_hotkey
            or commitment.commitment_sha256 != commitment_sha256
            or not verify_proof_reveal(reveal)
        ):
            raise StoreCorrupt("stored proof reveal failed its integrity check")
        if sha256_bytes(reveal.submission_bytes()) != reveal.submission_sha256:
            raise StoreCorrupt("stored proof reveal failed its SHA-256 check")
        return reveal


def bt_signer_hotkey(wallet: Any) -> str:
    """Resolve without exposing wallet paths or key material in store errors."""

    import bittensor as bt

    return bt.resolve_signer(wallet, role="hotkey").ss58_address
