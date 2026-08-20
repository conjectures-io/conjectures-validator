-- A buyer may pay in any currency TMC PAY accepts, not TAO alone.
--
-- The price does not move: a purchase is `credits * credit_price_rao` of TAO, converted to fiat at
-- the current rate, and that fiat figure is what TMC PAY is asked for. TMC PAY then converts it to
-- the chosen currency at its own rate. So what changes here is what arrives, never what a credit
-- costs.
--
-- Three columns and two constraint changes:
--
--   * `crypto_amount`, `crypto_currency`, `crypto_network` record what the invoice was actually
--     denominated in. The amount is the verbatim decimal string, because a BTC amount has eight
--     decimal places and an ETH amount eighteen, and neither is a rao count.
--
--   * `tmc_pay_invoice_covers_the_credits` compared `crypto_amount_rao` against the credit price.
--     That comparison is only meaningful for TAO, whose smallest unit *is* rao. It is left exactly
--     as it was and paired with `tmc_pay_rao_is_tao_only`, which keeps a non-TAO row from carrying
--     a rao figure at all -- so the covering rule still applies to every row that has one, and no
--     row can smuggle an unchecked amount past it by naming another currency.
--
--   * `tmc_pay_credited_needs_an_invoice` required a rao amount before an order could be credited.
--     A non-TAO order has none, so it now requires either a rao amount or an explicitly non-TAO
--     currency. What it still refuses is the case it was written for: a credited order with no
--     invoice behind it.
--
-- Existing rows are all TAO. Backfilling the currency makes that explicit rather than leaving it
-- to be inferred from a NULL, so `paid_rao` reads the same answer for an old row as for a new one.

ALTER TABLE tmc_pay_orders
    ADD COLUMN crypto_amount TEXT
        CONSTRAINT tmc_pay_crypto_amount_length
            CHECK (crypto_amount IS NULL OR length(crypto_amount) BETWEEN 1 AND 64),
    ADD COLUMN crypto_currency TEXT
        CONSTRAINT tmc_pay_crypto_currency_length
            CHECK (crypto_currency IS NULL OR length(crypto_currency) BETWEEN 1 AND 16),
    ADD COLUMN crypto_network TEXT
        CONSTRAINT tmc_pay_crypto_network_length
            CHECK (crypto_network IS NULL OR length(crypto_network) BETWEEN 1 AND 32);

UPDATE tmc_pay_orders
   SET crypto_currency = 'TAO',
       crypto_network  = 'bittensor'
 WHERE invoice_id IS NOT NULL
   AND crypto_currency IS NULL;

ALTER TABLE tmc_pay_orders
    ADD CONSTRAINT tmc_pay_rao_is_tao_only
        CHECK (crypto_currency IS NULL
               OR crypto_currency = 'TAO'
               OR crypto_amount_rao IS NULL);

ALTER TABLE tmc_pay_orders
    DROP CONSTRAINT tmc_pay_credited_needs_an_invoice;

ALTER TABLE tmc_pay_orders
    ADD CONSTRAINT tmc_pay_credited_needs_an_invoice
        CHECK (credited_ledger_id IS NULL
               OR (invoice_id IS NOT NULL
                   AND (crypto_amount_rao IS NOT NULL
                        OR (crypto_currency IS NOT NULL AND crypto_currency <> 'TAO'))));


-- `tmc_pay_invoiced_rows_are_complete` required a rao amount on every invoiced row. Its intent is
-- that a buyer can act on the invoice, and for a non-TAO one what they need is `crypto_amount`.
-- So the amount is still required, in whichever column holds it.

ALTER TABLE tmc_pay_orders
    DROP CONSTRAINT tmc_pay_invoiced_rows_are_complete;

ALTER TABLE tmc_pay_orders
    ADD CONSTRAINT tmc_pay_invoiced_rows_are_complete
        CHECK (status IN ('NEW', 'FAILED')
               OR (invoice_id IS NOT NULL
                   AND (crypto_amount_rao IS NOT NULL OR crypto_amount IS NOT NULL)
                   AND deposit_address IS NOT NULL
                   AND fiat_amount IS NOT NULL
                   AND fiat_currency IS NOT NULL));
