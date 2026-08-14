-- Stable external identities for Google sign-in.
--
-- The provider subject, not email, is the login key. Email can change, and for consumer Google
-- accounts created with a non-Gmail mailbox it does not prove current control of that mailbox.
-- One provider identity belongs to one account, and one account may attach at most one identity
-- from a provider. The application currently permits only Google, but keeping provider explicit
-- prevents a future identity type from being confused with Google's subject namespace.

CREATE TABLE account_identities (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id      UUID NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    provider        TEXT NOT NULL,
    subject         TEXT NOT NULL,
    email           TEXT NOT NULL,
    linked_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT account_identity_provider_known CHECK (provider = 'google'),
    CONSTRAINT account_identity_subject_length CHECK (length(subject) BETWEEN 1 AND 255),
    CONSTRAINT account_identity_email_shape
        CHECK (email ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$'),
    CONSTRAINT account_identity_used_after_link CHECK (last_used_at >= linked_at),
    CONSTRAINT account_identities_provider_subject_key UNIQUE (provider, subject),
    CONSTRAINT account_identities_account_provider_key UNIQUE (account_id, provider)
);

CREATE INDEX account_identities_account_idx
    ON account_identities (account_id, linked_at);
