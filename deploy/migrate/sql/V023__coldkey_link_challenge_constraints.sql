-- COLDKEY_LINK is a signature flow and therefore requires both the address and the exact message
-- that was signed. It also attaches a credential to an existing account, so its challenge must
-- be account-bound just like HOTKEY_LINK.

ALTER TABLE login_challenges
    DROP CONSTRAINT challenge_wallet_present;

ALTER TABLE login_challenges
    ADD CONSTRAINT challenge_wallet_present
        CHECK (kind NOT IN ('WALLET', 'HOTKEY_LINK', 'HOTKEY_SESSION', 'COLDKEY_LINK')
               OR (ss58 IS NOT NULL AND message IS NOT NULL));

ALTER TABLE login_challenges
    DROP CONSTRAINT challenge_link_has_account;

ALTER TABLE login_challenges
    ADD CONSTRAINT challenge_link_has_account
        CHECK (kind NOT IN ('HOTKEY_LINK', 'COLDKEY_LINK') OR account_id IS NOT NULL);
