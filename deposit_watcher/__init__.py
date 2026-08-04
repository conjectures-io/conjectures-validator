"""The deposit watcher: turn TAO arriving at the treasury into credits.

    python -m deposit_watcher            # poll until stopped
    python -m deposit_watcher --once     # catch up to the finalized head and exit

`docs/ACCOUNT_API.md` calls this "the reconciler" and `submission_api/payments.py` calls it "the
finalized-transfer reader"; V003 built the ledger and the deposit row it writes into, and left this
half unbuilt. It reads `Balances.Transfer` events out of finalized blocks, records every arrival at
the watched address, and credits the ones whose sending coldkey belongs to an account.

**One credit is one submission, at `CREDIT_PRICE_RAO` — 0.5 TAO by default.** The count is not
stored anywhere: the ledger holds the rao that arrived and `conjectures_subnet.db.credits`
divides. That is what makes remainders behave — 0.7 TAO buys one credit and leaves 0.2 towards the
next, rather than discarding it.

* `settings` — fail-closed configuration. The four values that decide whose money this is have no
  defaults at all;
* `watcher` — the loop, and the record-then-attribute-then-advance order that makes it restartable.

The chain-facing and durable halves live outside this package on purpose, because the API needs
them too: `conjectures_subnet.transfers` reads finalized blocks and holds no keys, and
`conjectures_subnet.db.transfers` owns the observed-transfer table and the attribution rules.

TRUST DOMAIN. This process holds database credentials and never compiles a miner's Lean, so it sits
on the same side of the line as the verification worker and the opposite side from the verifier
image. It also signs nothing and holds no wallet keys: it only ever asks the chain what happened.
"""

from __future__ import annotations

from deposit_watcher.settings import SettingsError, WatcherSettings
from deposit_watcher.watcher import DepositWatcher, Scanned

__all__ = [
    "DepositWatcher",
    "Scanned",
    "SettingsError",
    "WatcherSettings",
]
