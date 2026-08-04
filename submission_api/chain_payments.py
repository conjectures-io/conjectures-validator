"""The finalized-transfer reader `payments.ChainPaymentVerifier` needs, backed by Subtensor.

`payments.py` states the contract and, until now, had no implementation of it: `ChainPaymentVerifier`
was constructed with `reader=None` and refused every submission with `503
PAYMENT_VERIFIER_UNAVAILABLE` rather than admitting an unpaid one. This is the reader, and it is the
same one the deposit watcher uses — `conjectures_subnet/transfers.py`, read-only, holding no wallet
keys and signing nothing.

**One reader, one idea of what a transfer is.** Both funding paths resolve a reference through
`conjectures_subnet.transfers`, so `submissions.payment_reference` and
`deposits.extrinsic_reference` are the same string space: `block-extrinsic-event`. That is what
makes it possible to stop one transfer from funding a submission *and* buying a credit — see
`conjectures_subnet.db.transfers.claim_for_submission`. Two readers with two reference formats could
not have been reconciled.

**The canonical form is what gets stored.** A miner may cite `block-extrinsic`, which is what a
block explorer shows. The reader resolves it and returns the three-part identity, so the uniqueness
constraint on `payment_reference` actually prevents reuse instead of being defeated by respelling.

This module deliberately holds no policy. Whether the recipient, the amount and the coldkey are
*right* is `ChainPaymentVerifier`'s decision; this only answers what the chain says.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from conjectures_subnet import transfers as chain
from submission_api.payments import FinalizedTransfer

logger = logging.getLogger("submission_api.chain_payments")


@dataclass
class SubtensorTransferReader:
    """`payments.TransferReader` over finalized Subtensor state.

    Holds the connection open between requests, because the alternative is a websocket handshake
    per submission and the public endpoints answer HTTP 429 to that — the same reason
    `BittensorTransferSource` does. `aclose()` releases it at shutdown.
    """

    source: chain.PaymentSource

    @classmethod
    def for_network(
        cls, network: str, *, archive_network: str | None = None
    ) -> SubtensorTransferReader:
        # Construction is cold: bittensor opens no socket until the first call, so building this at
        # startup cannot fail on a chain that is briefly unreachable.
        return cls(
            source=chain.BittensorTransferSource(
                network, archive_network=archive_network
            )
        )

    async def finalized_transfer(self, *, reference: str) -> FinalizedTransfer | None:
        """The transfer a reference names, if it is finalized. None otherwise.

        None covers "unparseable", "not finalized yet", "no such extrinsic" and "that extrinsic
        moved no TAO" alike. `payments.py` turns any of them into the same refusal, deliberately:
        distinguishing them would turn a payment-gated endpoint into a free chain-query oracle.

        `AmbiguousReference` is allowed to propagate. It is the one failure a client can fix, and
        the message names the exact references to choose between.
        """
        parsed = chain.parse_reference(reference)
        if parsed is None:
            logger.info("payment reference is not resolvable: %r", reference)
            return None
        transfer = await chain.finalized_transfer(self.source, parsed)
        if transfer is None:
            return None
        return FinalizedTransfer(
            # The canonical three-part identity, not what the caller wrote. See the module
            # docstring on why this is the value that gets stored.
            reference=transfer.reference,
            sender=transfer.sender,
            recipient=transfer.recipient,
            amount_rao=transfer.amount_rao,
            block=transfer.block,
            block_timestamp=transfer.block_timestamp,
        )

    async def coldkey_owns_hotkey(self, *, coldkey: str, hotkey: str) -> bool:
        """Whether `coldkey` is the registered owner of `hotkey`.

        False for a hotkey the chain knows no owner for. An unregistered hotkey reads back as the
        zero account rather than as absent, which `conjectures_subnet.transfers.coldkey_of` maps to
        None — so an unregistered hotkey can never be established as owned by anyone.
        """
        owner = await self.source.coldkey_of(hotkey=hotkey)
        if owner is None:
            logger.info("hotkey %s has no registered owner on chain", hotkey)
            return False
        return owner == coldkey

    async def aclose(self) -> None:
        close = getattr(self.source, "close", None)
        if close is not None:
            await close()


__all__ = ["SubtensorTransferReader"]
