"""Bounded single-file multipart intake shared by Lean-kernel competitions."""

from fastapi import Request
from starlette.requests import Request as MultipartRequest
from submission_api.errors import BadRequest, PayloadTooLarge

MAX_FILE_BYTES = 256_000
MAX_BODY_BYTES = 2 * MAX_FILE_BYTES


async def read_kernel(request: Request) -> bytes:
    # Bound the actual stream, including multipart overhead, before the parser spools files.
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            raise PayloadTooLarge("Kernel upload body is too large")
        body.extend(chunk)

    async def receive():
        return {"type": "http.request", "body": bytes(body), "more_body": False}

    parsed = MultipartRequest(request.scope, receive=receive)
    try:
        async with parsed.form(max_files=1, max_fields=0) as form:
            if len(form.multi_items()) != 1 or set(form) != {"Kernel.lean"}:
                raise BadRequest("Expected exactly one file, Kernel.lean")
            part = form["Kernel.lean"]
            if not hasattr(part, "read"):
                raise BadRequest("Kernel.lean must be a file")
            kernel = await part.read(MAX_FILE_BYTES + 1)
    except (BadRequest, PayloadTooLarge):
        raise
    except Exception as exc:  # noqa: BLE001 - malformed multipart input
        raise BadRequest("Expected exactly one file, Kernel.lean") from exc
    if len(kernel) > MAX_FILE_BYTES:
        raise PayloadTooLarge(f"Kernel.lean must be at most {MAX_FILE_BYTES} bytes")
    if not kernel:
        raise BadRequest("Kernel.lean must be non-empty")
    try:
        kernel.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BadRequest("Kernel.lean must be UTF-8 text") from exc
    return kernel
