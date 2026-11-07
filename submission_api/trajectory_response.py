"""Keep long upload operations alive through proxies without buffering private ZIP data."""
import asyncio
import json
import logging

import anyio
from fastapi.encoders import jsonable_encoder
from starlette.responses import StreamingResponse

from submission_api.errors import ApiError, note_reason, unhandled_error_handler

HEARTBEAT_SECONDS = 10


async def upload_response(request, keepalive, operation):
    if not keepalive:
        return await operation()

    async def body():
        task = asyncio.create_task(operation())
        try:
            # Whitespace keeps the heartbeats and outcome in a single JSON document.
            yield b"\n"
            while not task.done():
                await asyncio.wait({task}, timeout=HEARTBEAT_SECONDS)
                if not task.done():
                    yield b"\n"
            try:
                payload = {"result": task.result()}
            except ApiError as error:
                payload = {"error": note_reason(request, error).problem()}
            except Exception as error:
                logging.getLogger(__name__).exception("Chat-log upload processing failed")
                response = await unhandled_error_handler(request, error)
                payload = {"error": json.loads(response.body)}
            yield json.dumps(jsonable_encoder(payload)).encode()
        finally:
            # A disconnect must release the operation's database locks and temporary file.
            task.cancel()
            with anyio.CancelScope(shield=True):
                await asyncio.gather(task, return_exceptions=True)

    return StreamingResponse(body(), media_type="application/json", headers={
        "Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no",
        "Vary": "Authorization, Cookie, Origin",
    })
