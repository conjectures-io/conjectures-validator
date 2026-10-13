"""PDF access follows proof approval; attachments never alter or charge for the proof."""

import asyncio
import uuid

import pytest
from sqlalchemy import func, select

from conftest_api import distinct_bundle, harness, postgres_dsn
from test_api_accounts import client, same_origin, sign_in_by_email
from test_api_reviews import _grant_reviewer, _verify
from test_api_session_submissions import SESSION, _digest, keyless_account, params, zip_headers
from conjectures_subnet.db import submissions as store
from conjectures_subnet.db.models import ReviewOutcome, Submission
from conjectures_subnet.db.submission_pdfs import SubmissionPdf
from submission_api.routers import submission_pdfs as pdfs

pytestmark = pytest.mark.skipif(
    postgres_dsn() is None, reason="requires a verified private PostgreSQL fixture"
)


def document(marker="paper"):
    """A real one-page PDF, with xref offsets generated from the fixture's bytes."""
    data = bytearray(f"%PDF-1.4\n% {marker}\n".encode())
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
    ]
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(b"xref\n0 4\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(data)


async def submitted(kit, http):
    await keyless_account(kit, http)
    bundle, _ = distinct_bundle("supporting-pdf-fixture", coldkey=None)
    response = await http.post(
        SESSION,
        params=params(bundle_sha256=_digest(bundle)),
        content=bundle,
        headers=zip_headers(http),
    )
    assert response.status_code == 201, response.text
    return response.json()["submission"]


def upload_args(raw=None, filename="paper.pdf"):
    return {
        "content": document() if raw is None else raw,
        "params": {"filename": filename, "publish_on_approval": "true"},
        "headers": {**same_origin(), "content-type": "application/pdf"},
    }


def test_pdf_ownership_reviewer_visibility_publication_and_correction():
    async def scenario():
        kit = await harness().setup()
        try:
            async with (
                await client(kit) as author,
                await client(kit) as reviewer,
                await client(kit) as outsider,
            ):
                original = await submitted(kit, author)
                sid = original["id"]
                mine = f"/v1/me/submissions/{sid}/pdfs"
                public = f"/v1/results/{sid}/pdfs"
                review = f"/v1/admin/reviews/{sid}/pdfs"
                assert (await outsider.post(mine, **upload_args())).status_code == 401
                stranger = await sign_in_by_email(kit, outsider, email="pdf-stranger@example.com")
                assert stranger["id"]
                assert (await outsider.post(mine, **upload_args())).status_code == 404
                assert (
                    await author.post(
                        mine, **{**upload_args(), "headers": {"content-type": "application/pdf"}}
                    )
                ).status_code == 403
                uploaded = await author.post(mine, **upload_args(filename="Explication é.pdf"))
                assert uploaded.status_code == 201, uploaded.text
                pid = uploaded.json()["id"]
                assert uploaded.json()["byte_length"] == len(document())
                assert (await author.post(mine, **upload_args())).json()["id"] == pid
                assert (await outsider.get(f"{mine}/{pid}")).status_code == 404
                assert (await author.get(review)).status_code == 403
                assert (await outsider.get(public)).status_code == 404
                assert (await outsider.get(f"{public}/{pid}")).status_code == 404
                person = await sign_in_by_email(kit, reviewer, email="pdf-reviewer@example.com")
                await _grant_reviewer(kit, person["id"])
                assert (await reviewer.get(review)).json()["items"][0]["id"] == pid
                assert (await reviewer.get(f"{review}/{pid}")).content == document()
                await _verify(kit, sid)
                assert (await outsider.get(f"{public}/{pid}")).status_code == 404
                async with kit.session() as session:
                    decision = await store.record_human_decision(
                        session,
                        uuid.UUID(sid),
                        decision=ReviewOutcome.APPROVED,
                        reason_code="REVIEW_APPROVED",
                        reviewer="fixture-reviewer",
                    )
                    decision_id = decision.id
                    await session.commit()
                downloaded = await outsider.get(f"{public}/{pid}")
                assert downloaded.status_code == 200 and downloaded.content == document()
                assert downloaded.headers["cache-control"] == "no-store"
                assert downloaded.headers["content-type"] == "application/pdf"
                assert downloaded.headers["content-disposition"].startswith("inline;")
                assert "sandbox" in downloaded.headers["content-security-policy"]
                assert "%C3%A9" in downloaded.headers["content-disposition"]
                assert (
                    (await outsider.get(f"{public}/{pid}?download=true"))
                    .headers["content-disposition"]
                    .startswith("attachment;")
                )
                assert "content" not in (await outsider.get(public)).json()["items"][0]
                async with kit.session() as session:
                    row = await session.get(Submission, uuid.UUID(sid))
                    assert "sha256:" + row.proof_digest.hex() == original["proof_sha256"]
                    await store.correct_human_decision(
                        session,
                        uuid.UUID(sid),
                        supersedes_id=decision_id,
                        decision=ReviewOutcome.REJECTED,
                        reason_code="REVIEW_REJECTED",
                        reviewer="fixture-reviewer",
                        notes="Fixture correction",
                        notes_public="Fixture correction",
                    )
                    await session.commit()
                assert (await outsider.get(public)).status_code == 404
                assert (await outsider.get(f"{public}/{pid}")).status_code == 404
                assert (await author.get(f"{mine}/{pid}")).status_code == 200
                assert (
                    await outsider.delete(f"{mine}/{pid}", headers=same_origin())
                ).status_code == 404
                assert (
                    await author.delete(f"{mine}/{pid}", headers=same_origin())
                ).status_code == 204
                assert (
                    await author.delete(f"{mine}/{pid}", headers=same_origin())
                ).status_code == 204
                assert (await author.get(f"{mine}/{pid}")).status_code == 404
        finally:
            await kit.teardown()

    asyncio.run(scenario())


def test_invalid_pdf_size_consent_and_atomic_attachment_limit(monkeypatch):
    async def scenario():
        kit = await harness().setup()
        try:
            async with await client(kit) as author:
                sid = (await submitted(kit, author))["id"]
                path = f"/v1/me/submissions/{sid}/pdfs"
                for filename in ("../paper.pdf", "paper.html", "bad\r\nname.pdf"):
                    assert (
                        await author.post(path, **upload_args(filename=filename))
                    ).status_code == 400
                assert (
                    await author.post(path, **upload_args(b"<html>not a PDF</html>"))
                ).status_code == 400
                wrong = upload_args()
                wrong["headers"]["content-type"] = "text/html"
                assert (await author.post(path, **wrong)).status_code == 415
                refused = upload_args()
                refused["params"]["publish_on_approval"] = "false"
                assert (await author.post(path, **refused)).status_code == 400
                monkeypatch.setattr(pdfs, "MAX_PDF_BYTES", 128)
                assert (await author.post(path, **upload_args())).status_code == 413

                async def chunks():
                    yield b"%PDF-1.4\n"
                    yield b"x" * 128

                assert (await author.post(path, **upload_args(chunks()))).status_code == 413
                monkeypatch.setattr(pdfs, "MAX_PDF_BYTES", 20 * 1024 * 1024)
                for marker in ("one", "two"):
                    assert (
                        await author.post(path, **upload_args(document(marker)))
                    ).status_code == 201
                results = await asyncio.gather(
                    *[
                        author.post(path, **upload_args(document(marker)))
                        for marker in ("three", "four")
                    ]
                )
                assert sorted(result.status_code for result in results) == [201, 409]
                assert (await author.post(path, **upload_args(document("one")))).status_code == 200
                async with kit.session() as session:
                    assert (
                        await session.scalar(select(func.count()).select_from(SubmissionPdf)) == 3
                    )
        finally:
            await kit.teardown()

    asyncio.run(scenario())
