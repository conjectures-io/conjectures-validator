-- A signed-in account may prove control of another coldkey and attach it as an additional
-- login credential. This is distinct from WALLET so a link signature cannot be replayed as a
-- sign-in signature, and distinct from HOTKEY_LINK because coldkeys and hotkeys grant different
-- capabilities.
--
-- The enum addition is intentionally isolated. PostgreSQL does not permit a new enum value to
-- be referenced by a CHECK constraint until the transaction that added it has committed, so the
-- corresponding constraints are updated by V023.

ALTER TYPE login_challenge_kind ADD VALUE 'COLDKEY_LINK';
