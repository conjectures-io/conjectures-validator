import asyncio
import json
import socket

from starlette.requests import Request

from submission_api import trajectory_response as streaming
from submission_api.errors import Conflict


def test_heartbeats_arrive_before_completion_and_disconnect_cancels_work(monkeypatch):
    monkeypatch.setattr(streaming, "HEARTBEAT_SECONDS", 0.01)

    async def scenario():
        release, cancelled = asyncio.Event(), asyncio.Event()

        async def process():
            try:
                await release.wait()
                return {"id": "claim"}
            finally:
                cancelled.set()

        request = Request({"type": "http"})
        response = await streaming.upload_response(request, True, process)
        body = response.body_iterator
        assert await anext(body) == b"\n"
        assert await anext(body) == b"\n"
        assert not cancelled.is_set()
        release.set()
        assert json.loads(await anext(body)) == {"result": {"id": "claim"}}
        await body.aclose()

        release.clear()
        cancelled.clear()
        response = await streaming.upload_response(request, True, process)
        body = response.body_iterator
        await anext(body)
        await anext(body)
        await body.aclose()
        assert cancelled.is_set()
    asyncio.run(scenario())


def test_stream_reports_safe_problem_and_records_reason():
    async def scenario():
        async def process():
            raise Conflict("Upload incomplete.")

        request = Request({"type": "http"})
        response = await streaming.upload_response(request, True, process)
        body = b"".join([chunk async for chunk in response.body_iterator])
        problem = json.loads(body)["error"]
        assert problem["status"] == 409
        assert problem["reason_code"] == "CONFLICT"
        assert request.scope["conjectures.reason_code"] == "CONFLICT"
    asyncio.run(scenario())


def test_asgi_disconnect_waits_for_operation_cleanup(monkeypatch):
    monkeypatch.setattr(streaming, "HEARTBEAT_SECONDS", 0.01)

    async def scenario():
        disconnected, started, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def process():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                cleaned.set()

        async def receive():
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.body" and started.is_set():
                disconnected.set()

        request = Request({"type": "http", "asgi": {"spec_version": "2.0"}})
        response = await streaming.upload_response(request, True, process)
        await asyncio.wait_for(response(request.scope, receive, send), timeout=1)
        assert cleaned.is_set()
    asyncio.run(scenario())


def test_http_client_receives_heartbeats_during_slow_processing(monkeypatch):
    import httpx
    import uvicorn
    from fastapi import FastAPI

    monkeypatch.setattr(streaming, "HEARTBEAT_SECONDS", 0.02)

    async def scenario():
        app = FastAPI()

        @app.post("/upload")
        async def upload(request: Request):
            async def process():
                await asyncio.sleep(0.7)
                return {"id": "claim"}
            return await streaming.upload_response(request, True, process)

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
            task = asyncio.create_task(server.serve(sockets=[listener]))
            try:
                async with asyncio.timeout(5):
                    while not server.started:
                        await asyncio.sleep(0.01)
                async with httpx.AsyncClient(timeout=0.2) as http:
                    response = await http.post(f"http://127.0.0.1:{port}/upload")
                assert response.json() == {"result": {"id": "claim"}}
                assert response.content.count(b"\n") > 1
            finally:
                server.should_exit = True
                await task
    asyncio.run(scenario())
