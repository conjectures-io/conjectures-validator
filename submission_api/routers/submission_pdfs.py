"""Owner-managed PDFs; public bytes use the same approval gate as the Lean proof."""

import hashlib
import re
import unicodedata
import uuid
from typing import Annotated
from urllib.parse import quote

import anyio
from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select

from conjectures_subnet.db.models import REVIEWER_ROLE, Submission
from conjectures_subnet.db.public import ACCEPTED
from conjectures_subnet.db.submission_pdfs import SubmissionPdf
from submission_api.dependencies import CookieWriterDep, PrincipalDep, SessionDep, require_role
from submission_api.errors import (
    ApiError,
    BadRequest,
    Conflict,
    NotFound,
    PayloadTooLarge,
    TooManyRequests,
)
from submission_api.sessions import Principal

router = APIRouter(prefix="/v1", tags=["submission PDFs"])
Reviewer = Annotated[Principal, Depends(require_role(REVIEWER_ROLE))]
MAX_PDFS = 3
MAX_PDF_BYTES = 20 * 1024 * 1024
PRIVATE_HEADERS = {"Cache-Control": "no-store", "Vary": "Cookie, Authorization"}


def filename_of(value: str) -> str:
    value = unicodedata.normalize("NFC", value).strip()
    if (
        not 5 <= len(value) <= 200
        or not value.lower().endswith(".pdf")
        or any(c in "/\\" or unicodedata.category(c).startswith("C") for c in value)
    ):
        raise BadRequest(
            "Choose a PDF filename of at most 200 characters, without paths or control characters.",
            reason_code="PDF_FILENAME",
        )
    return value


async def owner(session, submission_id, account_id, *, lock=False):
    query = select(Submission.id).where(
        Submission.id == submission_id, Submission.account_id == account_id
    )
    if lock:
        query = query.with_for_update()
    if await session.scalar(query) is None:
        raise NotFound("Submission not found")


async def public_submission(session, submission_id):
    if (
        await session.scalar(select(Submission.id).where(Submission.id == submission_id, *ACCEPTED))
        is None
    ):
        raise NotFound("No PDFs are published for this result")


def view(row):
    return {
        "id": str(row.id),
        "filename": row.filename,
        "byte_length": row.byte_length,
        "sha256": "sha256:" + row.sha256.hex(),
        "created_at": row.created_at,
    }


async def pdf_list(session, submission_id, response, *, public=False):
    response.headers.update(PRIVATE_HEADERS)
    query = select(SubmissionPdf).where(SubmissionPdf.submission_id == submission_id)
    if public:
        query = query.join(Submission, Submission.id == SubmissionPdf.submission_id).where(
            *ACCEPTED
        )
    rows = await session.scalars(query.order_by(SubmissionPdf.created_at, SubmissionPdf.id))
    return {"items": [view(row) for row in rows], "max_files": MAX_PDFS, "max_bytes": MAX_PDF_BYTES}


async def pdf_bytes(session, submission_id, pdf_id, *, download=False, public=False):
    query = select(SubmissionPdf.filename, SubmissionPdf.content).where(
        SubmissionPdf.submission_id == submission_id, SubmissionPdf.id == pdf_id
    )
    if public:
        # Apply approval in the byte query too; a correction must immediately stop new reads.
        query = query.join(Submission, Submission.id == SubmissionPdf.submission_id).where(
            *ACCEPTED
        )
    row = (await session.execute(query)).one_or_none()
    if row is None:
        raise NotFound("PDF not found")
    disposition = "attachment" if download else "inline"
    return Response(
        row.content,
        media_type="application/pdf",
        headers={
            **PRIVATE_HEADERS,
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "Referrer-Policy": "no-referrer",
            "Content-Disposition": f"{disposition}; filename=\"attachment.pdf\"; filename*=UTF-8''{quote(row.filename, safe='')}",
        },
    )


async def read_pdf(request):
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/pdf":
        raise ApiError("Upload a PDF file.", status_code=415, reason_code="PDF_FORMAT")
    if request.headers.get("content-encoding", "identity") != "identity":
        raise BadRequest("Compressed request bodies are not accepted.")
    declared = request.headers.get("content-length")
    if declared is not None:
        if not declared.isdecimal():
            raise BadRequest("Invalid Content-Length")
        if int(declared) > MAX_PDF_BYTES:
            raise PayloadTooLarge("Each PDF must be at most 20 MiB.", reason_code="PDF_SIZE")
    raw = bytearray()
    try:
        with anyio.fail_after(120):
            async for chunk in request.stream():
                if len(raw) + len(chunk) > MAX_PDF_BYTES:
                    raise PayloadTooLarge(
                        "Each PDF must be at most 20 MiB.", reason_code="PDF_SIZE"
                    )
                raw.extend(chunk)
    except TimeoutError:
        raise ApiError(
            "PDF upload timed out; retry the attachment.",
            status_code=408,
            reason_code="PDF_TIMEOUT",
        ) from None
    if declared is not None and len(raw) != int(declared):
        raise BadRequest("Body length does not match Content-Length")
    # Format checks only: we never interpret uploaded PDF instructions on the server.
    if not re.match(rb"%PDF-[12]\.\d[\r\n]", raw[:12]) or not raw.rstrip().endswith(b"%%EOF"):
        raise BadRequest(
            "This file does not have a PDF header and end marker.", reason_code="PDF_FORMAT"
        )
    return bytes(raw)


@router.get("/me/submissions/{submission_id}/pdfs")
async def mine(
    submission_id: uuid.UUID, principal: PrincipalDep, session: SessionDep, response: Response
):
    await owner(session, submission_id, principal.account.id)
    return await pdf_list(session, submission_id, response)


@router.post("/me/submissions/{submission_id}/pdfs")
async def upload(
    submission_id: uuid.UUID,
    request: Request,
    response: Response,
    principal: CookieWriterDep,
    session: SessionDep,
    filename: Annotated[str, Query(max_length=200)],
    publish_on_approval: Annotated[bool, Query()],
):
    if not publish_on_approval:
        raise BadRequest(
            "PDF attachments are public once the submission is approved.",
            reason_code="PDF_PUBLICATION_CONSENT",
        )
    filename = filename_of(filename)
    account_id = principal.account.id
    await owner(session, submission_id, account_id)
    # Release the read transaction before streaming; slow uploads hold no row locks/connections.
    await session.close()
    if not hasattr(request.app.state, "pdf_upload_slots"):
        request.app.state.pdf_upload_slots = anyio.Semaphore(2)
    slots = request.app.state.pdf_upload_slots
    try:
        slots.acquire_nowait()
    except anyio.WouldBlock:
        raise TooManyRequests(
            "PDF uploads are busy; retry shortly.", reason_code="PDF_BUSY"
        ) from None
    try:
        raw = await read_pdf(request)
        digest = hashlib.sha256(raw).digest()
        await owner(session, submission_id, account_id, lock=True)
        rows = list(
            await session.scalars(
                select(SubmissionPdf).where(SubmissionPdf.submission_id == submission_id)
            )
        )
        existing = next((row for row in rows if row.sha256 == digest), None)
        if existing is not None:
            response.headers.update(PRIVATE_HEADERS)
            return view(existing)  # A retry never creates a second attachment.
        if len(rows) >= MAX_PDFS:
            raise Conflict(
                "A submission can have at most three PDFs. Remove one before adding another.",
                reason_code="PDF_LIMIT",
            )
        row = SubmissionPdf(
            id=uuid.uuid4(),
            submission_id=submission_id,
            filename=filename,
            sha256=digest,
            byte_length=len(raw),
            content=raw,
        )
        session.add(row)
        await session.flush()
        result = view(row)
        await session.commit()
        response.status_code = 201
        response.headers.update(PRIVATE_HEADERS)
        return result
    finally:
        slots.release()


@router.delete("/me/submissions/{submission_id}/pdfs/{pdf_id}", status_code=204)
async def remove(
    submission_id: uuid.UUID, pdf_id: uuid.UUID, principal: CookieWriterDep, session: SessionDep
):
    await owner(session, submission_id, principal.account.id, lock=True)
    row = await session.scalar(
        select(SubmissionPdf).where(
            SubmissionPdf.id == pdf_id, SubmissionPdf.submission_id == submission_id
        )
    )
    if row is not None:
        await session.delete(row)
        await session.commit()
    return Response(status_code=204, headers=PRIVATE_HEADERS)


@router.get("/me/submissions/{submission_id}/pdfs/{pdf_id}")
async def mine_bytes(
    submission_id: uuid.UUID,
    pdf_id: uuid.UUID,
    principal: PrincipalDep,
    session: SessionDep,
    download: bool = False,
):
    await owner(session, submission_id, principal.account.id)
    return await pdf_bytes(session, submission_id, pdf_id, download=download)


@router.get("/admin/reviews/{submission_id}/pdfs")
async def review(
    submission_id: uuid.UUID, principal: Reviewer, session: SessionDep, response: Response
):
    return await pdf_list(session, submission_id, response)


@router.get("/admin/reviews/{submission_id}/pdfs/{pdf_id}")
async def review_bytes(
    submission_id: uuid.UUID,
    pdf_id: uuid.UUID,
    principal: Reviewer,
    session: SessionDep,
    download: bool = False,
):
    return await pdf_bytes(session, submission_id, pdf_id, download=download)


@router.get("/results/{submission_id}/pdfs")
async def published(submission_id: uuid.UUID, session: SessionDep, response: Response):
    await public_submission(session, submission_id)
    return await pdf_list(session, submission_id, response, public=True)


@router.get("/results/{submission_id}/pdfs/{pdf_id}")
async def published_bytes(
    submission_id: uuid.UUID, pdf_id: uuid.UUID, session: SessionDep, download: bool = False
):
    return await pdf_bytes(session, submission_id, pdf_id, download=download, public=True)
