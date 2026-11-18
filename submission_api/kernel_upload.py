"""Bounded single-file multipart intake shared by Lean-kernel competitions."""

from fastapi import Request
from starlette.requests import Request as MultipartRequest
from submission_api.errors import BadRequest, PayloadTooLarge, UnprocessableEntity

MAX_FILE_BYTES = 256_000


async def read_kernel(
    request: Request,
    *,
    max_file_bytes: int = MAX_FILE_BYTES,
    filename: str = "Kernel.lean",
    typed_errors: bool = False,
) -> bytes:
    # Bound the actual stream, including multipart overhead, before the parser spools files.
    if max_file_bytes <= 0:
        raise ValueError("kernel size limit must be positive")
    max_body_bytes = 2 * max_file_bytes
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_body_bytes:
            raise PayloadTooLarge(
                "Lean upload body is too large",
                reason_code="SUBMISSION_TOO_LARGE" if typed_errors else "BUNDLE_TOO_LARGE",
            )
        body.extend(chunk)

    async def receive():
        return {"type": "http.request", "body": bytes(body), "more_body": False}

    parsed = MultipartRequest(request.scope, receive=receive)
    try:
        async with parsed.form(max_files=1, max_fields=0, max_part_size=max_file_bytes) as form:
            if len(form.multi_items()) != 1 or set(form) != {filename}:
                raise BadRequest(f"Expected exactly one file, {filename}")
            part = form[filename]
            if not hasattr(part, "read"):
                raise BadRequest(f"{filename} must be a file")
            kernel = await part.read(max_file_bytes + 1)
    except (BadRequest, PayloadTooLarge):
        raise
    except Exception as exc:  # noqa: BLE001 - malformed multipart input
        raise BadRequest(f"Expected exactly one file, {filename}") from exc
    if len(kernel) > max_file_bytes:
        raise PayloadTooLarge(
            f"{filename} must be at most {max_file_bytes} bytes",
            reason_code="SUBMISSION_TOO_LARGE" if typed_errors else "BUNDLE_TOO_LARGE",
        )
    if not kernel:
        if typed_errors:
            raise UnprocessableEntity("Source must be nonempty", reason_code="SUBMISSION_EMPTY")
        raise BadRequest(f"{filename} must be non-empty")
    try:
        kernel.decode("utf-8")
    except UnicodeDecodeError as exc:
        if typed_errors:
            raise UnprocessableEntity(
                "Source must be UTF-8", reason_code="SUBMISSION_NOT_UTF8"
            ) from exc
        raise BadRequest(f"{filename} must be UTF-8 text") from exc
    return kernel
