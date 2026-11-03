"""Bounded multipart parsing without a database or production service."""

import asyncio

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from conftest_api import build_settings
from submission_api.errors import ApiError, api_error_handler
from submission_api.kernel_upload import MAX_FILE_BYTES, read_kernel
from submission_api.settings import SettingsError


def upload(kernel=None, *, limit=1024 * 1024, raw_body=None, legacy=False):
    app = FastAPI()
    app.add_exception_handler(ApiError, api_error_handler)

    @app.post("/upload")
    async def receive(request: Request):
        value = (
            await read_kernel(request)
            if legacy
            else await read_kernel(request, max_file_bytes=limit)
        )
        return {"bytes": len(value)}

    async def run():
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            if raw_body is not None:

                async def chunks():
                    for start in range(0, len(raw_body), 65536):
                        yield raw_body[start : start + 65536]

                return await client.post(
                    "/upload",
                    content=chunks(),
                    headers={"Content-Type": "multipart/form-data; boundary=test"},
                )
            return await client.post("/upload", files={"Kernel.lean": ("Kernel.lean", kernel)})

    return asyncio.run(run())


@pytest.mark.parametrize("limit", [1024 * 1024, 2 * 1024 * 1024, 4 * 1024 * 1024])
def test_configured_boundary_and_multipart_overhead(limit):
    # Exactly the allowed file size is accepted even with multipart headers/framing.
    response = upload(b"x" * limit, limit=limit)
    assert response.status_code == 200, response.text
    assert response.json() == {"bytes": limit}
    response = upload(b"x" * (limit + 1), limit=limit)
    assert response.status_code == 413
    assert str(limit) in response.json()["detail"]


def test_streamed_body_is_bounded_before_multipart_parsing():
    response = upload(limit=1024, raw_body=b"x" * 2049)
    assert response.status_code == 413
    assert "body" in response.json()["detail"]


def test_limit_counts_utf8_bytes_not_characters():
    response = upload(("λ" * 513).encode(), limit=1024)
    assert response.status_code == 413


def test_legacy_default_is_unchanged():
    assert MAX_FILE_BYTES == 256000
    assert upload(b"x" * MAX_FILE_BYTES, legacy=True).status_code == 200
    assert upload(b"x" * (MAX_FILE_BYTES + 1), legacy=True).status_code == 413


def test_settings_default_and_supported_adjustments():
    assert build_settings().torchlean_max_source_bytes == 1024 * 1024
    for limit in (1, 2 * 1024 * 1024, 4 * 1024 * 1024):
        settings = build_settings(TORCHLEAN_MAX_SOURCE_BYTES=str(limit))
        assert settings.torchlean_max_source_bytes == limit


@pytest.mark.parametrize("value", ["0", "-1", "4194305", "invalid", "1.5"])
def test_invalid_settings_fail_at_startup(value):
    with pytest.raises(SettingsError, match="TORCHLEAN_MAX_SOURCE_BYTES"):
        build_settings(TORCHLEAN_MAX_SOURCE_BYTES=value)
