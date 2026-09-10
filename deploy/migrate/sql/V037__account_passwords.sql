-- Passwords on accounts, and the pending-registration column the new challenge kinds need.
--
-- `password_hash` is a self-describing string, not a bare digest: `scrypt$<cost>$<r>$<p>$<salt>
-- $<key>`, all base64. A row has to record the parameters it was derived with to be verifiable
-- at all, and recording them is also what makes raising the cost a non-event — the sign-in path
-- rehashes an account whose stored parameters are weaker than the configured ones, so no
-- migration ever has to touch this column. The format is owned by submission_api/passwords.py.
--
-- TEXT rather than BYTEA for the same reason: the value is a structured encoding, and half of
-- it is parameters rather than key material.
--
-- `failed_password_attempts` and `password_throttled_until` bound online guessing. A
-- memory-hard KDF makes each guess cost ~140ms, which is a great deal against a stolen database
-- and not nearly enough against an attacker patiently trying a wordlist over HTTP. This pauses
-- the *password method* for an account, and nothing else: magic link, Google and coldkey
-- sign-in all keep working while it is set. That asymmetry is deliberate. A true lockout would
-- hand anyone who knows an address the ability to lock its owner out; pausing one of four
-- doors costs an attacker's victim a few minutes on one button.

ALTER TABLE accounts
    ADD COLUMN password_hash             TEXT,
    ADD COLUMN password_updated_at       TIMESTAMPTZ,
    ADD COLUMN failed_password_attempts  SMALLINT NOT NULL DEFAULT 0,
    ADD COLUMN password_throttled_until  TIMESTAMPTZ;

ALTER TABLE accounts
    ADD CONSTRAINT password_hash_is_dated
        CHECK ((password_hash IS NULL) = (password_updated_at IS NULL));

-- A password may only exist on an account that has an address to reset it through. Without
-- this, a wallet-only account could acquire a password it has no way to recover, and the
-- account would be one forgotten passphrase away from unreachable.
ALTER TABLE accounts
    ADD CONSTRAINT password_requires_an_email
        CHECK (password_hash IS NULL OR email IS NOT NULL);

ALTER TABLE accounts
    ADD CONSTRAINT password_attempts_not_negative
        CHECK (failed_password_attempts >= 0);

-- The pending registration's hash. Always a finished scrypt hash, never a password: this table
-- is not a place a plaintext credential is permitted to rest, however briefly. The equality
-- form of the CHECK enforces both halves at once — a signup challenge must carry one, and no
-- other kind may.
ALTER TABLE login_challenges
    ADD COLUMN password_hash TEXT;

ALTER TABLE login_challenges
    ADD CONSTRAINT challenge_password_hash_is_signup_only
        CHECK ((kind = 'PASSWORD_SIGNUP') = (password_hash IS NOT NULL));

-- Both new kinds address a mailbox, so both need the address the existing EMAIL kind needed.
ALTER TABLE login_challenges
    DROP CONSTRAINT challenge_email_present;

ALTER TABLE login_challenges
    ADD CONSTRAINT challenge_email_present
        CHECK (kind NOT IN ('EMAIL', 'PASSWORD_SIGNUP', 'PASSWORD_RESET')
               OR email IS NOT NULL);

-- A reset changes an existing account's credential, so its challenge names that account. Signup
-- deliberately does not: there is no account yet, which is what makes it safe to mail.
ALTER TABLE login_challenges
    ADD CONSTRAINT challenge_reset_has_account
        CHECK (kind <> 'PASSWORD_RESET' OR account_id IS NOT NULL);
