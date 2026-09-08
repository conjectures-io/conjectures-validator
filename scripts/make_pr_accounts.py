#!/usr/bin/env python3
"""Mint accounts for external contributors: keys, free credits, a CLI token, a sign-in link.

An outside contributor who wants to try a proof needs three things they cannot get for
themselves: an account, a coldkey linked and designated for submitting, and credit to spend. The website gives them
the first two only after a coldkey signature or a magic link, and the third only after a
TAO transfer. This script skips all three, once, on purpose -- it is how a promotional or
press account gets made.

It hands out two credentials, because the API deliberately separates them. The BEARER token
is the CLI: it submits, and that is all it can do. The **sign-in link** is the browser, and
it is the half that matters for anything the token is forbidden -- above all pointing the
payout destination at a key the contributor owns. The link is a real `login_challenges` row
of kind EMAIL, identical to the one `POST /v1/auth/email/request-link` writes, so it
redeems through the ordinary single-use path and nothing about it is a special case. It is
pre-minted rather than mailed because `send_login_link` raises ServiceUnavailable where no
SMTP transport is configured, and a contributor who can never receive a link would
otherwise have no way into their own account.

    # one account per contributor, 10 credits each
    scripts/make_pr_accounts.py alice@example.com bob@example.com

    # five throwaway keys with no mailbox behind them
    scripts/make_pr_accounts.py --anonymous 5

    # an account with a mailbox as well as its key
    scripts/make_pr_accounts.py alice@example.com

It writes three kinds of file into a fresh directory and touches no database:

    grant.sql          the statements to run. Contains no secret -- only public addresses
                       and the SHA-256 of each token and link, which is all the server
                       ever stores.
    <label>.txt        one handout per contributor: sign-in link, mnemonics, token, and
                       how to use them. Mode 0600, and the only copy -- nothing here can
                       be regenerated.
    accounts.csv       the operator's record. Addresses, emails and digests, no secrets.

Then run the SQL yourself, so the write to the database is a thing you did deliberately:

    psql "$DATABASE_URL" -v i_am_sure=1 -f <dir>/grant.sql

WHAT THE CONTRIBUTOR CAN DO WITH IT: everything the CLI does -- read their own account,
open a submission intent, upload a bundle, and confirm it with a coldkey signature. The
token is a BEARER session, so `submission_api/dependencies.require_cookie_writer` still
refuses the four writes that could take an account over: linking another coldkey, setting
the payout destination, editing the profile, and claiming a deposit. Those need the
browser -- which is what the sign-in link is for, and why an `--anonymous` account (no
mailbox, so no link is possible) can submit but can never be paid.

The link's window is the one number here with no counterpart in the API: 24 hours by
default, against EMAIL_LINK_MINUTES=15 for a link someone just asked for. A handout sits in
an inbox for a day before anyone opens it. 24h is also the most that setting can be raised
to (`maximum=1_440`), so a link this writes is never longer-lived than one the deployment
could be configured to mail. It is single use, and it is dead the moment it is redeemed.

THE CREDIT IS UNBACKED. It is an ADJUSTMENT in the ledger, which is the kind that means
"an operator put this here", and the schema makes it say why. There is no deposit and no
transfer behind it, and the balance it creates is spendable on real verification.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import os
import re
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from bittensor.sp_core import Keypair

ROOT = Path(__file__).resolve().parent.parent

# The `ss58` domain (deploy/migrate/sql/V001) and `EMAIL_SHAPE`
# (conjectures_subnet/db/models.py). Checked here so a bad input is a message about the
# argument you typed rather than a constraint violation naming a domain.
SS58_SHAPE = re.compile(r"[1-9A-HJ-NP-Za-km-z]{48}")
EMAIL_SHAPE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

# submission_api/sessions.py. The prefix is inside the credential -- `digest()` hashes the
# whole string -- so it has to be exactly this, and the entropy is the same 256 bits either
# way. It is there to make a leaked token greppable by secret scanners.
BEARER_TOKEN_PREFIX = "conj_cli_"
TOKEN_BYTES = 32

# The default PAYMENT_AMOUNT_RAO / CREDIT_PRICE_RAO: 0.5 TAO for one verification attempt.
DEFAULT_CREDIT_PRICE_RAO = 500_000_000

# Written to `credit_ledger.created_by`, and the marker that makes a re-run of the emitted
# SQL grant no further credit. Changing it re-grants to every account that already has one.
CREATED_BY = "operator:make_pr_accounts.py"

DEFAULT_CREDITS = 10

# Under the ceiling, and above what the CLI mints for itself. `submission_api/settings.py`
# has two numbers: CLI_SESSION_DAYS (14) is the rolling window a `conjectures auth login`
# token gets, and CLI_SESSION_MAX_DAYS (90) is the absolute ceiling measured from
# `issued_at` that rolling may not roll past. A handout token is not refreshed by a cron
# job the way a miner's is, so the rolling window is the wrong number for it -- but the
# ceiling is real and is applied as a LEAST on every touch, so a row written with a longer
# expiry than the deployment's cap is silently shortened the first time it is used.
#
# So what this number really buys is time to *first* use: `touch_session` assigns
# `expires_at` rather than extending it, which means the first request past the refresh
# interval replaces whatever is written here with `now + CLI_SESSION_DAYS`. A handout that
# sits in an inbox for three weeks needs the longer initial window; one being used does not.
DEFAULT_SESSION_DAYS = 30
# DEFAULT_CLI_SESSION_MAX_DAYS. Only used to warn: the deployment's value is what counts,
# and this script does not read the API's environment.
CLI_SESSION_MAX_DAYS_DEFAULT = 90
DEFAULT_API = "https://api.conjectures.io"

# The magic link points at the *website*, not the API: `mail.magic_link` builds
# `{WEBSITE_BASE_URL}/auth/verify?token=...`, and the page there POSTs the token to
# /v1/auth/email/verify. A link built against the wrong base URL is a 404 for the
# contributor, so this has to match the deployment's WEBSITE_BASE_URL.
DEFAULT_WEBSITE = "https://conjectures.io"
MAGIC_LINK_PATH = "/auth/verify"

# How long a pre-minted sign-in link lives. The API's own default is EMAIL_LINK_MINUTES=15,
# which is right for a link that was just requested and is sitting in an inbox being read
# now -- and useless for one handed out in a file. 24 hours is the ceiling `settings.py`
# allows that setting to be raised to (`maximum=1_440`), so a link this script writes is
# never longer-lived than one the deployment could be configured to mail.
DEFAULT_LINK_HOURS = 24
API_MAX_EMAIL_LINK_HOURS = 24

MAX_DISPLAY_NAME = 64  # accounts.display_name_length


class UsageError(RuntimeError):
    """Something about the invocation, reported without a traceback."""


@dataclass(frozen=True)
class Contributor:
    """One account to create, and the secrets only its handout will hold."""

    label: str
    email: str | None
    display_name: str
    # One key since V035: it signs in, it is designated as the submission coldkey, it scopes
    # the bearer token, and it is the payout destination. There used to be a separate hotkey
    # for the last three of those.
    coldkey: str
    coldkey_mnemonic: str
    bearer_token: str
    # None for an account with no email: `verify_email` resolves the account by the
    # challenge's address (`find_by_email`), so a link to a mailbox nobody owns resolves to
    # nothing, and `challenge_email_present` would refuse the row anyway.
    link_token: str | None

    @property
    def token_sha256_hex(self) -> str:
        """What the database stores. The token itself is recoverable from nothing."""
        return _digest_hex(self.bearer_token)

    @property
    def link_sha256_hex(self) -> str | None:
        return None if self.link_token is None else _digest_hex(self.link_token)

    def magic_link(self, website: str) -> str | None:
        """The URL, built exactly as `submission_api.mail.magic_link` builds it.

        `quote(safe="")` is redundant for a `token_urlsafe` value and applied anyway,
        because the thing that must not drift is the *encoding rule*: a link this script
        writes has to be byte-identical to one the API would mail for the same token.
        """
        if self.link_token is None:
            return None
        token = quote(self.link_token, safe="")
        return f"{website.rstrip('/')}{MAGIC_LINK_PATH}?token={token}"


# --- Key material -----------------------------------------------------------------------


def new_keypair() -> tuple[str, str]:
    """A fresh sr25519 keypair, as (mnemonic, SS58 address for prefix 42).

    Retried rather than returned blind: a public key whose first byte is zero base58-encodes
    to 47 characters, and the `ss58` domain demands exactly 48, so roughly one key in 256
    cannot be stored at all. Better to discard it here than to fail the INSERT after the
    mnemonic has been written into a handout.
    """
    for _ in range(64):
        mnemonic = Keypair.generate_mnemonic()
        address = Keypair.create_from_mnemonic(mnemonic).ss58_address
        if SS58_SHAPE.fullmatch(address):
            return mnemonic, address
    raise RuntimeError("could not generate a 48-character SS58 address in 64 attempts")


def new_bearer_token() -> str:
    """The CLI credential, shaped exactly as `sessions.new_bearer_token` shapes it."""
    return f"{BEARER_TOKEN_PREFIX}{secrets.token_urlsafe(TOKEN_BYTES)}"


def new_link_token() -> str:
    """The magic-link secret: `sessions.new_token()`, which is what the mailer sends.

    No prefix, unlike the bearer token. The API's own links carry none, and this value has
    to be indistinguishable from one `request_email_link` minted -- it is redeemed by the
    same `consume_challenge` predicate, and nothing there would tolerate a difference.
    """
    return secrets.token_urlsafe(TOKEN_BYTES)


def _digest_hex(secret: str) -> str:
    """`accounts.digest`, in hex for the SQL. Plain SHA-256 of the UTF-8 bytes."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


# --- Who we are creating ----------------------------------------------------------------


def _sanitize_label(raw: str) -> str:
    """A label safe to use as a filename and readable in a NOTICE."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-.")
    return (cleaned or "contributor")[:48]


def _unique(label: str, taken: set[str]) -> str:
    if label not in taken:
        taken.add(label)
        return label
    for suffix in range(2, 1000):
        candidate = f"{label}-{suffix}"
        if candidate not in taken:
            taken.add(candidate)
            return candidate
    raise UsageError(f"cannot find a free label for {label!r}")


def plan(
    emails: list[str],
    anonymous: int,
    batch: str,
    *,
    with_links: bool,
) -> list[Contributor]:
    """Turn the arguments into the accounts to create, keys and all."""
    seen_emails: set[str] = set()
    labels: set[str] = set()
    contributors: list[Contributor] = []

    for email in emails:
        if not EMAIL_SHAPE.fullmatch(email):
            raise UsageError(
                f"{email!r} is not an email address the `account_email_shape` "
                f"constraint accepts"
            )
        # accounts_email_idx is unique on lower(email), so two spellings of one mailbox are
        # one account. Creating both would put the second contributor on the first's account.
        folded = email.lower()
        if folded in seen_emails:
            raise UsageError(f"{email!r} appears twice; one account per mailbox")
        seen_emails.add(folded)

        local = email.split("@", 1)[0]
        contributors.append(
            _mint(
                label=_unique(_sanitize_label(local), labels),
                email=email,
                display_name=local[:MAX_DISPLAY_NAME],
                with_links=with_links,
            )
        )

    for index in range(1, anonymous + 1):
        label = _unique(f"{batch}-{index:02d}", labels)
        contributors.append(
            _mint(
                label=label,
                email=None,
                display_name=label[:MAX_DISPLAY_NAME],
                with_links=with_links,
            )
        )

    return contributors


def _mint(
    *,
    label: str,
    email: str | None,
    display_name: str,
    with_links: bool,
) -> Contributor:
    # Always generated, where a `--with-coldkey` flag used to gate it. There is one kind of
    # key now: a contributor without one could sign in by email but could not submit,
    # because the intent flow signs as the account's designated submission coldkey. So the
    # flag had nothing left to turn off and is gone.
    coldkey_mnemonic, coldkey = new_keypair()
    return Contributor(
        label=label,
        email=email,
        display_name=display_name or label,
        coldkey=coldkey,
        coldkey_mnemonic=coldkey_mnemonic,
        bearer_token=new_bearer_token(),
        link_token=new_link_token() if (email and with_links) else None,
    )


# --- The SQL ----------------------------------------------------------------------------


def _lit(value: str | None) -> str:
    """A SQL string literal, or NULL. Inputs are validated, but quoting is not optional."""
    if value is None:
        return "NULL"
    return "'" + value.replace("'", "''") + "'"


def render_sql(
    contributors: list[Contributor],
    *,
    batch: str,
    credits: int,
    credit_price_rao: int,
    session_days: int,
    link_hours: int,
    generated_at: str,
) -> str:
    rows = ",\n".join(
        "    ("
        + ", ".join(
            (
                _lit(c.label),
                _lit(c.email),
                _lit(c.display_name),
                _lit(c.coldkey),
                f"decode('{c.token_sha256_hex}', 'hex')",
                "NULL"
                if c.link_sha256_hex is None
                else f"decode('{c.link_sha256_hex}', 'hex')",
            )
        )
        + ")"
        for c in contributors
    )
    with_links = sum(1 for c in contributors if c.link_token is not None)

    return rf"""-- =====================================================================================
-- {len(contributors)} external-contributor account(s), {credits} credit(s) each.
--
-- Generated {generated_at} by scripts/make_pr_accounts.py, batch {batch}.
-- Do not hand-edit: the token digests below match handout files that cannot be regenerated.
--
--     psql "$DATABASE_URL" -v i_am_sure=1 -f grant.sql
--
-- or, with the database in compose and no psql on the host:
--
--     docker exec -i conjectures_db sh -c \
--         'psql -v ON_ERROR_STOP=1 -v i_am_sure=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
--         < grant.sql
--
-- NOTHING HERE IS SECRET. The mnemonics and the bearer tokens are in the handout files
-- beside this one; what is here is the public address of each key and the SHA-256 of each
-- token, which is what `account_sessions` stores anyway. A read of this file grants nobody
-- anything, so it is safe to paste into a ticket or a shell history.
--
-- WHAT IT DOES, per row
--   1. Finds the account that owns the coldkey, else the email. Failing both -- the normal
--      case for a freshly generated key -- creates one.
--   2. Links the coldkey, designates it as the account's submission coldkey, and sets it as
--      the payout destination if the account has none.
--   3. Appends one ADJUSTMENT of {credits} x {credit_price_rao} rao to the credit ledger.
--   4. Inserts the BEARER session whose digest is in this file, scoped to the coldkey.
--   5. Inserts an EMAIL login challenge -- the magic link -- valid for {link_hours} hour(s),
--      for the {with_links} of {len(contributors)} account(s) that have an address. It is the
--      same row `POST /v1/auth/email/request-link` writes, so the link redeems through the
--      ordinary `consume_challenge` path: single use, and dead the moment it is used.
--
-- RE-RUNNABLE, and deliberately not a top-up: step 3 is skipped for an account that
-- already holds a grant from this script, so running the file twice refreshes the sessions
-- and doubles nobody's promotion. Pass `-v force_topup=1` to grant another {credits} to
-- every account named here.
--
-- WHAT IT DOES NOT DO
--   * It does not verify a signature. `account_wallets.signature` is 64 zero bytes, because
--     writing the row directly is the point -- it is what skips the challenge-and-sign round
--     trip. Nothing downstream re-reads it.
--   * It does not verify the email. `email_verified` stays false, which is true: nobody
--     has proved they can read that mailbox. The magic-link flow flips it on first use
--     (submission_api/routers/auth.py), so the contributor verifies it by signing in --
--     including with the link written here, which is the one respect in which a pre-minted
--     link is weaker than a mailed one: it proves the address was typed correctly, not that
--     the person reading it owns the mailbox. Hand the handouts out accordingly.
--   * It does not send any mail. That is the point of pre-minting: `send_login_link` raises
--     ServiceUnavailable on a deployment with no SMTP transport, and a contributor who can
--     never receive a link would otherwise have no way into the account at all.
--   * It does not touch `deposits` or `chain_transfers`. There is no transfer behind this
--     credit, which is why the entry is an ADJUSTMENT and not a DEPOSIT: DEPOSIT is
--     constrained to name a source row, and inventing one would claim money arrived.
--
-- THE CREDIT IS SPENDABLE AND UNBACKED. {credits * len(contributors)} credit(s) of
-- verification, {credits * credit_price_rao * len(contributors)} rao of ledger balance, with
-- no payment behind any of it. That is an operator decision; this file is the record of it.
-- =====================================================================================

\if :{{?i_am_sure}}
\else
\echo ''
\echo 'refusing: this mints spendable credit with no payment behind it, and inserts'
\echo 'live session credentials. Re-run with -v i_am_sure=1 once you mean it.'
\echo ''
\quit
\endif

-- Off unless asked for. See RE-RUNNABLE above.
\if :{{?force_topup}}
\else
\set force_topup 0
\endif

\set ON_ERROR_STOP on
\set credit_price_rao {credit_price_rao}

BEGIN;

-- psql substitutes `:name` in the query buffer but never inside a dollar-quoted body, so
-- the one value the operator can override reaches the block below through a
-- transaction-local session setting instead.
SELECT set_config('pr.force_topup', (:force_topup)::text, true) \gset _discard_

-- The batch, typed against the schema's own domains: a mistyped address fails here, naming
-- this table, instead of part-way through the loop with half the accounts written. Session
-- scoped rather than ON COMMIT DROP, so the summary after COMMIT can still join it.
CREATE TEMP TABLE pr_batch (
    label        text  NOT NULL,
    email        text,
    display_name text  NOT NULL,
    coldkey      ss58  NOT NULL,
    token_sha256 sha256 NOT NULL,
    -- NULL where there is no email. `challenge_email_present` refuses an EMAIL challenge
    -- without an address, and a link that resolved to no mailbox would sign nobody in.
    link_sha256  sha256
);

INSERT INTO pr_batch
    (label, email, display_name, coldkey, token_sha256, link_sha256) VALUES
{rows};

DO $pr$
DECLARE
    c_credits    bigint  := {credits};
    c_price      bigint  := {credit_price_rao};
    c_days       int     := {session_days};
    c_link_hours int     := {link_hours};
    c_batch      text    := {_lit(batch)};
    c_created_by text    := {_lit(CREATED_BY)};
    -- 64 bytes, the length `wallet_signature_len` demands.
    -- Zeroes rather than plausible-looking bytes: these links were never signed, and the
    -- row should say so to anyone who looks.
    c_unsigned   bytea   := decode(repeat('00', 64), 'hex');

    v_topup      boolean := current_setting('pr.force_topup')::boolean;
    r            record;
    v_account    uuid;
    v_owner      uuid;
    v_created    int     := 0;
    v_granted    int     := 0;
    v_linked     int     := 0;
BEGIN
    FOR r IN SELECT * FROM pr_batch ORDER BY label LOOP
        v_account := NULL;

        -- The coldkey first: it is globally unique and it is the identity the submission
        -- will be made under, so if it is already linked, that account is the only correct
        -- answer. A freshly generated key never is -- this is what makes a second run of
        -- this file land on the accounts the first run created rather than creating them
        -- again.
        SELECT account_id INTO v_account FROM account_wallets WHERE coldkey = r.coldkey;

        IF v_account IS NULL AND r.email IS NOT NULL THEN
            SELECT id INTO v_account FROM accounts WHERE lower(email) = lower(r.email);
        END IF;

        IF v_account IS NULL THEN
            INSERT INTO accounts (email, email_verified, display_name, roles)
            VALUES (r.email, false, r.display_name, ARRAY['MINER']::text[])
            RETURNING id INTO v_account;
            v_created := v_created + 1;
            RAISE NOTICE '% -> created account %', r.label, v_account;
        ELSE
            -- Someone who already has an account. Credit it rather than making a second
            -- one: two accounts for one mailbox is a support problem, and the credit would
            -- be on the one they are not signed in to.
            RAISE NOTICE '% -> existing account %', r.label, v_account;
        END IF;

        -- MINER is the role every submission endpoint assumes, and the only one a bearer
        -- token may exercise (`dependencies.BEARER_ROLES`). An existing account that lost
        -- it would authenticate and then fail further in, which reads as a credit problem.
        UPDATE accounts
        SET roles = array_append(roles, 'MINER')
        WHERE id = v_account AND NOT ('MINER' = ANY(roles));

        -- `coldkey` is the primary key of account_wallets: one coldkey signs in to exactly
        -- one account. If it is someone else's, that is a real conflict, and crediting an
        -- account nobody in this batch can sign in to is worse than stopping.
        SELECT account_id INTO v_owner FROM account_wallets WHERE coldkey = r.coldkey;
        IF v_owner IS NULL THEN
            INSERT INTO account_wallets (account_id, coldkey, signature)
            VALUES (v_account, r.coldkey, c_unsigned);
        ELSIF v_owner <> v_account THEN
            RAISE EXCEPTION
                '%: coldkey % is the sign-in key for account %, not %',
                r.label, r.coldkey, v_owner, v_account;
        END IF;

        -- The designation. `open_intent` signs as `Account.submission_coldkey` and refuses
        -- with NO_SUBMISSION_COLDKEY when none is set, so a contributor handed credit but no
        -- designation would have a balance they could not spend -- which is the whole thing
        -- this script exists to hand out. Ordered after the link because
        -- `account_submission_coldkey_is_linked` is a composite foreign key against
        -- (account_id, coldkey).
        --
        -- Unlike the payout below, this is set even when the account already has one: it
        -- has to name the key whose bearer token this file carries, or `assert_coldkey_in_scope`
        -- refuses every submission that token makes.
        UPDATE accounts
        SET submission_coldkey = r.coldkey
        WHERE id = v_account AND submission_coldkey IS DISTINCT FROM r.coldkey;

        -- Somewhere to be paid, if the account has nowhere yet. Not needed to submit, but a
        -- submission that reaches ELIGIBLE with no destination cannot be paid, and that
        -- failure surfaces a long way from here. Only when it is NULL: never repoint a
        -- destination the account holder chose.
        UPDATE accounts
        SET payout_coldkey = r.coldkey
        WHERE id = v_account AND payout_coldkey IS NULL;

        IF v_topup OR NOT EXISTS (
            SELECT 1 FROM credit_ledger
            WHERE account_id = v_account
              AND kind = 'ADJUSTMENT'
              AND created_by = c_created_by
        ) THEN
            INSERT INTO credit_ledger
                (account_id, kind, amount_rao, credit_price_rao, reason, created_by)
            VALUES (v_account, 'ADJUSTMENT', c_credits * c_price, c_price,
                    format('External contributor grant: %s credits at %s rao '
                           '(batch %s, %s)', c_credits, c_price, c_batch, r.label),
                    c_created_by);
            v_granted := v_granted + 1;
            RAISE NOTICE '% -> granted % credits', r.label, c_credits;
        ELSE
            RAISE NOTICE
                '% -> already granted by this script; no credit added (-v force_topup=1 to add)',
                r.label;
        END IF;

        -- The CLI credential. Only the digest is stored, so the token exists in the handout
        -- file and nowhere else -- not here, and not in the terminal that ran this.
        -- `coldkey_scope` is what bounds it: `assert_coldkey_in_scope` refuses this token
        -- acting for any other key, and `coldkey_still_linked` re-checks the link above on
        -- every request, so unlinking the coldkey revokes the token.
        INSERT INTO account_sessions
            (account_id, kind, token_sha256, coldkey_scope, expires_at, user_agent)
        VALUES (v_account, 'BEARER', r.token_sha256, r.coldkey,
                now() + (c_days || ' days')::interval, 'make_pr_accounts.py')
        ON CONFLICT (token_sha256) DO UPDATE
            SET account_id   = EXCLUDED.account_id,
                coldkey_scope = EXCLUDED.coldkey_scope,
                expires_at   = EXCLUDED.expires_at,
                last_seen_at = now(),
                -- A revoked session has to come back, or a second run of this file would
                -- hand back a token that no longer authenticates.
                revoked_at   = NULL;

        -- The magic link. `account_id` stays NULL, as `request_email_link` leaves it: an
        -- EMAIL challenge is bound to the *address*, and `verify_email` resolves the
        -- account from it at redemption (`find_by_email`). Writing an account_id here
        -- would be inventing a binding the redeeming code does not read.
        --
        -- The address is stored lowercased because `create_challenge` stores
        -- `normalise_email(email)`. Nothing depends on it -- redemption matches the digest,
        -- and `find_by_email` lowercases both sides -- but a row that differs from the one
        -- the API writes is a row someone will eventually have to explain.
        IF r.link_sha256 IS NOT NULL THEN
            INSERT INTO login_challenges (kind, email, secret_sha256, expires_at)
            VALUES ('EMAIL', lower(r.email), r.link_sha256,
                    now() + (c_link_hours || ' hours')::interval)
            ON CONFLICT (secret_sha256) DO UPDATE
                SET expires_at = EXCLUDED.expires_at
                -- Only while it is still unused. Re-running this file to refresh the
                -- sessions must not resurrect a sign-in link somebody already redeemed:
                -- single-use is the property that makes a link in a file tolerable.
                WHERE login_challenges.consumed_at IS NULL;
            v_linked := v_linked + 1;
        END IF;
    END LOOP;

    RAISE NOTICE 'batch %: % account(s) created, % grant(s) written, % link(s) minted',
        c_batch, v_created, v_granted, v_linked;
END
$pr$;

COMMIT;


-- =====================================================================================
-- What now exists. `credits_available` mirrors `conjectures_subnet.db.credits`: the ledger
-- sum minus live holds, floored to whole credits and clamped at zero.
-- =====================================================================================

\echo ''
SELECT b.label,
       a.email,
       a.email_verified,
       b.coldkey,
       a.id AS account_id,
       a.payout_coldkey IS NOT NULL AS payout_set,
       -- ::bigint before dividing: `sum(bigint)` is numeric, and numeric division would
       -- report a whole credit as 10.0000000000000000 rather than flooring as the API does.
       (greatest(bal.balance_rao - held.held_rao, 0))::bigint
           / (:credit_price_rao)::bigint AS credits_available,
       s.expires_at AS token_expires,
       -- Three states worth telling apart: no link (no email), a live one, and one the
       -- contributor has already used -- which is the success case, not a problem.
       CASE
           WHEN b.link_sha256 IS NULL THEN 'none'
           WHEN lc.consumed_at IS NOT NULL THEN 'used ' || to_char(lc.consumed_at, 'YYYY-MM-DD HH24:MI')
           WHEN lc.expires_at <= now() THEN 'expired'
           ELSE 'valid until ' || to_char(lc.expires_at, 'YYYY-MM-DD HH24:MI')
       END AS sign_in_link
FROM pr_batch AS b
JOIN account_wallets  AS w  ON w.coldkey = b.coldkey
JOIN accounts         AS a  ON a.id = w.account_id
JOIN account_sessions AS s  ON s.token_sha256 = b.token_sha256
LEFT JOIN login_challenges AS lc ON lc.secret_sha256 = b.link_sha256
CROSS JOIN LATERAL (
    SELECT coalesce(sum(amount_rao), 0) AS balance_rao
    FROM credit_ledger WHERE account_id = a.id
) AS bal
CROSS JOIN LATERAL (
    SELECT coalesce(sum(credits_held * credit_price_rao), 0) AS held_rao
    FROM submission_intents
    WHERE account_id = a.id
      AND status IN ('OPEN', 'BUNDLE_ATTACHED')
      AND expires_at > now()
) AS held
ORDER BY b.label;

\echo ''
\echo 'Hand each contributor their own <label>.txt and nothing else. To revoke one token:'
\echo '  UPDATE account_sessions SET revoked_at = now()'
\echo '   WHERE token_sha256 = decode(''<hex from accounts.csv>'', ''hex'');'
\echo 'To revoke every token in this batch:'
\echo '  UPDATE account_sessions SET revoked_at = now()'
\echo '   WHERE user_agent = ''make_pr_accounts.py'' AND revoked_at IS NULL;'
\echo 'The grants stay in the ledger either way -- it is append-only, and a revoked session'
\echo 'is not a refund. Reverse one with a negative ADJUSTMENT that says why.'
\echo ''
\echo 'An unredeemed sign-in link is killed the same way, by expiring it:'
\echo '  UPDATE login_challenges SET expires_at = now()'
\echo '   WHERE secret_sha256 = decode(''<link_sha256 from accounts.csv>'', ''hex'')'
\echo '     AND consumed_at IS NULL;'
\echo ''
"""


# --- The handouts -----------------------------------------------------------------------


def render_handout(
    c: Contributor,
    *,
    batch: str,
    credits: int,
    session_days: int,
    link_hours: int,
    api: str,
    website: str,
    generated_at: str,
) -> str:
    link = c.magic_link(website)
    if link is not None:
        sign_in_block = f"""
Sign-in link -- click this first ({link_hours} hour(s), one use only)

  {link}

  It signs you in to the website as {c.email} and marks that address verified. Single
  use: the moment it is redeemed it is dead, and if a mail scanner or a link preview
  fetches it first, it will be dead before you get there. That is what the fallback below
  is for.

  The website is where the things the CLI token deliberately cannot do get done -- above
  all setting the payout destination, which is where any reward for your work goes.

  When this link expires, ask the site for another; nothing about it was special:

    curl -sS -X POST {api}/v1/auth/email/request-link \\
         -H 'Content-Type: application/json' \\
         -d '{{"email": "{c.email}"}}'

  That answers 202 whether or not it sent anything -- deliberately, so the endpoint cannot
  be used to find out who has an account -- so if no mail arrives, ask us rather than
  retrying. This deployment may have no mail transport configured at all, which is exactly
  why the link above was minted for you by hand.
"""
    elif c.email is not None:
        # An address, but no link was pre-minted (--no-links). Getting in is a request away
        # -- provided the deployment can actually send mail, which is the whole reason
        # pre-minting is the default.
        sign_in_block = f"""
Sign-in: ask the site for a link to {c.email}

  curl -sS -X POST {api}/v1/auth/email/request-link \\
       -H 'Content-Type: application/json' \\
       -d '{{"email": "{c.email}"}}'

  It answers 202 whether or not it sent anything -- deliberately, so the endpoint cannot be
  used to find out who has an account -- so if no mail arrives, ask us rather than retrying.
  Signing in that way also marks the address verified.

  The website is where the things the CLI token deliberately cannot do get done -- above
  all setting the payout destination, which is where any reward for your work goes.
"""
    else:
        sign_in_block = """
No sign-in link, and no way to make one: this account has no email address on it, so there
is nothing for a link to be sent to and nothing for one to resolve to. The token below is
all you need to submit, but the payout destination cannot be set on an account nobody can
sign in to, and a submission that becomes eligible for reward will have nowhere to be paid.
Ask for an account with your own email address on it before you submit anything you want
paid for.
"""

    return f"""\
Conjectures Subnet 66 -- contributor account "{c.label}"
Issued {generated_at} (batch {batch}). {credits} credit(s), one per verification attempt.

Everything below is a secret and this is the only copy. Nothing here can be regenerated:
the mnemonics exist in this file alone, and the server stores only a hash of the token and
of the link.
{sign_in_block}
Coldkey (the identity your submissions are made under, and where rewards go)
  address   {c.coldkey}
  mnemonic  {c.coldkey_mnemonic}

  Import it when you need to sign:
    btcli wallet regen_coldkey --wallet.name conjectures \\
          --mnemonic "{c.coldkey_mnemonic}"

  One key, not two. Miners sign with a coldkey now, so this single address signs in, signs
  every submission, and is where a reward is sent.

  It was generated for you, which means it passed through someone else's machine. Treat it
  as a demo key: before you submit anything you want paid for, sign in at the website and
  repoint the payout destination at a coldkey you generated yourself. That needs no
  signature and no linking -- a payout destination is only a destination.

CLI token
  {c.bearer_token}

  Left unused it expires {session_days} days from issue. Once you start using it, each
  request resets the window to the validator's CLI session length (14 days by default),
  and it stops resetting 90 days after issue. You do not have to ask for another: the
  coldkey above is linked, so it can mint its own replacement through the CLI sign-in --
  POST /v1/auth/cli/challenge, sign the message it returns, POST /v1/auth/cli/verify.

WHAT TO DO WITH IT

  export API={api}
  export TOKEN={c.bearer_token}

  # your account, and the credit balance
  curl -sS $API/v1/me         -H "Authorization: Bearer $TOKEN"
  curl -sS $API/v1/me/credits -H "Authorization: Bearer $TOKEN"

  # pick something to prove
  curl -sS "$API/v1/tasks?limit=5"

  # hold one credit against a task
  curl -sS -X POST $API/v1/submissions/intents \\
       -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \\
       -d "{{\\"task_id\\":\\"<task_id>\\",\\"task_bundle_sha256\\":\\"<bundle_digest>\\"}}"
  #    No key in the body: the signer is your account's designated submission coldkey.

  # upload the bundle; the response carries the digest to sign
  curl -sS -X PUT $API/v1/submissions/intents/<intent_id>/bundle \\
       -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/octet-stream' \\
       --data-binary @bundle.zip

  # spend the credit: sign that digest with the coldkey above
  curl -sS -X POST $API/v1/submissions/intents/<intent_id>/confirm \\
       -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \\
       -d '{{"signature": "0x<sr25519 over the request digest>"}}'

The signature in the last step is checked for real against your coldkey -- it is the one
part of this nobody can do for you. POST /v1/submissions/preflight spends no credit, so
use it to shake out bundle policy errors before you hold one.

WHAT THE TOKEN CANNOT DO

It is a CLI session, so four writes are out of its reach and answer BROWSER_SESSION_REQUIRED:
linking another coldkey, setting the payout destination, editing the profile, and claiming a
deposit. Those need a browser sign-in -- the link at the top of this file -- deliberately: a
CLI token is a long-lived file, and it must not be enough to change where an account's money
goes. Reads through the token are also redacted: no email, no payout destination, and only
this one coldkey. `"email": null` from /v1/me is that working, not a broken account.

Signing in through the link does not disturb this token. A sign-in retires earlier *browser*
sessions only; CLI tokens are deliberately outside that scope, so visiting the website never
kills the credential a long-running job is using.

If the token leaks, say so and it will be revoked; the coldkey is what your work is
attributed to and where it is paid, so losing that mnemonic loses both.

Reference: docs/MINER.md and docs/ACCOUNT_API.md in the validator repository.
"""


# --- Writing it out ---------------------------------------------------------------------


def write_batch(
    out_dir: Path,
    contributors: list[Contributor],
    *,
    batch: str,
    credits: int,
    credit_price_rao: int,
    session_days: int,
    link_hours: int,
    api: str,
    website: str,
    generated_at: str,
) -> list[Path]:
    # 0700 before anything lands in it, not after: a handout must never be world-readable,
    # not even for the moment between the write and the chmod.
    out_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(out_dir, 0o700)

    written: list[Path] = []

    sql_path = out_dir / "grant.sql"
    sql_path.write_text(
        render_sql(
            contributors,
            batch=batch,
            credits=credits,
            credit_price_rao=credit_price_rao,
            session_days=session_days,
            link_hours=link_hours,
            generated_at=generated_at,
        ),
        encoding="utf-8",
    )
    written.append(sql_path)

    for c in contributors:
        path = out_dir / f"{c.label}.txt"
        # Created 0600 by hand rather than written and chmod'ed, for the same reason as the
        # directory: there must be no window in which the mnemonic is readable by others.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(
                render_handout(
                    c,
                    batch=batch,
                    credits=credits,
                    session_days=session_days,
                    link_hours=link_hours,
                    api=api,
                    website=website,
                    generated_at=generated_at,
                )
            )
        written.append(path)

    csv_path = out_dir / "accounts.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "label",
                "email",
                "coldkey",
                "coldkey",
                "credits",
                "token_sha256",
                # Not a secret, and the handle for the one write an operator is likely to
                # need in a hurry: expiring a link that went to the wrong address.
                "link_sha256",
                "batch",
            ]
        )
        for c in contributors:
            writer.writerow(
                [
                    c.label,
                    c.email or "",
                    c.coldkey,
                    c.coldkey or "",
                    credits,
                    c.token_sha256_hex,
                    c.link_sha256_hex or "",
                    batch,
                ]
            )
    written.append(csv_path)

    return written


# --- CLI --------------------------------------------------------------------------------


def _positive(name: str, value: int) -> int:
    if value <= 0:
        raise UsageError(f"--{name} must be positive, got {value}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "emails",
        nargs="*",
        metavar="EMAIL",
        help="one contributor per address; the account is reachable by magic link",
    )
    parser.add_argument(
        "--anonymous",
        type=int,
        default=0,
        metavar="N",
        help="additionally create N accounts with no email and no way to sign in to the "
        "website (they can submit, but their payout destination can never be set)",
    )
    parser.add_argument(
        "--credits",
        type=int,
        default=DEFAULT_CREDITS,
        help=f"credits per account, one per verification attempt (default {DEFAULT_CREDITS})",
    )
    parser.add_argument(
        "--credit-price-rao",
        type=int,
        default=int(
            os.environ.get("CREDIT_PRICE_RAO")
            or os.environ.get("PAYMENT_AMOUNT_RAO")
            or DEFAULT_CREDIT_PRICE_RAO
        ),
        help="rao per credit. MUST equal the API's PAYMENT_AMOUNT_RAO, which is what "
        "divides the balance to get a credit count; a mismatch quietly buys fewer "
        "credits than the ledger paid for. Defaults to $CREDIT_PRICE_RAO, "
        f"$PAYMENT_AMOUNT_RAO, then {DEFAULT_CREDIT_PRICE_RAO}",
    )
    parser.add_argument(
        "--session-days",
        type=int,
        default=DEFAULT_SESSION_DAYS,
        help=f"CLI token lifetime (default {DEFAULT_SESSION_DAYS}). The API applies "
        f"CLI_SESSION_MAX_DAYS (default {CLI_SESSION_MAX_DAYS_DEFAULT}) as an absolute "
        f"ceiling from issue, so a longer value here is shortened on first use",
    )
    parser.add_argument(
        "--api",
        default=os.environ.get("CONJECTURES_API", DEFAULT_API),
        help=f"API base URL written into the handouts (default {DEFAULT_API})",
    )
    parser.add_argument(
        "--website",
        default=os.environ.get("WEBSITE_BASE_URL", DEFAULT_WEBSITE),
        help="website base URL the sign-in links point at. Must match the deployment's "
        f"WEBSITE_BASE_URL or the links 404 (default {DEFAULT_WEBSITE})",
    )
    parser.add_argument(
        "--link-hours",
        type=int,
        default=DEFAULT_LINK_HOURS,
        help=f"how long a pre-minted sign-in link stays valid (default "
        f"{DEFAULT_LINK_HOURS}). Single use either way. The API's own links last "
        f"EMAIL_LINK_MINUTES, 15 by default and 1440 at most",
    )
    parser.add_argument(
        "--no-links",
        action="store_true",
        help="do not pre-mint sign-in links. Contributors then need "
        "/v1/auth/email/request-link and a working mail transport to reach the website",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="where to write. Default scripts/generated/pr-accounts-<batch>, which "
        ".gitignore already covers",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        if not args.emails and args.anonymous <= 0:
            raise UsageError(
                "nothing to create: pass at least one email address, or --anonymous N"
            )
        if args.anonymous < 0:
            raise UsageError(f"--anonymous cannot be negative, got {args.anonymous}")
        _positive("credits", args.credits)
        _positive("credit-price-rao", args.credit_price_rao)
        _positive("session-days", args.session_days)
        _positive("link-hours", args.link_hours)
        if not args.website.startswith(("http://", "https://")):
            # The same check `settings.py` makes on WEBSITE_BASE_URL. A bare hostname here
            # produces a link that is relative nonsense in an email client.
            raise UsageError(
                f"--website must be an absolute http(s) URL, got {args.website!r}"
            )
        if args.link_hours > API_MAX_EMAIL_LINK_HOURS:
            print(
                f"warning: --link-hours {args.link_hours} exceeds the longest link the API "
                f"can be configured to mail ({API_MAX_EMAIL_LINK_HOURS}h, from "
                f"EMAIL_LINK_MINUTES maximum=1440). These are single-use, but they are "
                f"sign-in credentials sitting in a file for that whole window",
                file=sys.stderr,
            )
        if args.session_days > CLI_SESSION_MAX_DAYS_DEFAULT:
            # A warning and not an error: the deployment's CLI_SESSION_MAX_DAYS may well be
            # higher than the default, and this script cannot see it. What it must not do is
            # print a lifetime into a handout that the first request quietly shortens.
            print(
                f"warning: --session-days {args.session_days} exceeds the default "
                f"CLI_SESSION_MAX_DAYS ({CLI_SESSION_MAX_DAYS_DEFAULT}); unless the "
                f"deployment raised it, `touch_session` will shorten these tokens to "
                f"{CLI_SESSION_MAX_DAYS_DEFAULT} days on first use",
                file=sys.stderr,
            )

        now = dt.datetime.now(dt.UTC)
        batch = now.strftime("%Y%m%d-%H%M%S")
        generated_at = now.strftime("%Y-%m-%d %H:%M:%S UTC")

        default_dir = ROOT / "scripts" / "generated" / f"pr-accounts-{batch}"
        out_dir = args.out_dir or default_dir
        # Never into a directory that already holds a batch: the handouts are the only copy
        # of their keys, and overwriting them orphans accounts nobody can use again.
        if out_dir.exists() and any(out_dir.iterdir()):
            raise UsageError(f"{out_dir} is not empty; pass an empty --out-dir")

        contributors = plan(
            args.emails,
            args.anonymous,
            batch,
            with_links=not args.no_links,
        )

        written = write_batch(
            out_dir,
            contributors,
            batch=batch,
            credits=args.credits,
            credit_price_rao=args.credit_price_rao,
            session_days=args.session_days,
            link_hours=args.link_hours,
            api=args.api,
            website=args.website,
            generated_at=generated_at,
        )
    except UsageError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    total = args.credits * len(contributors)
    print(
        f"batch {batch}: {len(contributors)} account(s), {args.credits} credit(s) each"
    )
    for c in contributors:
        keys = c.coldkey
        link = "link" if c.link_token else "no link"
        print(f"  {c.label:<24} {c.email or '(no email)':<32} {link:<8} {keys}")
    print()
    linked = sum(1 for c in contributors if c.link_token)
    print(f"  {len(written)} file(s) under {out_dir}")
    print(
        "  handouts are mode 0600 and hold the only copy of each mnemonic, token and link"
    )
    if linked:
        print(
            f"  {linked} sign-in link(s), valid {args.link_hours}h from when the SQL runs, "
            f"pointing at {args.website}"
        )
    print()
    print("nothing has been written to the database yet. To apply:")
    print(f'  psql "$DATABASE_URL" -v i_am_sure=1 -f {out_dir / "grant.sql"}')
    print()
    print(
        f"that grants {total} credit(s) of verification "
        f"({total * args.credit_price_rao} rao) with no payment behind it"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
