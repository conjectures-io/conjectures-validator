"""Bounded single-file multipart intake shared by kernel competitions.

The one file is `Kernel.lean` unless the caller names another: Exact integer kernels' is `entry.c`.
"""

from fastapi import Request
from starlette.requests import Request as MultipartRequest
from submission_api.errors import BadRequest, PayloadTooLarge

MAX_FILE_BYTES = 256_000


async def read_kernel(
    request: Request, *, max_file_bytes: int = MAX_FILE_BYTES, name: str = "Kernel.lean"
) -> bytes:
    # Bound the actual stream, including multipart overhead, before the parser spools files.
    if max_file_bytes <= 0:
        raise ValueError("kernel size limit must be positive")
    max_body_bytes = 2 * max_file_bytes
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_body_bytes:
            raise PayloadTooLarge("Kernel upload body is too large")
        body.extend(chunk)

    async def receive():
        return {"type": "http.request", "body": bytes(body), "more_body": False}

    parsed = MultipartRequest(request.scope, receive=receive)
    try:
        async with parsed.form(max_files=1, max_fields=0) as form:
            if len(form.multi_items()) != 1 or set(form) != {name}:
                raise BadRequest(f"Expected exactly one file, {name}")
            part = form[name]
            if not hasattr(part, "read"):
                raise BadRequest(f"{name} must be a file")
            kernel = await part.read(max_file_bytes + 1)
    except (BadRequest, PayloadTooLarge):
        raise
    except Exception as exc:  # noqa: BLE001 - malformed multipart input
        raise BadRequest(f"Expected exactly one file, {name}") from exc
    if len(kernel) > max_file_bytes:
        raise PayloadTooLarge(f"{name} must be at most {max_file_bytes} bytes")
    if not kernel:
        raise BadRequest(f"{name} must be non-empty")
    try:
        kernel.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BadRequest(f"{name} must be UTF-8 text") from exc
    return kernel
