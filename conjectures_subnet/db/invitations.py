"""Invitation links, and the grants they turn into.

An operator issues a link carrying N verification attempts; a signed-in account redeems it once
and gains N credits. The rules here are the same ones the rest of the credit path lives by, and
they are stated rather than assumed:

* **The code is never stored.** Only its SHA-256, so a link can be listed, counted and revoked
  but never read back. Lookup digests what the caller presented and compares — see
  ``code_digest``. The code is a credential worth real money, and an operator session that is
  taken over should yield the inventory, not the ability to spend it.
* **Redemption is serialised on the invitation row.** ``redeem`` takes ``SELECT ... FOR UPDATE``
  before it reads ``redeemed_count``, in the same transaction as the write that follows. A check
  that reads before it writes is not a check: without the lock, two callers both see the last
  remaining redemption and both take it. This is the argument ``credits.lock_account`` and
  ``submissions.record_human_decision`` already make, applied to a different counter.
* **One transaction, or none of it.** The redemption row, the GRANT ledger entry naming it, and
  the incremented counter are one unit. A grant with no redemption behind it has no auditable
  source, and a redemption with no grant is an attempt someone paid for and did not receive.
* **Attempts, not rao.** An invitation stores a credit count. The conversion happens here, at
  redemption, against the price in force — so repricing between issuing a link and clicking it
  cannot change what the recipient was promised.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from conjectures_subnet.db import credits as credit_store
from conjectures_subnet.db.errors import (
    RecordConflict,
    RecordNotFound,
    violated_constraint,
)
from conjectures_subnet.db.models import (
    Account,
    CreditEntryKind,
    Invitation,
    InvitationRedemption,
)

# 256 bits, matching `sessions.new_token`. The code travels in a URL that is pasted into chat and
# mail, so it is url-safe base64 rather than anything that needs escaping.
CODE_BYTES = 32

# The ceiling an API caller may ask for. The schema allows up to 100 and this is the operational
# limit under it, kept here so the store refuses before the database has to.
MAX_GRANT_CREDITS = 100

REASON_NOT_FOUND = "INVITATION_NOT_FOUND"
REASON_EXPIRED = "INVITATION_EXPIRED"
REASON_REVOKED = "INVITATION_REVOKED"
REASON_EXHAUSTED = "INVITATION_EXHAUSTED"
REASON_ALREADY_REDEEMED = "INVITATION_ALREADY_REDEEMED"
REASON_EMAIL_DOMAIN = "INVITATION_EMAIL_DOMAIN_REQUIRED"


class InvitationUnusable(RecordConflict):
    """The link exists but cannot be redeemed, and says which of the reasons applies.

    Deliberately *not* collapsed into `INVITATION_NOT_FOUND`. An unknown code and an expired one
    are different facts and the holder is entitled to the difference: someone told "expired" asks
    for a new link, while someone told "not found" checks their typing and then writes to us. The
    code is 256 bits, so distinguishing them leaks nothing an attacker can enumerate.
    """


def new_code() -> str:
    """A fresh invitation code. Shown once, at creation, and never recoverable after."""
    return secrets.token_urlsafe(CODE_BYTES)


def code_digest(code: str) -> bytes:
    """The raw 32 bytes stored for a code.

    Plain SHA-256 rather than a password hash, for the reason `accounts.digest` gives: this is a
    256-bit value this service generated, not a user-chosen secret, so there is no dictionary to
    slow down and stretching would only add latency to every redemption.
    """
    return hashlib.sha256(code.encode("utf-8")).digest()


@dataclass(frozen=True)
class InvitationState:
    """What a caller may know about a link before redeeming it.

    Carries no code and no redeemer identities: this backs the public preflight page, which is
    reachable by anyone holding the link and must not become a way to see who else has it.
    """

    id: uuid.UUID
    credits: int
    max_redemptions: int
    redeemed_count: int
    email_domain: str | None
    expires_at: dt.datetime | None
    revoked_at: dt.datetime | None
    note: str

    @property
    def remaining(self) -> int:
        return max(self.max_redemptions - self.redeemed_count, 0)


def _state(row: Invitation) -> InvitationState:
    return InvitationState(
        id=row.id,
        credits=row.credits,
        max_redemptions=row.max_redemptions,
        redeemed_count=row.redeemed_count,
        email_domain=row.email_domain,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
        note=row.note,
    )


def _refuse_if_unusable(row: Invitation, *, now: dt.datetime) -> None:
    """The three reasons a live link stops working, in the order a holder would ask them.

    Revocation first: an operator withdrawing a link is a decision about it, and reporting
    "expired" for a link someone deliberately killed would send the holder back to ask for a
    replacement they should not get.
    """
    if row.revoked_at is not None:
        raise InvitationUnusable(
            "this invitation has been withdrawn", reason_code=REASON_REVOKED
        )
    if row.expires_at is not None and row.expires_at <= now:
        raise InvitationUnusable(
            "this invitation has expired", reason_code=REASON_EXPIRED
        )
    if row.redeemed_count >= row.max_redemptions:
        raise InvitationUnusable(
            "this invitation has been fully redeemed", reason_code=REASON_EXHAUSTED
        )


async def create(
    session: AsyncSession,
    *,
    credits: int,
    note: str,
    created_by: uuid.UUID,
    max_redemptions: int = 1,
    expires_at: dt.datetime | None = None,
    email_domain: str | None = None,
) -> tuple[Invitation, str]:
    """Issue a link, and hand back the one and only copy of its code.

    The code is returned here and nowhere else, ever. Callers must put it in the response to the
    request that created it; there is no second chance and no recovery path but issuing another.
    """
    if not 1 <= credits <= MAX_GRANT_CREDITS:
        raise ValueError(f"credits must be between 1 and {MAX_GRANT_CREDITS}")
    if max_redemptions < 1:
        raise ValueError("max_redemptions must be at least 1")

    code = new_code()
    row = Invitation(
        code_sha256=code_digest(code),
        credits=credits,
        max_redemptions=max_redemptions,
        expires_at=expires_at,
        email_domain=email_domain.strip().lower() if email_domain else None,
        note=note.strip(),
        created_by=created_by,
    )
    session.add(row)
    await session.flush()
    return row, code


async def read(session: AsyncSession, code: str) -> InvitationState:
    """The public preflight: what this link offers, without redeeming it.

    Raises `RecordNotFound` for an unknown code and `InvitationUnusable` for a known one that
    cannot be redeemed, so the page can say which. Writes nothing.
    """
    row = await _by_code(session, code)
    return _state(row)


async def _by_code(session: AsyncSession, code: str, *, lock: bool = False) -> Invitation:
    statement = select(Invitation).where(Invitation.code_sha256 == code_digest(code))
    if lock:
        statement = statement.with_for_update()
    row = (await session.execute(statement)).scalar_one_or_none()
    if row is None:
        raise RecordNotFound("no such invitation", reason_code=REASON_NOT_FOUND)
    return row


@dataclass(frozen=True)
class Redemption:
    """What redeeming produced: the row, the ledger entry, and the resulting balance."""

    redemption_id: uuid.UUID
    invitation_id: uuid.UUID
    credit_ledger_id: int
    credits: int
    amount_rao: int


async def redeem(
    session: AsyncSession,
    *,
    code: str,
    account_id: uuid.UUID,
    credit_price_rao: int,
    now: dt.datetime,
) -> Redemption:
    """Grant this account the link's credits, once.

    The caller commits. Everything below is one transaction by construction: the invitation row
    is locked before its counter is read, and the redemption row, the ledger entry and the
    incremented counter are written together or not at all.
    """
    row = await _by_code(session, code, lock=True)
    _refuse_if_unusable(row, now=now)

    if row.email_domain is not None:
        await _require_email_domain(session, account_id, row.email_domain)

    # After the invitation, always. Two locks in one path is a deadlock waiting to be built, and
    # it is avoided by every path agreeing on the order: invitation, then account. Nothing else
    # in this package takes an invitation lock, so the account lock can never come first.
    await credit_store.lock_account(session, account_id)

    redemption = InvitationRedemption(invitation_id=row.id, account_id=account_id)
    session.add(redemption)
    try:
        await session.flush()
    except IntegrityError as exc:
        # The unique index, not a guess: `violated_constraint` returning None means "could not
        # tell", and on a money path that has to re-raise rather than report the case we hoped
        # for. See its docstring.
        if violated_constraint(exc) == "invitation_redemption_once_per_account":
            raise RecordConflict(
                "this account has already redeemed that invitation",
                reason_code=REASON_ALREADY_REDEEMED,
            ) from exc
        raise

    amount_rao = row.credits * credit_price_rao
    entry = await credit_store.record_entry(
        session,
        account_id=account_id,
        kind=CreditEntryKind.GRANT,
        amount_rao=amount_rao,
        credit_price_rao=credit_price_rao,
        invitation_redemption_id=redemption.id,
        created_by="invitation",
    )

    # Conditional on the count we validated under the lock. The lock already makes this safe, so
    # the WHERE clause is a second line of defence rather than the mechanism: if it ever matches
    # nothing, the lock was not held and the transaction must not commit.
    result = await session.execute(
        update(Invitation)
        .where(
            Invitation.id == row.id,
            Invitation.redeemed_count < Invitation.max_redemptions,
        )
        .values(redeemed_count=Invitation.redeemed_count + 1)
    )
    if result.rowcount != 1:  # pragma: no cover - unreachable while the lock is held
        raise RecordConflict(
            "this invitation has been fully redeemed", reason_code=REASON_EXHAUSTED
        )

    return Redemption(
        redemption_id=redemption.id,
        invitation_id=row.id,
        credit_ledger_id=entry.id,
        credits=row.credits,
        amount_rao=amount_rao,
    )


async def _require_email_domain(
    session: AsyncSession, account_id: uuid.UUID, domain: str
) -> None:
    """The account's address must be verified and in `domain`.

    Verified, not merely present: an unverified address is a string someone typed, and a link
    restricted to a university would otherwise be claimable by anyone willing to type one.
    """
    statement = select(Account.email, Account.email_verified).where(
        Account.id == account_id
    )
    found = (await session.execute(statement)).first()
    email = (found.email or "") if found is not None else ""
    verified = bool(found.email_verified) if found is not None else False
    if not verified or not email.lower().endswith(f"@{domain}"):
        raise InvitationUnusable(
            f"this invitation is limited to verified {domain} addresses",
            reason_code=REASON_EMAIL_DOMAIN,
            email_domain=domain,
        )


@dataclass(frozen=True)
class RedemptionRecord:
    """One use of an invitation, for the operator detail view."""

    id: uuid.UUID
    account_id: uuid.UUID
    credit_ledger_id: int | None
    created_at: dt.datetime


async def get(session: AsyncSession, invitation_id: uuid.UUID) -> Invitation:
    row = await session.get(Invitation, invitation_id)
    if row is None:
        raise RecordNotFound("no such invitation", reason_code=REASON_NOT_FOUND)
    return row


async def redemptions_for(
    session: AsyncSession, invitation_id: uuid.UUID
) -> Sequence[RedemptionRecord]:
    """Who used this link, and which ledger entry each use produced.

    The ledger entry is found *from* the redemption rather than stored on it, because the
    reference runs that way — see V031 on why the reverse would be a cycle.
    """
    entry = credit_store.CreditLedgerEntry
    statement = (
        select(
            InvitationRedemption.id,
            InvitationRedemption.account_id,
            entry.id,
            InvitationRedemption.created_at,
        )
        .join(entry, entry.invitation_redemption_id == InvitationRedemption.id, isouter=True)
        .where(InvitationRedemption.invitation_id == invitation_id)
        .order_by(InvitationRedemption.created_at.desc())
    )
    return [
        RedemptionRecord(
            id=row[0], account_id=row[1], credit_ledger_id=row[2], created_at=row[3]
        )
        for row in (await session.execute(statement)).all()
    ]


async def listing(
    session: AsyncSession,
    *,
    now: dt.datetime,
    state: str | None = None,
    limit: int = 50,
) -> Sequence[Invitation]:
    """Newest first, optionally filtered to `active`, `expired`, `revoked` or `exhausted`.

    The filter is evaluated in SQL rather than by loading everything and testing in Python, so a
    deployment that has issued thousands of links still answers from the index.
    """
    statement = select(Invitation).order_by(
        Invitation.created_at.desc(), Invitation.id.desc()
    )
    live = Invitation.revoked_at.is_(None) & (
        Invitation.expires_at.is_(None) | (Invitation.expires_at > now)
    )
    exhausted = Invitation.redeemed_count >= Invitation.max_redemptions
    if state == "revoked":
        statement = statement.where(Invitation.revoked_at.is_not(None))
    elif state == "expired":
        statement = statement.where(
            Invitation.revoked_at.is_(None),
            Invitation.expires_at.is_not(None),
            Invitation.expires_at <= now,
        )
    elif state == "exhausted":
        statement = statement.where(live, exhausted)
    elif state == "active":
        statement = statement.where(live, ~exhausted)
    return (await session.execute(statement.limit(limit))).scalars().all()


async def revoke(
    session: AsyncSession, invitation_id: uuid.UUID, *, now: dt.datetime
) -> Invitation:
    """Withdraw a link. Soft, and idempotent.

    Never a DELETE: ledger entries reach this row through their redemptions, so removing it would
    orphan the explanation for credits already granted. Re-revoking keeps the first timestamp,
    because the moment the link stopped working is the fact worth preserving.
    """
    row = await get(session, invitation_id)
    if row.revoked_at is None:
        row.revoked_at = now
        await session.flush()
    return row


async def granted_rao(session: AsyncSession) -> int:
    """Everything given away through invitations, ever.

    One query, and it stays one query only because GRANT is its own ledger kind — which is the
    whole argument for not folding these into BONUS.
    """
    statement = select(
        func.coalesce(func.sum(credit_store.CreditLedgerEntry.amount_rao), 0)
    ).where(credit_store.CreditLedgerEntry.kind == CreditEntryKind.GRANT)
    return (await session.execute(statement)).scalar_one()


__all__ = [
    "CODE_BYTES",
    "MAX_GRANT_CREDITS",
    "REASON_ALREADY_REDEEMED",
    "REASON_EMAIL_DOMAIN",
    "REASON_EXHAUSTED",
    "REASON_EXPIRED",
    "REASON_NOT_FOUND",
    "REASON_REVOKED",
    "InvitationState",
    "InvitationUnusable",
    "Redemption",
    "RedemptionRecord",
    "code_digest",
    "create",
    "get",
    "granted_rao",
    "listing",
    "new_code",
    "read",
    "redeem",
    "redemptions_for",
    "revoke",
]
