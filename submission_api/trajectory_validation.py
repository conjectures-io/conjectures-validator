"""Run untrusted archive parsing outside the API process with enforceable budgets."""
import asyncio
import datetime as dt
import json
import os
from pathlib import Path
import signal
import sys
from dataclasses import dataclass

import anyio

from submission_api.errors import BadRequest
from submission_api.trajectory_schema import Manifest, PackageSummary
from submission_api.trajectory_storage import FilePackage

MAX_REPLY_BYTES = 64 * 1024
RESOURCE_MESSAGE = "package exceeds validation resource limits"


@dataclass(frozen=True)
class ValidationLimits:
    wall_seconds: float = 600
    cpu_seconds: int = 300
    memory_bytes: int = 1536 * 1024 * 1024
    index_bytes: int = 256 * 1024 * 1024


def invalid(errors=None):
    return BadRequest("Invalid chat-log package; check the indicated files and lines against the instructions.",
                      reason_code="TRAJECTORY_INVALID", extra={"validation_errors": errors or [
                          {"path": [], "type": "value_error", "message": RESOURCE_MESSAGE}]})


async def run_validator(package, limits):
    package.file.flush()
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "submission_api.trajectory_validation_worker",
        str(package.file.fileno()), str(limits.cpu_seconds), str(limits.memory_bytes), str(limits.index_bytes),
        pass_fds=(package.file.fileno(),), start_new_session=True,
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        limit=MAX_REPLY_BYTES + 1,
        env={"PYTHONPATH": str(Path(__file__).resolve().parents[1]),
             "TMPDIR": os.environ.get("TMPDIR", "/tmp"), "LANG": "C.UTF-8"},
    )
    try:
        with anyio.fail_after(limits.wall_seconds):
            reply = await process.stdout.readline()
            await process.wait()
        if process.returncode != 0 or not reply or len(reply) > MAX_REPLY_BYTES:
            raise invalid()
        result = json.loads(reply)
        if "error" in result:
            raise invalid(result["error"])
        return result
    except (TimeoutError, ValueError):
        raise invalid() from None
    finally:
        # A cancelled request must not leave its decompressor consuming resources.
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        with anyio.CancelScope(shield=True):
            await process.wait()


async def prepare_upload_isolated(raw, consent, expected_state_version=None, *, limits=None):
    package = raw if isinstance(raw, FilePackage) else FilePackage(size_hint=len(raw))
    try:
        if package is not raw:
            await anyio.to_thread.run_sync(package.write, raw)
        result = await run_validator(package, limits or ValidationLimits())
        summary = PackageSummary(
            manifest=Manifest.model_validate(result["manifest"]), fingerprint=bytes.fromhex(result["fingerprint"]),
            proof_first_seen=dt.datetime.fromisoformat(result["proof_first_seen"]),
            events=result["events"], artifacts=result["artifacts"],
        )
        digest = bytes.fromhex(result["digest"])
        return summary, raw, digest, summary.fingerprint, {
            **consent, "source_file_sha256": "sha256:" + digest.hex(),
        }, expected_state_version
    finally:
        if package is not raw:
            package.close()
