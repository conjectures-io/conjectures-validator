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

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from conjectures_subnet.db import transfers as transfer_store
from conjectures_subnet.transfers import (
    AmbiguousReference,
    ChainUnavailable,
    parse_reference,
)
from submission_api.errors import PaymentRequired
from submission_api.settings import (
    CHAIN_PAYMENTS,
    DEVELOPMENT_PAYMENTS,
    Settings,
)

REASON_NOT_FINALIZED = "PAYMENT_NOT_FINALIZED"
REASON_UNAVAILABLE = "PAYMENT_VERIFIER_UNAVAILABLE"
# The reference named an extrinsic that moved TAO more than once. Distinct from
# PAYMENT_NOT_FINALIZED because the miner can fix it, and the message says how.
REASON_AMBIGUOUS = "PAYMENT_REFERENCE_AMBIGUOUS"


@dataclass(frozen=True)
class ConfirmedPayment:
    """A finalized transfer that may fund exactly one submission.

    Carries the whole identity of the transfer, not just what the submission row stores, because
    `spend` has to be able to write it into `chain_transfers` — the table that stops the same
    transfer also buying credits.
    """

    reference: str  # canonical, `block-extrinsic-event`
    sender: str  # coldkey, proven to own the submitting hotkey
    amount_rao: int
    block: int  # the finalized block it was observed in
    recipient: str = ""
    extrinsic_index: int = 0
    event_index: int = 0
    # The block's own time. NOT NULL on `chain_transfers`, so it travels with the payment rather
    # than being re-read from the chain a second time.
    block_timestamp: dt.datetime | None = None


class PaymentVerifier(Protocol):
    async def confirm(self, *, reference: str, hotkey: str) -> ConfirmedPayment:
        """Return the confirmed transfer, or raise PaymentRequired."""
        ...

    async def spend(
        self,
        session: AsyncSession,
        payment: ConfirmedPayment,
        *,
        submission_id: uuid.UUID,
    ) -> None:
        """Mark the transfer as spent on this submission, or raise if it is already spent.

        Separate from `confirm` because it is a *write*, and it must land in the same transaction
        as the submission it funds. Confirming reads the chain; spending records the decision.
        """
        ...


@dataclass(frozen=True)
class ChainPaymentVerifier:
    """Confirm a transfer against finalized Subtensor state.

    `submission_api/chain_payments.py` is the reader in production, and it is the same one the
    deposit watcher uses, so both funding paths agree on what one transfer is. A verifier built
    without a reader still fails closed — it refuses every submission rather than admitting an
    unpaid one, which is the only safe default for a component that gates money — because a
    deployment can still be configured without a chain to read.
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
        try:
            transfer = await self.reader.finalized_transfer(reference=reference)
        except AmbiguousReference as exc:
            # The one refusal a miner can act on: the extrinsic they named moved TAO more than
            # once, and the message lists the references to choose between. 400, not 402 — nothing
            # is wrong with the payment, only with how it was identified.
            raise PaymentRequired(
                str(exc),
                reason_code=REASON_AMBIGUOUS,
                status_code=400,
                extra={"payment_reference": reference},
            ) from exc
        except ChainUnavailable as exc:
            # The chain could not be read. Not the miner's fault and not a refusal of their
            # payment, so it must not be recorded as one: 503, and they should come back.
            raise PaymentRequired(
                "payment confirmation is temporarily unavailable; retry shortly",
                reason_code=REASON_UNAVAILABLE,
                status_code=503,
                extra={"payment_reference": reference},
            ) from exc
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
            # The reader's canonical reference, NOT the one the request supplied. A miner may cite
            # `block-extrinsic` where the canonical identity is `block-extrinsic-event`, and
            # `submissions.payment_reference` is unique — so storing what was typed would let two
            # spellings of one transfer fund two submissions. The docstring's last bullet is this.
            reference=transfer.reference,
            sender=transfer.sender,
            amount_rao=transfer.amount_rao,
            block=transfer.block,
            recipient=transfer.recipient,
            # Parsed back out of the canonical reference the reader returned. The reader built that
            # string from the position it read, so this cannot disagree with the chain — and it
            # keeps the reference the single carrier of a transfer's identity.
            **_position(transfer.reference),
            block_timestamp=transfer.block_timestamp,
        )

    async def spend(
        self,
        session: AsyncSession,
        payment: ConfirmedPayment,
        *,
        submission_id: uuid.UUID,
    ) -> None:
        """Record the transfer as spent on this submission, so it cannot also buy credits.

        Raises `RecordConflict` if the deposit watcher already credited it, which rolls the
        submission back with it. See `conjectures_subnet.db.transfers.claim_for_submission`.
        """
        if payment.block_timestamp is None:  # pragma: no cover - the reader always sets it
            raise PaymentRequired(
                "the payment reader returned no block timestamp",
                reason_code=REASON_UNAVAILABLE,
                status_code=503,
            )
        await transfer_store.claim_for_submission(
            session,
            extrinsic_reference=payment.reference,
            block=payment.block,
            block_timestamp=payment.block_timestamp,
            extrinsic_index=payment.extrinsic_index,
            event_index=payment.event_index,
            sender_coldkey=payment.sender,
            recipient=payment.recipient,
            amount_rao=payment.amount_rao,
            note=f"funded submission {submission_id} directly",
        )


def _position(reference: str) -> dict[str, int]:
    """The extrinsic and event indexes out of a canonical reference."""
    parsed = parse_reference(reference)
    if parsed is None or parsed.event_index is None:  # pragma: no cover - reader-built
        raise PaymentRequired(
            "the payment reader returned a non-canonical reference",
            reason_code=REASON_UNAVAILABLE,
            status_code=503,
        )
    return {
        "extrinsic_index": parsed.extrinsic_index,
        "event_index": parsed.event_index,
    }


@dataclass(frozen=True)
class FinalizedTransfer:
    reference: str  # canonical, `block-extrinsic-event`
    sender: str
    recipient: str
    amount_rao: int
    block: int
    # The block's own time, so `spend` need not read the chain again to record the transfer.
    block_timestamp: dt.datetime | None = None


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

    async def spend(
        self,
        session: AsyncSession,
        payment: ConfirmedPayment,
        *,
        submission_id: uuid.UUID,
    ) -> None:
        """Nothing to spend: no transfer happened.

        Deliberately not a synthetic `chain_transfers` row. That table is the record of what was
        observed on chain, and writing invented block positions into it would make the deposit
        watcher's own queue untrustworthy — the one place an operator looks to find real money
        nobody has claimed. `submissions.payment_reference` still keeps a development reference from
        funding two submissions, which is the only guarantee available without a chain.
        """
        del session, payment, submission_id


def build_payment_verifier(settings: Settings) -> PaymentVerifier:
    if settings.payment_verifier == CHAIN_PAYMENTS:
        # Imported here rather than at module scope: this module defines the protocol, and the
        # implementation imports it back. Local, so the two are not a cycle.
        from submission_api.chain_payments import SubtensorTransferReader

        return ChainPaymentVerifier(
            recipient=settings.payment_recipient,
            amount_rao=settings.payment_amount_rao,
            reader=SubtensorTransferReader.for_network(
                settings.bittensor_network,
                archive_network=settings.bittensor_archive_network or None,
            ),
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
