-- TMC PAY's published invoice lifecycle includes `cancelled`, which this enum was written
-- without: a merchant can cancel an invoice from the dashboard, and a buyer can cancel from the
-- hosted payment page, so the status arrives on a webhook for an order this deployment owns.
--
-- Until now that status was refused during parsing, which turned an ordinary cancellation into a
-- rejected webhook TMC PAY would keep retrying. Terminal and unpaid, like EXPIRED.
--
-- The enum addition is intentionally isolated, matching V022. PostgreSQL will not let a value
-- added in one transaction be referenced by the same one, so anything that has to name CANCELLED
-- belongs in a later migration rather than here.

-- Appended rather than positioned. scripts/check_schema_drift.py compares this file's enum
-- against the ORM mirror including sort order, and ADD VALUE ... BEFORE produces a fractional
-- position that Base.metadata.create_all cannot reproduce. So the value goes last here and last
-- in models.py, and the enum's declaration order stops matching the lifecycle's order.
ALTER TYPE tmc_pay_order_state ADD VALUE IF NOT EXISTS 'CANCELLED';
