-- Email/password registration and password reset, as two new challenge kinds.
--
-- PASSWORD_SIGNUP is a registration that has not been confirmed yet: it carries the address and
-- the already-hashed password, and no `accounts` row exists until the link in the mail is
-- followed. That ordering is the whole point of a separate kind. An account created before its
-- address is proved would be a squatting primitive — register someone else's address and they
-- can never sign up, link Google, or be found by their own mailbox — and it would also make
-- `accounts.email_verified` mean "someone typed this" rather than "this mailbox answered".
--
-- PASSWORD_RESET authorises setting a new password on an account that already exists, so unlike
-- signup it is account-bound. It is distinct from EMAIL because the two grant different things:
-- an EMAIL token is a sign-in, and a token that could be used as either would mean every
-- password reset link is also a live session credential, and every sign-in link a way to
-- change the password.
--
-- The enum addition is intentionally isolated. PostgreSQL does not permit a new enum value to
-- be referenced by a CHECK constraint until the transaction that added it has committed, so the
-- corresponding columns and constraints are added by V037. Same reason as V022/V023.

ALTER TYPE login_challenge_kind ADD VALUE 'PASSWORD_SIGNUP';
ALTER TYPE login_challenge_kind ADD VALUE 'PASSWORD_RESET';
