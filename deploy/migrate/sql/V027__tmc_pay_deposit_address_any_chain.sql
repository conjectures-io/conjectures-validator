-- The deposit address of a TMC PAY invoice is an address on whatever chain the buyer chose, so it
-- stops being typed as a Bittensor one.
--
-- `deposit_address` has carried the `ss58` domain since V016, when TAO on Bittensor was the only
-- pair this integration could invoice. That domain is `TEXT CHECK (VALUE ~
-- '^[1-9A-HJ-NP-Za-km-z]{48}$')` -- exactly 48 base58 characters, which is a Substrate address and
-- nothing else. V026 then made every currency TMC PAY accepts payable without revisiting this
-- column, so every non-TAO invoice failed on the write that stored its address: an Ethereum
-- address is 42 characters and begins `0x`, a bech32 Bitcoin address is 42 and uses a different
-- alphabet, a Monero address is 95. The check violation surfaced as a 500 from
-- POST /v1/me/credits/tmc-pay/orders, and it was reachable for every pair except TAO.
--
-- The address is not this validator's to constrain by shape. It is minted by the processor on a
-- chain named by the buyer, from a set that changes when TMC PAY adds a network -- so a pattern
-- here would be a third source of truth about address formats, behind TMC PAY's and the chain's,
-- and would go stale exactly the way the `ss58` domain did. What is worth enforcing is that the
-- column holds a bounded string, which is the bound `parse_invoice` already applies when it reads
-- the field: `submission_api.tmc_pay.MAX_DEPOSIT_ADDRESS_LENGTH`. Stated in both places so a
-- disagreement between them is a failed test rather than a 500.
--
-- `ss58` itself stays. Every other column using it -- payout keys, treasury addresses, the chain
-- watcher's senders and recipients -- really is a Bittensor address, and the domain is what keeps
-- those from admitting one that is not.
--
-- Existing rows all satisfy the new check: they are 48 characters, which is inside 1..128. No
-- backfill, and nothing to rewrite -- TEXT and a domain over TEXT have the same on-disk
-- representation, so this is a catalogue change rather than a table rewrite.

ALTER TABLE tmc_pay_orders
    ALTER COLUMN deposit_address TYPE TEXT;

ALTER TABLE tmc_pay_orders
    ADD CONSTRAINT tmc_pay_deposit_address_length
        CHECK (deposit_address IS NULL
               OR length(deposit_address) BETWEEN 1 AND 128);
