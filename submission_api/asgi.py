"""ASGI entry point: `uvicorn submission_api.asgi:app`."""

from __future__ import annotations

from submission_api.app import create_app


app = create_app()
