"""Optional author-supplied papers, kept separate from the immutable proof bundle."""

import datetime as dt
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from conjectures_subnet.db.models import Base, SHA256


class SubmissionPdf(Base):
    __tablename__ = "submission_pdfs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    submission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("submissions.id"), nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[bytes] = mapped_column(SHA256, nullable=False)
    byte_length: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, deferred=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("submission_id", "sha256", name="submission_pdf_content_once"),
        CheckConstraint(
            "length(filename) BETWEEN 5 AND 200", name="submission_pdf_filename_length"
        ),
        CheckConstraint(
            "byte_length BETWEEN 1 AND 20971520 AND byte_length = octet_length(content)",
            name="submission_pdf_size",
        ),
    )
