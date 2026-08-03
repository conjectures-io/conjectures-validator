"""Payment confirmation at intake.

The schema makes payment a precondition, not a state: every payment column on `submissions`
is NOT NULL and there is no payment status, so a row exists only for a transfer the validator
has already confirmed on finalized chain state. A request whose payment cannot be confirmed
creates no submission and is recorded in `api_rejection_log`.

Confirmation is therefore synchronous, and it is a trust-boundary decision: the API process
needs a finalized-chain reader. `docs/SUBNET.md` and `SECURITY.md` require that reader to hold
no wallet keys and to be read-only — it answers "was this transfer finalized", nothing more.
`conjectures_subnet/chain.py` is the read-only starting point.

A verifier must establish all of the following before returning, because nothing downstream
re-checks them:

* the extrinsic is included in a **finalized** block;
* the recipient is the configured payment address;
* the amount is exactly the configured submission price, in integer rao;
* the sender coldkey **owns the submitting hotkey**, so a miner cannot cite someone else's
  transfer; and
* the reference is the canonical extrinsic identity, so the uniqueness constraint on
  `submissions.payment_reference` actually prevents reuse.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from submission_api.errors import PaymentRequired
from submission_api.settings import (
    CHAIN_PAYMENTS,
    DEVELOPMENT_PAYMENTS,
    Settings,
)

REASON_NOT_FINALIZED = "PAYMENT_NOT_FINALIZED"
REASON_UNAVAILABLE = "PAYMENT_VERIFIER_UNAVAILABLE"


@dataclass(frozen=True)
class ConfirmedPayment:
    """A finalized transfer that may fund exactly one submission."""

    reference: str
    sender: str  # coldkey, proven to own the submitting hotkey
    amount_rao: int
    block: int  # the finalized block it was observed in


class PaymentVerifier(Protocol):
    async def confirm(self, *, reference: str, hotkey: str) -> ConfirmedPayment:
        """Return the confirmed transfer, or raise PaymentRequired."""
        ...


@dataclass(frozen=True)
class ChainPaymentVerifier:
    """Confirm a transfer against finalized Subtensor state.

    The finalized-transfer reader is the remaining piece of the payment component
    (`README.md` lists it as to build). Until it is injected this verifier fails closed: it
    refuses every submission rather than admitting an unpaid one, which is the only safe
    default for a component that gates money.
    """

    recipient: str
    amount_rao: int
    reader: TransferReader | None = None

    async def confirm(self, *, reference: str, hotkey: str) -> ConfirmedPayment:
        if self.reader is None:
            raise PaymentRequired(
                "payment confirmation is not available on this deployment",
                reason_code=REASON_UNAVAILABLE,
                status_code=503,
            )
        transfer = await self.reader.finalized_transfer(reference=reference)
        if transfer is None:
            raise PaymentRequired(
                "no finalized transfer found for this payment reference",
                reason_code=REASON_NOT_FINALIZED,
                extra={"payment_reference": reference},
            )
        if transfer.recipient != self.recipient:
            raise PaymentRequired(
                "transfer was not sent to the validator's payment address",
                reason_code=REASON_NOT_FINALIZED,
                extra={"payment_reference": reference},
            )
        if transfer.amount_rao != self.amount_rao:
            raise PaymentRequired(
                "transfer amount does not equal the submission price",
                reason_code=REASON_NOT_FINALIZED,
                extra={
                    "payment_reference": reference,
                    "required_amount_rao": self.amount_rao,
                    "observed_amount_rao": transfer.amount_rao,
                },
            )
        if not await self.reader.coldkey_owns_hotkey(
            coldkey=transfer.sender, hotkey=hotkey
        ):
            raise PaymentRequired(
                "the paying coldkey does not own the submitting hotkey",
                reason_code=REASON_NOT_FINALIZED,
                extra={"payment_reference": reference},
            )
        return ConfirmedPayment(
            reference=reference,
            sender=transfer.sender,
            amount_rao=transfer.amount_rao,
            block=transfer.block,
        )


@dataclass(frozen=True)
class FinalizedTransfer:
    reference: str
    sender: str
    recipient: str
    amount_rao: int
    block: int


class TransferReader(Protocol):
    """Read-only finalized-chain queries. Holds no keys and signs nothing."""

    async def finalized_transfer(
        self, *, reference: str
    ) -> FinalizedTransfer | None: ...

    async def coldkey_owns_hotkey(self, *, coldkey: str, hotkey: str) -> bool: ...


@dataclass(frozen=True)
class DevelopmentPaymentVerifier:
    """Accept configured references without touching a chain. Never permitted in production.

    Local runs and tests need a submission to exist without a real transfer. `Settings`
    refuses this verifier when `APP_MODE=PROD`.
    """

    sender: str
    amount_rao: int
    block: int = 1
    references: tuple[str, ...] = ()

    async def confirm(self, *, reference: str, hotkey: str) -> ConfirmedPayment:
        if self.references and reference not in self.references:
            raise PaymentRequired(
                "payment reference is not in the development allowlist",
                reason_code=REASON_NOT_FINALIZED,
                extra={"payment_reference": reference},
            )
        return ConfirmedPayment(
            reference=reference,
            sender=self.sender,
            amount_rao=self.amount_rao,
            block=self.block,
        )


def build_payment_verifier(settings: Settings) -> PaymentVerifier:
    if settings.payment_verifier == CHAIN_PAYMENTS:
        return ChainPaymentVerifier(
            recipient=settings.payment_recipient,
            amount_rao=settings.payment_amount_rao,
        )
    if settings.payment_verifier == DEVELOPMENT_PAYMENTS:
        if settings.production:  # pragma: no cover - Settings already refuses this
            raise RuntimeError(
                "the development payment verifier is not permitted in production"
            )
        return DevelopmentPaymentVerifier(
            sender=settings.development_coldkey,
            amount_rao=settings.payment_amount_rao,
            references=settings.development_payment_references,
        )
    raise RuntimeError(f"unknown payment verifier: {settings.payment_verifier}")
