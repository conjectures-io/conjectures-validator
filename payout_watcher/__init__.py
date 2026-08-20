"""Read-only Subtensor event watcher for outbound bounty settlement.

This process holds no wallet and signs nothing.  Human multisig operators still execute payout
calls; the watcher only makes the site's Paying/Paid projection follow best/finalized chain facts.
"""

