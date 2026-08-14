"""Accounts, sessions, login challenges, and linked keys.

The seam behind ``/v1/auth`` and ``/v1/me``. Four properties are enforced here rather
than left to a router to remember:

* **Secrets are stored only as digests.** A session token and every login challenge
  secret are held as raw SHA-256 bytes. A database read — a dump, a backup, a
  replica, an over-broad SELECT — must not yield anything that can be replayed as
  a credential. Nothing in this module can return a usable token; the caller that
  generated one is the only thing that ever holds it.
* **Challenges are claimed atomically.** ``consume_challenge`` is a single
  conditional UPDATE, so a magic link or a signing nonce is usable exactly once
  even if two requests arrive together. A read-then-write check would let both in.
* **Identity lookups are exact.** Email is compared case-folded, because two
  addresses differing only in case are the same mailbox and one must not be able
  to claim the other's account. Keys are compared as-is, since SS58 is
  case-significant.
* **Nothing here decides policy.** Lifetimes, rate limits and message formats are
  the API's, passed in. This module stores what it is told and refuses what the
  schema refuses.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from conjectures_subnet.db.errors import RecordConflict, RecordNotFound
from conjectures_subnet.db.models import (
    MINER_ROLE,
    Account,
    AccountIdentity,
    AccountSession,
    AccountWallet,
    LinkedHotkey,
    LoginChallenge,
    LoginChallengeKind,
)

# Constraint and index names from deploy/migrate/sql/V003__accounts_credits_intents.sql.
# Matching on the name is what lets one IntegrityError be reported as the specific
# conflict the caller caused.
HOTKEY_UNIQUE = "linked_hotkeys_hotkey_key"
WALLET_PRIMARY = "account_wallets_pkey"
EMAIL_UNIQUE = "accounts_email_idx"
IDENTITY_SUBJECT_UNIQUE = "account_identities_provider_subject_key"
IDENTITY_PROVIDER_UNIQUE = "account_identities_account_provider_key"


def digest(secret: str) -> bytes:
    """The raw 32 bytes stored for a token or nonce.

    Plain SHA-256 rather than a password hash on purpose: these are 256-bit values
    this service generated, not user-chosen passwords, so there is no dictionary to
    slow down and stretching would only add per-request latency.
    """
    return hashlib.sha256(secret.encode("utf-8")).digest()


def normalise_email(value: str) -> str:
    """Trimmed and case-folded. The stored form for comparison, not for display."""
    return value.strip().casefold()


# --- Accounts ----------------------------------------------------------------------


async def get_account(session: AsyncSession, account_id: uuid.UUID) -> Account:
    account = await session.get(Account, account_id)
    if account is None:
        raise RecordNotFound("account not found")
    return account


async def find_by_email(session: AsyncSession, email: str) -> Account | None:
    statement = select(Account).where(
        func.lower(Account.email) == normalise_email(email)
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def find_by_coldkey(session: AsyncSession, coldkey: str) -> Account | None:
    statement = (
        select(Account)
        .join(AccountWallet, AccountWallet.account_id == Account.id)
        .where(AccountWallet.coldkey == coldkey)
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def find_by_hotkey(session: AsyncSession, hotkey: str) -> Account | None:
    """The account a submitting hotkey belongs to.

    Used to attribute a submission and to check that an intent's hotkey is one the
    account actually proved control of.
    """
    statement = (
        select(Account)
        .join(LinkedHotkey, LinkedHotkey.account_id == Account.id)
        .where(LinkedHotkey.hotkey == hotkey)
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def find_by_identity(
    session: AsyncSession, *, provider: str, subject: str
) -> tuple[Account, AccountIdentity] | None:
    """The account attached to a provider's stable subject, never to its email claim."""

    statement = (
        select(Account, AccountIdentity)
        .join(AccountIdentity, AccountIdentity.account_id == Account.id)
        .where(
            AccountIdentity.provider == provider,
            AccountIdentity.subject == subject,
        )
    )
    row = (await session.execute(statement)).one_or_none()
    return None if row is None else (row[0], row[1])


async def create_account(
    session: AsyncSession,
    *,
    email: str | None = None,
    email_verified: bool = False,
    display_name: str | None = None,
) -> Account:
    """A new account with the MINER role and nothing else.

    Roles are never taken from client input. An operator grants REVIEWER or ADMIN
    out of band; a signup cannot ask for one.
    """
    account = Account(
        email=normalise_email(email) if email else None,
        email_verified=email_verified,
        display_name=display_name,
        roles=[MINER_ROLE],
    )
    session.add(account)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise RecordConflict(
            "an account already exists for that email", reason_code="EMAIL_IN_USE"
        ) from exc
    return account


async def set_display_name(
    session: AsyncSession, account: Account, display_name: str | None
) -> Account:
    account.display_name = display_name
    await session.flush()
    return account


async def set_payout(
    session: AsyncSession, account: Account, *, coldkey: str, hotkey: str
) -> Account:
    """Set the payout destination. Both keys or neither — the schema enforces the pair.

    Alpha is held as stake, so a transfer needs both, and half a destination cannot
    be paid to.
    """
    account.payout_coldkey = coldkey
    account.payout_hotkey = hotkey
    await session.flush()
    return account


# --- External identities --------------------------------------------------------------


async def link_identity(
    session: AsyncSession,
    account: Account,
    *,
    provider: str,
    subject: str,
    email: str,
) -> AccountIdentity:
    """Attach one verified provider subject to an account.

    The two unique constraints refuse both dangerous ambiguities: one Google subject on two
    local accounts, and two Google subjects on one local account.  The caller decides whether a
    collision is a login or an explicit-link conflict; this seam only reports it faithfully.
    """

    identity = AccountIdentity(
        account_id=account.id,
        provider=provider,
        subject=subject,
        email=normalise_email(email),
    )
    session.add(identity)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        constraint = getattr(getattr(exc, "orig", None), "diag", None)
        name = getattr(constraint, "constraint_name", "")
        reason = (
            "IDENTITY_ALREADY_LINKED"
            if name == IDENTITY_SUBJECT_UNIQUE
            else "PROVIDER_ALREADY_LINKED"
            if name == IDENTITY_PROVIDER_UNIQUE
            else "IDENTITY_LINK_CONFLICT"
        )
        raise RecordConflict(
            "that external identity cannot be linked to this account",
            reason_code=reason,
        ) from exc
    return identity


async def touch_identity(
    session: AsyncSession,
    identity: AccountIdentity,
    *,
    email: str,
    now: dt.datetime,
) -> AccountIdentity:
    """Record the provider's current email claim and successful use."""

    identity.email = normalise_email(email)
    identity.last_used_at = now
    await session.flush()
    return identity


async def identities_for(
    session: AsyncSession, account_id: uuid.UUID
) -> Sequence[AccountIdentity]:
    statement = (
        select(AccountIdentity)
        .where(AccountIdentity.account_id == account_id)
        .order_by(AccountIdentity.linked_at.asc())
    )
    return list((await session.execute(statement)).scalars())


# --- Linked keys -------------------------------------------------------------------


async def link_hotkey(
    session: AsyncSession, account: Account, *, hotkey: str, signature: bytes
) -> LinkedHotkey:
    """Attach a hotkey to this account.

    Globally unique, so a hotkey already claimed by another account is a conflict
    rather than a silent re-parent: submission attribution has to have one answer,
    and a reward has to have one owner.
    """
    link = LinkedHotkey(account_id=account.id, hotkey=hotkey, signature=signature)
    session.add(link)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise RecordConflict(
            "that hotkey is already linked to an account",
            reason_code="HOTKEY_ALREADY_LINKED",
            hotkey=hotkey,
        ) from exc
    return link


async def hotkeys_for(
    session: AsyncSession, account_id: uuid.UUID
) -> Sequence[LinkedHotkey]:
    statement = (
        select(LinkedHotkey)
        .where(LinkedHotkey.account_id == account_id)
        .order_by(LinkedHotkey.linked_at.desc())
    )
    return list((await session.execute(statement)).scalars())


async def owns_hotkey(
    session: AsyncSession, account_id: uuid.UUID, hotkey: str
) -> bool:
    statement = select(LinkedHotkey.id).where(
        LinkedHotkey.account_id == account_id, LinkedHotkey.hotkey == hotkey
    )
    return (await session.execute(statement)).first() is not None


async def link_wallet(
    session: AsyncSession, account: Account, *, coldkey: str, signature: bytes
) -> AccountWallet:
    wallet = AccountWallet(
        account_id=account.id, coldkey=coldkey, signature=signature
    )
    session.add(wallet)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise RecordConflict(
            "that wallet is already linked to an account",
            reason_code="WALLET_ALREADY_LINKED",
        ) from exc
    return wallet


async def wallets_for(
    session: AsyncSession, account_id: uuid.UUID
) -> Sequence[AccountWallet]:
    statement = select(AccountWallet).where(AccountWallet.account_id == account_id)
    return list((await session.execute(statement)).scalars())


# --- Sessions ----------------------------------------------------------------------


@dataclass(frozen=True)
class Authenticated:
    """A live session and the account behind it."""

    account: Account
    session_row: AccountSession


async def create_session(
    session: AsyncSession,
    account: Account,
    *,
    token_digest: bytes,
    csrf_digest: bytes,
    expires_at: dt.datetime,
    user_agent: str | None = None,
    source_ip: str | None = None,
) -> AccountSession:
    """Record a new session. The caller holds the only copy of the token."""
    row = AccountSession(
        account_id=account.id,
        token_sha256=token_digest,
        csrf_sha256=csrf_digest,
        expires_at=expires_at,
        user_agent=user_agent,
        source_ip=source_ip,
    )
    session.add(row)
    await session.flush()
    return row


async def authenticate(
    session: AsyncSession, token_digest: bytes, *, now: dt.datetime
) -> Authenticated | None:
    """Resolve a session token digest to a live session, or None.

    A read, not a write. The rolling extension is a separate call the API makes only
    when one is due — see ``touch_session`` — because writing on every authenticated
    request would take a row lock and generate WAL for every page load.

    Expiry and revocation are part of the predicate rather than checked afterwards,
    so there is no window where an expired row is treated as live.
    """
    statement = (
        select(AccountSession, Account)
        .join(Account, Account.id == AccountSession.account_id)
        .where(
            AccountSession.token_sha256 == token_digest,
            AccountSession.revoked_at.is_(None),
            AccountSession.expires_at > now,
        )
    )
    row = (await session.execute(statement)).first()
    if row is None:
        return None
    return Authenticated(account=row[1], session_row=row[0])


async def touch_session(
    session: AsyncSession,
    session_id: uuid.UUID,
    *,
    now: dt.datetime,
    expires_at: dt.datetime,
    refresh_after: dt.timedelta,
) -> bool:
    """Extend a session's life, but only if it has gone unused for long enough.

    The window is rolling, so an active session does not expire under someone. The
    ``last_seen_at`` guard is what keeps that from meaning a write per request: the
    extension happens at most once per ``refresh_after``, and the returned bool says
    whether it did, so the API knows whether to re-send the cookie.

    Still conditioned on the session being live, so a request that races a logout
    does not resurrect the session it was revoking.
    """
    statement = (
        update(AccountSession)
        .where(
            AccountSession.id == session_id,
            AccountSession.revoked_at.is_(None),
            AccountSession.expires_at > now,
            AccountSession.last_seen_at < now - refresh_after,
        )
        .values(last_seen_at=now, expires_at=expires_at)
    )
    return (await session.execute(statement)).rowcount > 0


async def revoke_session(session: AsyncSession, session_id: uuid.UUID) -> None:
    """Idempotent: logging out twice is not an error."""
    await session.execute(
        update(AccountSession)
        .where(AccountSession.id == session_id, AccountSession.revoked_at.is_(None))
        .values(revoked_at=func.now())
    )


async def revoke_all_sessions(session: AsyncSession, account_id: uuid.UUID) -> int:
    """Every live session for an account.

    Used when an account's reachability changes — a new email verified, a payout
    address moved — so a session established under the old state cannot outlive it.
    """
    result = await session.execute(
        update(AccountSession)
        .where(
            AccountSession.account_id == account_id,
            AccountSession.revoked_at.is_(None),
        )
        .values(revoked_at=func.now())
    )
    return result.rowcount


# --- Login challenges --------------------------------------------------------------


async def create_challenge(
    session: AsyncSession,
    *,
    kind: LoginChallengeKind,
    secret_digest: bytes,
    expires_at: dt.datetime,
    account_id: uuid.UUID | None = None,
    email: str | None = None,
    ss58: str | None = None,
    message: str | None = None,
) -> LoginChallenge:
    challenge = LoginChallenge(
        kind=kind,
        account_id=account_id,
        email=normalise_email(email) if email else None,
        ss58=ss58,
        secret_sha256=secret_digest,
        message=message,
        expires_at=expires_at,
    )
    session.add(challenge)
    await session.flush()
    return challenge


async def consume_challenge(
    session: AsyncSession,
    *,
    kind: LoginChallengeKind,
    secret_digest: bytes,
    now: dt.datetime,
) -> LoginChallenge | None:
    """Claim a challenge, exactly once.

    One conditional UPDATE, so two requests presenting the same magic link or nonce
    cannot both succeed — the second matches no unconsumed row. A read-then-write
    check would admit both under concurrency, which for a magic link means a
    forwarded email logs in twice.

    ``kind`` is part of the predicate so a secret minted for one flow cannot be
    redeemed in another: a hotkey-link nonce must not be usable as a sign-in.
    """
    statement = (
        update(LoginChallenge)
        .where(
            LoginChallenge.secret_sha256 == secret_digest,
            LoginChallenge.kind == kind,
            LoginChallenge.consumed_at.is_(None),
            LoginChallenge.expires_at > now,
        )
        .values(consumed_at=now)
        .returning(LoginChallenge)
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def latest_open_challenge(
    session: AsyncSession,
    *,
    kind: LoginChallengeKind,
    ss58: str,
    now: dt.datetime,
    account_id: uuid.UUID | None = None,
) -> LoginChallenge | None:
    """The newest unconsumed, unexpired challenge for an address.

    Needed because the signature-based flows do not send the nonce back: the client returns
    only a signature, and the message it must be valid over is the one stored on this row. So
    the row is found by address and the message is read from it, rather than being rebuilt
    from anything the client supplied.

    Newest first, so re-requesting a challenge supersedes the previous one in practice while
    both remain individually single-use.
    """
    statement = (
        select(LoginChallenge)
        .where(
            LoginChallenge.kind == kind,
            LoginChallenge.ss58 == ss58,
            LoginChallenge.consumed_at.is_(None),
            LoginChallenge.expires_at > now,
        )
        .order_by(LoginChallenge.created_at.desc())
        .limit(1)
    )
    if account_id is not None:
        statement = statement.where(LoginChallenge.account_id == account_id)
    return (await session.execute(statement)).scalar_one_or_none()


async def recent_challenge_count(
    session: AsyncSession,
    *,
    kind: LoginChallengeKind,
    since: dt.datetime,
    email: str | None = None,
    ss58: str | None = None,
) -> int:
    """How many challenges this mailbox or address asked for since `since`.

    The per-identity limit. The IP-based limiter cannot cover this: mailing a magic
    link is an action taken against someone else's mailbox, so the thing to bound is
    requests per address, not per requester.
    """
    statement = select(func.count()).select_from(LoginChallenge).where(
        LoginChallenge.kind == kind, LoginChallenge.created_at >= since
    )
    if email is not None:
        statement = statement.where(
            func.lower(LoginChallenge.email) == normalise_email(email)
        )
    if ss58 is not None:
        statement = statement.where(LoginChallenge.ss58 == ss58)
    return (await session.execute(statement)).scalar_one()


__all__ = [
    "Authenticated",
    "authenticate",
    "consume_challenge",
    "create_account",
    "create_challenge",
    "create_session",
    "digest",
    "find_by_coldkey",
    "find_by_email",
    "find_by_hotkey",
    "get_account",
    "hotkeys_for",
    "latest_open_challenge",
    "link_hotkey",
    "link_wallet",
    "normalise_email",
    "owns_hotkey",
    "recent_challenge_count",
    "revoke_all_sessions",
    "revoke_session",
    "set_display_name",
    "set_payout",
    "touch_session",
    "wallets_for",
]
