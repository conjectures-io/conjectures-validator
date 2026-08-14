-- Buying credits through TMC PAY: a processor-settled funding path beside the direct transfer.
--
-- V003 modelled one way to fund an account: declare a deposit, send TAO to the treasury, and let
-- the deposit watcher credit the transfer it read off finalized chain state. That path is
-- chain-verified end to end, and `deposits` encodes it — `treasury_address` is NOT NULL, and a
-- CREDITED row must carry an extrinsic reference, an observed amount and a block.
--
-- TMC PAY does not fit inside those columns, and must not be made to. The buyer sends TAO to an
-- address TMC PAY derives from its own HD wallet, TMC PAY confirms it, and the funds reach the
-- treasury later as a batched payout net of commission. So for a TMC PAY purchase there is no
-- extrinsic of ours to point at, no transfer to our own address, and no block in which our money
-- moved. Widening `deposits` to admit that would mean making its finality columns nullable, which
-- would weaken the one table whose whole job is to say "this rao was seen on chain".
--
-- Hence a separate table with its own evidence, and one deliberate consequence: a DEPOSIT entry in
-- `credit_ledger` now names EITHER a chain deposit OR a TMC PAY order, exactly one of the two.
-- `ledger_deposit_names_its_deposit` is replaced by an exclusive-or below, so an auditor can
-- separate chain-confirmed rao from processor-confirmed rao with a WHERE clause and neither kind
-- can be recorded without saying which it is.
--
-- Keep colon-prefixed words out of these comments. scripts/check_schema_drift.py feeds each
-- migration through SQLAlchemy's text() construct, which treats a colon followed by an identifier
-- as a bind parameter even inside a SQL comment.


CREATE TYPE tmc_pay_order_state AS ENUM (
    -- Ours, both of them, and both transient or terminal rather than states TMC PAY reports.
    'NEW',              -- the row exists; the invoice has not been created yet
    'FAILED',           -- the invoice could not be created, or could not be quoted at all

    -- TMC PAY's invoice lifecycle, label for label. Kept identical on purpose: a status this
    -- validator invented would be a mapping to maintain, and a mapping is a place for the two
    -- systems to disagree about whether money arrived.
    'CREATED',          -- invoice exists, no deposit seen
    'PENDING',          -- a deposit is visible but below the confirmation target
    'CONFIRMING',       -- confirmations accumulating
    'UNDERPAID',        -- confirmed below the invoice amount; non-terminal, the buyer may top up
    'CONFIRMED',        -- paid, confirmed, amount matches
    'OVERPAID',         -- paid more than the invoice; terminal
    'EXPIRED',          -- the TTL elapsed with no confirming payment
    'LATE_PAYMENT'      -- confirmed after expiry; exceptional, reconciled by hand
);


CREATE TABLE tmc_pay_orders (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id              UUID NOT NULL REFERENCES accounts (id),

    -- What is being bought, and at what price. `credits` is the paid credit count the buyer asked
    -- for; the rao it costs is `credits * credit_price_rao` and is deliberately NOT stored, so
    -- there is no second number that can disagree with the first two.
    credits                 INTEGER NOT NULL
        CONSTRAINT tmc_pay_credits_positive CHECK (credits > 0),
    credit_price_rao        BIGINT NOT NULL
        CONSTRAINT tmc_pay_price_positive CHECK (credit_price_rao > 0),

    status                  tmc_pay_order_state NOT NULL DEFAULT 'NEW',

    -- The idempotency key sent to TMC PAY as `external_id`. Minted here, before the invoice is
    -- created, which is what makes a lost create-response recoverable: repeating the create with
    -- the same key returns the original invoice instead of making a second one, and a webhook for
    -- an invoice whose id we never learned still carries this value and can be matched on it.
    external_id             TEXT NOT NULL
        CONSTRAINT tmc_pay_external_id_length CHECK (length(external_id) BETWEEN 1 AND 128),

    -- Everything below is TMC PAY's answer, so all of it is NULL in state NEW.
    invoice_id              TEXT
        CONSTRAINT tmc_pay_invoice_id_length CHECK (invoice_id IS NULL OR length(invoice_id) BETWEEN 1 AND 64),
    merchant_id             TEXT
        CONSTRAINT tmc_pay_merchant_id_length CHECK (merchant_id IS NULL OR length(merchant_id) BETWEEN 1 AND 64),

    -- The fiat side of the quote, verbatim as strings. They are the buyer's receipt and an
    -- operator's join key against the TMC PAY dashboard, never arithmetic input again — and a
    -- price that becomes a float is a price that has been rounded by something other than policy.
    fiat_amount             TEXT
        CONSTRAINT tmc_pay_fiat_amount_length CHECK (fiat_amount IS NULL OR length(fiat_amount) BETWEEN 1 AND 64),
    fiat_currency           TEXT
        CONSTRAINT tmc_pay_fiat_currency_shape CHECK (fiat_currency IS NULL OR fiat_currency ~ '^[A-Z]{3}$'),
    exchange_rate           TEXT
        CONSTRAINT tmc_pay_exchange_rate_length CHECK (exchange_rate IS NULL OR length(exchange_rate) BETWEEN 1 AND 64),
    commission_amount       TEXT
        CONSTRAINT tmc_pay_commission_length CHECK (commission_amount IS NULL OR length(commission_amount) BETWEEN 1 AND 64),

    -- The crypto side, in integer rao, and the only amount anything is allowed to credit. TMC PAY
    -- locks it at invoice creation and requires exactly it before reporting `confirmed`, so it is
    -- both what the buyer sends and what arrives. A webhook body is never read for an amount.
    crypto_amount_rao       BIGINT
        CONSTRAINT tmc_pay_crypto_amount_positive CHECK (crypto_amount_rao IS NULL OR crypto_amount_rao > 0),
    -- Where the buyer sends it. TMC PAY's, derived per invoice from its own HD wallet — never the
    -- treasury, which is the whole reason this table exists.
    deposit_address         ss58,

    -- The ledger entry this order produced. One order credits at most once, and the UNIQUE is what
    -- makes a duplicate webhook and a concurrent reconciler collide instead of crediting twice.
    credited_ledger_id      BIGINT UNIQUE REFERENCES credit_ledger (id),

    -- Set when money is involved but the outcome is not something to settle automatically:
    -- overpaid, underpaid, or a late payment. An operator's queue, and the reason it is a flag
    -- rather than a status is that the status still has to say what TMC PAY reported.
    needs_review            BOOLEAN NOT NULL DEFAULT false,
    failure_reason          TEXT,

    -- The `X-Webhook-ID` of the last delivery applied to this row, and when TMC PAY was last read
    -- back directly. Together they answer "why does this row say what it says": a webhook, or a
    -- poll, and which one.
    last_event_id           TEXT
        CONSTRAINT tmc_pay_last_event_length CHECK (last_event_id IS NULL OR length(last_event_id) BETWEEN 1 AND 64),
    last_polled_at          TIMESTAMPTZ,

    invoice_expires_at      TIMESTAMPTZ,
    confirmed_at            TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Once an invoice exists, the four values a buyer needs in order to pay it exist too. Written
    -- as one constraint over the states that imply an invoice, so a partially-applied update
    -- cannot leave a row that says "pay this" without saying what or where.
    CONSTRAINT tmc_pay_invoiced_rows_are_complete
        CHECK (status IN ('NEW', 'FAILED')
               OR (invoice_id IS NOT NULL
                   AND crypto_amount_rao IS NOT NULL
                   AND deposit_address IS NOT NULL
                   AND fiat_amount IS NOT NULL
                   AND fiat_currency IS NOT NULL)),
    -- The invoice must be worth at least the credits it is selling. Checked here as well as in the
    -- application because it is the one property that makes crediting the locked amount safe: if it
    -- holds, then floor(crypto_amount_rao / credit_price_rao) is at least `credits`, and the buyer
    -- cannot end up with fewer credits than they paid for.
    CONSTRAINT tmc_pay_invoice_covers_the_credits
        CHECK (crypto_amount_rao IS NULL OR crypto_amount_rao >= credits * credit_price_rao),
    CONSTRAINT tmc_pay_credited_needs_an_invoice
        CHECK (credited_ledger_id IS NULL
               OR (invoice_id IS NOT NULL AND crypto_amount_rao IS NOT NULL)),
    CONSTRAINT tmc_pay_failed_needs_a_reason
        CHECK (status <> 'FAILED' OR failure_reason IS NOT NULL),
    CONSTRAINT tmc_pay_orders_updated_not_before_created CHECK (updated_at >= created_at)
);

-- The idempotency key, globally. One merchant account serves this whole deployment, so TMC PAY's
-- own `(merchant_id, external_id)` uniqueness and this index agree on what a duplicate is.
CREATE UNIQUE INDEX tmc_pay_orders_external_idx ON tmc_pay_orders (external_id);
-- One row per invoice. Partial, so the NEW rows that have no invoice id yet do not collide on NULL.
CREATE UNIQUE INDEX tmc_pay_orders_invoice_idx ON tmc_pay_orders (invoice_id)
    WHERE invoice_id IS NOT NULL;
CREATE INDEX tmc_pay_orders_account_idx ON tmc_pay_orders (account_id, created_at DESC);
-- The reconciler's queue: every order that could still change. UNDERPAID is in the list because
-- TMC PAY documents it as non-terminal — the buyer may top up — and NEW is in it because an order
-- stuck there means a create whose response was lost.
CREATE INDEX tmc_pay_orders_open_idx ON tmc_pay_orders (created_at)
    WHERE status IN ('NEW', 'CREATED', 'PENDING', 'CONFIRMING', 'UNDERPAID');
-- An operator's queue, kept small by the partial predicate.
CREATE INDEX tmc_pay_orders_review_idx ON tmc_pay_orders (created_at)
    WHERE needs_review;
-- "The most recent rate TMC PAY locked, for this currency." Every invoice reports the rate it used,
-- and that is the best available seed for pricing the next one — same rate source, and already in
-- the merchant's own currency. This index is what makes reading it back one row rather than a scan
-- over every order ever placed.
CREATE INDEX tmc_pay_orders_rate_idx ON tmc_pay_orders (fiat_currency, created_at DESC)
    WHERE exchange_rate IS NOT NULL;


-- Every delivery TMC PAY has made, by its own `X-Webhook-ID`. Two jobs:
--
--   * deduplication, as the primary key. TMC PAY retries reuse the id, and the production
--     checklist asks for exactly this; a repeat is recognised by an insert that conflicts rather
--     than by re-deriving whether the event was already applied.
--   * an audit trail an operator can read against the dashboard's own Webhook events tab.
--
-- `order_id` is NULLable because a delivery can arrive for an invoice this deployment has no row
-- for — a foreign merchant's webhook pointed here, or an order whose create response was lost. The
-- delivery is still recorded, which is what makes that case investigable instead of invisible.
CREATE TABLE tmc_pay_webhook_deliveries (
    webhook_id      TEXT PRIMARY KEY
        CONSTRAINT tmc_pay_delivery_id_length CHECK (length(webhook_id) BETWEEN 1 AND 64),
    order_id        UUID REFERENCES tmc_pay_orders (id),
    invoice_id      TEXT
        CONSTRAINT tmc_pay_delivery_invoice_length CHECK (invoice_id IS NULL OR length(invoice_id) BETWEEN 1 AND 64),
    event           TEXT
        CONSTRAINT tmc_pay_delivery_event_length CHECK (event IS NULL OR length(event) BETWEEN 1 AND 64),
    status          TEXT
        CONSTRAINT tmc_pay_delivery_status_length CHECK (status IS NULL OR length(status) BETWEEN 1 AND 32),
    -- What this delivery caused here, in one word: 'CREDITED', 'RECORDED', 'IGNORED', 'UNKNOWN'.
    -- Text rather than an enum: it is an observability field, and a new outcome should not need a
    -- migration to be writable.
    outcome         TEXT NOT NULL
        CONSTRAINT tmc_pay_delivery_outcome_length CHECK (length(outcome) BETWEEN 1 AND 32),
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX tmc_pay_webhook_deliveries_order_idx
    ON tmc_pay_webhook_deliveries (order_id, received_at DESC);


-- The ledger's side of it: a DEPOSIT now names one of two sources, and says which.
-- Named explicitly, the way V003 names `credit_ledger_deposit_fkey`: the ORM mirror declares the
-- same name, and scripts/check_schema_drift.py compares constraint names.
ALTER TABLE credit_ledger
    ADD COLUMN tmc_pay_order_id UUID
        CONSTRAINT credit_ledger_tmc_pay_order_fkey REFERENCES tmc_pay_orders (id);

-- Replaced rather than added alongside, because two overlapping constraints on the same column
-- would each have to be read to know what is required. The new one is strictly stronger: it still
-- forbids a DEPOSIT with no source, and it additionally forbids one claiming both.
ALTER TABLE credit_ledger
    DROP CONSTRAINT ledger_deposit_names_its_deposit;

ALTER TABLE credit_ledger
    ADD CONSTRAINT ledger_deposit_names_its_deposit
        CHECK (kind <> 'DEPOSIT'
               OR ((deposit_id IS NOT NULL) <> (tmc_pay_order_id IS NOT NULL)));

-- One credit entry per order, mirroring `credit_ledger_spend_idx` for intents. Belt and braces
-- with `tmc_pay_orders.credited_ledger_id UNIQUE`: that one stops the order pointing at two
-- entries, this one stops two entries pointing at the order.
CREATE UNIQUE INDEX credit_ledger_tmc_pay_idx ON credit_ledger (tmc_pay_order_id)
    WHERE tmc_pay_order_id IS NOT NULL;


CREATE FUNCTION tmc_pay_orders_touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER tmc_pay_orders_touch_updated_at
    BEFORE UPDATE ON tmc_pay_orders
    FOR EACH ROW EXECUTE FUNCTION tmc_pay_orders_touch_updated_at();
