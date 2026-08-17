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
    ACCOUNT_ROLES,
    MINER_ROLE,
    Account,
    AccountIdentity,
    AccountSession,
    AccountSessionKind,
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


async def set_roles(
    session: AsyncSession, account: Account, roles: Sequence[str]
) -> Account:
    """Replace an account's roles.

    Whole-set replacement rather than grant/revoke deltas, because the set is what the
    schema stores and what every read wants: a delta API over a five-element array would
    invent a concurrency problem — two simultaneous grants each reading and rewriting the
    array — that PUT-the-set does not have.

    MINER is re-added unconditionally. It is the role every account has by virtue of
    existing, `create_account` grants it, and an "admin only" account that could not
    submit would be a state no part of this system expects. Unknown roles are refused
    here as well as by the `account_roles_are_known` CHECK, so the caller gets a named
    error rather than an IntegrityError.
    """
    requested = {role.strip().upper() for role in roles if role and role.strip()}
    unknown = sorted(requested - set(ACCOUNT_ROLES))
    if unknown:
        raise RecordConflict(
            f"unknown roles: {', '.join(unknown)}",
            reason_code="UNKNOWN_ROLE",
            roles=unknown,
        )
    # Sorted so the stored array has one canonical order, which makes an audit diff of
    # two role sets readable rather than an exercise in set comparison.
    account.roles = sorted(requested | {MINER_ROLE})
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
    expires_at: dt.datetime,
    kind: AccountSessionKind = AccountSessionKind.COOKIE,
    hotkey_scope: str | None = None,
    user_agent: str | None = None,
    source_ip: str | None = None,
) -> AccountSession:
    """Record a new session. The caller holds the only copy of the token.

    ``kind`` decides whether ``hotkey_scope`` is required, and the schema's biconditional
    CHECK is what enforces it: a BEARER session is bounded to the hotkey that minted it, a
    COOKIE session is scoped to the account and must not carry one. Passing the wrong pair
    is an IntegrityError rather than a session whose authority is quietly unbounded.
    """
    row = AccountSession(
        account_id=account.id,
        kind=kind,
        token_sha256=token_digest,
        hotkey_scope=hotkey_scope,
        expires_at=expires_at,
        user_agent=user_agent,
        source_ip=source_ip,
    )
    session.add(row)
    await session.flush()
    return row


async def authenticate(
    session: AsyncSession,
    token_digest: bytes,
    *,
    kind: AccountSessionKind,
    now: dt.datetime,
) -> Authenticated | None:
    """Resolve a session token digest to a live session of that kind, or None.

    A read, not a write. The rolling extension is a separate call the API makes only
    when one is due — see ``touch_session`` — because writing on every authenticated
    request would take a row lock and generate WAL for every page load.

    Expiry and revocation are part of the predicate rather than checked afterwards,
    so there is no window where an expired row is treated as live.

    ``kind`` is part of the predicate, which makes the two credentials
    non-interchangeable: a cookie token replayed in an ``Authorization`` header matches
    nothing, and a bearer token planted in the session cookie matches nothing. Neither
    is reachable by an attacker who does not already hold the secret — the cookie is
    HttpOnly, so no script reads it to move it — but only one of the two is *ambient*,
    and a credential that can change which rules apply to it by changing
    where it is presented is the kind of confusion that is much easier to forbid here
    than to reason about at every call site.
    """
    statement = (
        select(AccountSession, Account)
        .join(Account, Account.id == AccountSession.account_id)
        .where(
            AccountSession.token_sha256 == token_digest,
            AccountSession.kind == kind,
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
    max_lifetime: dt.timedelta | None = None,
) -> bool:
    """Extend a session's life, but only if it has gone unused for long enough.

    The window is rolling, so an active session does not expire under someone. The
    ``last_seen_at`` guard is what keeps that from meaning a write per request: the
    extension happens at most once per ``refresh_after``, and the returned bool says
    whether it did, so the API knows whether to re-send the cookie.

    Still conditioned on the session being live, so a request that races a logout
    does not resurrect the session it was revoking.

    ``max_lifetime`` is the ceiling a rolling window may not roll past, measured from
    ``issued_at``. Without it "rolling" means a credential that is used regularly never
    expires at all — acceptable for an HttpOnly cookie the user can see and clear from
    their browser, much less so for a bearer token sitting in a file on a laptop. The cap
    is applied in SQL as ``LEAST`` rather than computed by the caller, because the caller
    does not have ``issued_at`` and fetching it first would open a race with a concurrent
    revoke.
    """
    ceiling = (
        func.least(expires_at, AccountSession.issued_at + max_lifetime)
        if max_lifetime is not None
        else expires_at
    )
    statement = (
        update(AccountSession)
        .where(
            AccountSession.id == session_id,
            AccountSession.revoked_at.is_(None),
            AccountSession.expires_at > now,
            AccountSession.last_seen_at < now - refresh_after,
        )
        .values(last_seen_at=now, expires_at=ceiling)
    )
    return (await session.execute(statement)).rowcount > 0


async def revoke_session(session: AsyncSession, session_id: uuid.UUID) -> None:
    """Idempotent: logging out twice is not an error."""
    await session.execute(
        update(AccountSession)
        .where(AccountSession.id == session_id, AccountSession.revoked_at.is_(None))
        .values(revoked_at=func.now())
    )


async def revoke_session_for_account(
    session: AsyncSession, session_id: uuid.UUID, account_id: uuid.UUID
) -> bool:
    """Revoke one session, but only if it belongs to this account.

    Ownership is in the predicate rather than checked first. A read-then-revoke would be
    both a race and an id-enumeration oracle: the caller would learn whether a session id
    exists before being told they may not touch it. Here a foreign id and a nonexistent
    one are the same `False`, and the same 404.
    """
    result = await session.execute(
        update(AccountSession)
        .where(
            AccountSession.id == session_id,
            AccountSession.account_id == account_id,
            AccountSession.revoked_at.is_(None),
        )
        .values(revoked_at=func.now())
    )
    return result.rowcount > 0


async def revoke_all_sessions(
    session: AsyncSession,
    account_id: uuid.UUID,
    *,
    kind: AccountSessionKind | None = None,
    except_session_id: uuid.UUID | None = None,
) -> int:
    """Every live session for an account, or every one of a single kind.

    Used when an account's reachability changes — a new email verified, a payout
    address moved — so a session established under the old state cannot outlive it.

    ``kind`` exists because signing in to the website must **not** evict a miner's CLI
    tokens. A browser sign-in is a clean boundary for the browser: whatever that cookie
    could reach before, the only live cookie afterwards is the one just issued. It is not
    a statement about a long-running CLI on another machine, and treating it as one would
    mean every visit to the website silently broke `conjectures submissions --watch`.
    Passing no ``kind`` still revokes everything, which is what an account-wide security
    action wants.

    ``except_session_id`` keeps the caller's own session alive — "sign out everywhere
    else", which is the useful shape of that button.
    """
    statement = update(AccountSession).where(
        AccountSession.account_id == account_id,
        AccountSession.revoked_at.is_(None),
    )
    if kind is not None:
        statement = statement.where(AccountSession.kind == kind)
    if except_session_id is not None:
        statement = statement.where(AccountSession.id != except_session_id)
    result = await session.execute(statement.values(revoked_at=func.now()))
    return result.rowcount


async def revoke_sessions_for_hotkey(session: AsyncSession, hotkey: str) -> int:
    """Every live bearer session scoped to this hotkey.

    Called when a hotkey stops belonging to the account that minted the token — an
    unlink, or a re-link elsewhere. The token names the account it was issued for, so
    without this it would keep working against an account that no longer holds the key
    that proved it, which is exactly the authority the scope was supposed to bound.
    """
    result = await session.execute(
        update(AccountSession)
        .where(
            AccountSession.kind == AccountSessionKind.BEARER,
            AccountSession.hotkey_scope == hotkey,
            AccountSession.revoked_at.is_(None),
        )
        .values(revoked_at=func.now())
    )
    return result.rowcount


async def live_sessions_for(
    session: AsyncSession,
    account_id: uuid.UUID,
    *,
    now: dt.datetime,
    kind: AccountSessionKind | None = None,
) -> Sequence[AccountSession]:
    """The account's live sessions, newest first. Never the digests — the caller decides
    what is safe to serialise, and `schemas_account.SessionView` is the only shape that
    crosses the API boundary."""
    statement = (
        select(AccountSession)
        .where(
            AccountSession.account_id == account_id,
            AccountSession.revoked_at.is_(None),
            AccountSession.expires_at > now,
        )
        .order_by(AccountSession.issued_at.desc())
    )
    if kind is not None:
        statement = statement.where(AccountSession.kind == kind)
    return list((await session.execute(statement)).scalars())


async def live_session_count(
    session: AsyncSession,
    account_id: uuid.UUID,
    *,
    kind: AccountSessionKind,
    now: dt.datetime,
) -> int:
    """How many live sessions of one kind this account holds.

    The per-account ceiling on concurrent CLI tokens. Without a ceiling, a hotkey that
    can mint a session can mint unboundedly many, and every one of them is a durable
    credential that has to be revoked individually to be got rid of.
    """
    statement = (
        select(func.count())
        .select_from(AccountSession)
        .where(
            AccountSession.account_id == account_id,
            AccountSession.kind == kind,
            AccountSession.revoked_at.is_(None),
            AccountSession.expires_at > now,
        )
    )
    return (await session.execute(statement)).scalar_one()


async def oldest_live_session(
    session: AsyncSession,
    account_id: uuid.UUID,
    *,
    kind: AccountSessionKind,
    now: dt.datetime,
) -> AccountSession | None:
    """The least recently issued live session of one kind.

    What the per-account ceiling evicts when it is reached. Evicting the oldest rather
    than refusing the new login is the choice that cannot lock a miner out of their own
    tooling: a stale token on a machine they no longer use would otherwise be able to
    deny them a working one.
    """
    statement = (
        select(AccountSession)
        .where(
            AccountSession.account_id == account_id,
            AccountSession.kind == kind,
            AccountSession.revoked_at.is_(None),
            AccountSession.expires_at > now,
        )
        .order_by(AccountSession.issued_at.asc())
        .limit(1)
    )
    return (await session.execute(statement)).scalar_one_or_none()


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


async def open_challenge_by_nonce(
    session: AsyncSession,
    *,
    kind: LoginChallengeKind,
    ss58: str,
    secret_digest: bytes,
    now: dt.datetime,
    max_attempts: int,
) -> LoginChallenge | None:
    """The one unconsumed challenge this nonce names, or None.

    The alternative — ``latest_open_challenge``, which the coldkey flow uses — picks the
    newest open row for an address, and that is a denial-of-service primitive whenever the
    address is public knowledge. Hotkeys are published on chain. An attacker who requests a
    challenge for someone else's hotkey supersedes the challenge that person is in the middle
    of signing, so their signature arrives valid over a message that is no longer "latest"
    and is refused. Repeat once a minute and that miner can never log in again.

    Naming the row by its own nonce removes the race entirely: two challenges for one address
    coexist and each is redeemable by whoever holds its nonce. The nonce is not the proof —
    the signature is, and it is checked against the message stored on *this* row — so echoing
    it back costs nothing. It is the difference between "which challenge is current" (a global
    question, and therefore attackable) and "which challenge is this" (a local one).

    ``ss58`` stays in the predicate as well, so a nonce minted for one address cannot be
    redeemed for another even if it leaks, and ``attempts`` bounds how many signatures may be
    offered against a single challenge.
    """
    statement = select(LoginChallenge).where(
        LoginChallenge.secret_sha256 == secret_digest,
        LoginChallenge.kind == kind,
        LoginChallenge.ss58 == ss58,
        LoginChallenge.consumed_at.is_(None),
        LoginChallenge.expires_at > now,
        LoginChallenge.attempts < max_attempts,
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def record_failed_attempt(session: AsyncSession, challenge_id: uuid.UUID) -> int:
    """Count one failed signature against a challenge, and return the new total.

    Incremented in SQL rather than read-modify-written, so concurrent attempts each count.
    A read-then-write would let a burst of parallel guesses all observe the same low value.
    """
    statement = (
        update(LoginChallenge)
        .where(LoginChallenge.id == challenge_id)
        .values(attempts=LoginChallenge.attempts + 1)
        .returning(LoginChallenge.attempts)
    )
    return (await session.execute(statement)).scalar_one()


async def hotkey_still_linked(
    session: AsyncSession, *, hotkey: str, account_id: uuid.UUID
) -> bool:
    """Whether this hotkey is *currently* linked to this account.

    Checked on every request a bearer session authenticates, not only at mint. A bearer token
    records the hotkey that proved it, and that hotkey is the entire basis for the token's
    existence — so if the link is gone, the token's authority is gone with it, and the next
    request must fail rather than the next expiry. Revoking scoped sessions eagerly on unlink
    is the other half of this (``revoke_sessions_for_hotkey``); this is the half that does not
    depend on remembering to call it.
    """
    statement = select(LinkedHotkey.id).where(
        LinkedHotkey.hotkey == hotkey, LinkedHotkey.account_id == account_id
    )
    return (await session.execute(statement)).first() is not None


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
    "hotkey_still_linked",
    "hotkeys_for",
    "latest_open_challenge",
    "link_hotkey",
    "link_wallet",
    "live_session_count",
    "live_sessions_for",
    "normalise_email",
    "oldest_live_session",
    "owns_hotkey",
    "recent_challenge_count",
    "revoke_all_sessions",
    "revoke_session",
    "revoke_session_for_account",
    "revoke_sessions_for_hotkey",
    "set_display_name",
    "set_payout",
    "set_roles",
    "touch_session",
    "wallets_for",
]
