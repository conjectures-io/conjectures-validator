-- Where TMC PAY wants the buyer sent to pay, as TMC PAY reported it.
--
-- The payment URL was previously assembled here, as TMC_PAY_HOSTED_BASE_URL plus the invoice id.
-- That cannot be right: TMC PAY's public invoice route is keyed by an opaque `hosted_token`, not
-- by the invoice id, so the constructed link pointed at nothing. `InvoiceResponse` carries the
-- real URL and this is where it is kept.
--
-- Nullable, and no backfill. Rows created before this migration have no URL to recover -- the
-- value only ever arrives on an invoice response -- and the read path falls back to the
-- constructed link for them, which is no worse than what they had.

ALTER TABLE tmc_pay_orders
    ADD COLUMN hosted_invoice_url TEXT
        CONSTRAINT tmc_pay_hosted_invoice_url_length
            CHECK (hosted_invoice_url IS NULL OR length(hosted_invoice_url) BETWEEN 1 AND 2048);
