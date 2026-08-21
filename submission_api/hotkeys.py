"""Does the chain know this hotkey?

One question, asked at one place: `POST /v1/submissions/web`, where the submitter *declares* a
hotkey instead of proving control of it. The declaration is a payout instruction — the bounty is
staked for the signing coldkey and delegated to this hotkey — and the extrinsic that performs it,
`SubtensorModule.transfer_stake_and_hotkey`, cannot stake to a hotkey the chain has never heard
of. Accepting an unknown one would produce a submission that verifies, wins, and then strands a
payout command a human signs and watches fail.

So the check belongs at intake, where the person who mistyped the address is still holding it,
rather than weeks later in a multisig.

**`SubtensorModule.Owner` is the authority, and it is the same one the payment verifier already
trusts.** The extrinsic funding path establishes that the coldkey which paid owns the hotkey named
by reading exactly this storage item; a hotkey nobody registered reads back as the zero account,
which `conjectures_subnet.transfers.coldkey_of` maps to None. "Has an owner" is therefore this
codebase's existing definition of a hotkey the chain knows, and using a second definition here
would mean two answers to one question.

Note what this deliberately does **not** check: who the owner is. A submitter may nominate a
hotkey owned by somebody else's coldkey, and that is not a vulnerability — the stake is owned by
the coldkey it is staked *for*, which is the one that signed the submission. Nominating another
party's hotkey delegates your own reward to their neuron. The attribution question is separate and
answered separately, by refusing a hotkey another *account* here has proved control of.

Three implementations, following `payments.py`: the chain, an allowlist for development, and one
that refuses. The last is the default on `Services`, so a service graph assembled without this
fails closed rather than accepting whatever it is handed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from submission_api.errors import ServiceUnavailable
from submission_api.settings import CHAIN_PAYMENTS, DEVELOPMENT_PAYMENTS, Settings

logger = logging.getLogger("submission_api.hotkeys")

REASON_UNAVAILABLE = "HOTKEY_DIRECTORY_UNAVAILABLE"


class HotkeyDirectory(Protocol):
    async def is_registered(self, hotkey: str) -> bool:
        """Whether the chain knows an owner for this hotkey.

        Raises `ServiceUnavailable` if the question cannot be answered. **Not False** — the two
        mean opposite things to the caller, and collapsing them would turn an outage of ours into
        "your address is wrong".
        """
        ...


@dataclass(frozen=True)
class ChainHotkeyDirectory:
    """`SubtensorModule.Owner`, through the reader the payment verifier already holds open.

    Borrowed rather than built, because a second reader is a second websocket to the same node for
    the same kind of question — and the public endpoints answer HTTP 429 to a handshake per
    request. `app.py` closes the one reader at shutdown either way.
    """

    reader: object

    async def is_registered(self, hotkey: str) -> bool:
        probe = getattr(self.reader, "hotkey_is_registered", None)
        if probe is None:  # pragma: no cover - the factory only wraps a reader that has it
            raise ServiceUnavailable(
                "the hotkey directory is not configured",
                reason_code=REASON_UNAVAILABLE,
            )
        try:
            return await probe(hotkey=hotkey)
        except ServiceUnavailable:
            raise
        except Exception as exc:
            # Every chain failure is one refusal with one reason code. The submitter can retry;
            # what they must not get is an accepted submission whose payout cannot be executed.
            logger.warning("hotkey directory could not answer for %s: %s", hotkey, exc)
            raise ServiceUnavailable(
                "the validator cannot reach the chain to check that hotkey; try again",
                reason_code=REASON_UNAVAILABLE,
            ) from exc


@dataclass(frozen=True)
class DevelopmentHotkeyDirectory:
    """An allowlist standing in for the chain. Never permitted in production.

    Shares `DEVELOPMENT_HOTKEYS` with the development authenticator, and that overload is
    deliberate: in a local run those addresses *are* the keys that exist, so one list is one
    answer to "which hotkeys are real here". An empty list accepts nothing, which is the same
    fail-closed default the rest of this module takes.
    """

    hotkeys: tuple[str, ...] = ()

    async def is_registered(self, hotkey: str) -> bool:
        return hotkey in self.hotkeys


@dataclass(frozen=True)
class UnavailableHotkeyDirectory:
    """Refuse rather than guess. The default, so an unwired graph cannot accept a bad hotkey."""

    async def is_registered(self, hotkey: str) -> bool:
        raise ServiceUnavailable(
            "the validator cannot check hotkeys against the chain right now",
            reason_code=REASON_UNAVAILABLE,
        )


def build_hotkey_directory(
    settings: Settings, *, payments: object
) -> HotkeyDirectory:
    """Pick a directory from the same switch that decides whether we can read the chain at all.

    `SUBMISSION_PAYMENT_VERIFIER` rather than a knob of its own, because there is no deployment
    in which one of these two answers is available and the other is not: both are read-only
    queries against the same node, through the same reader. A second setting could only ever be
    set inconsistently.
    """
    if settings.payment_verifier == CHAIN_PAYMENTS:
        reader = getattr(payments, "reader", None)
        if reader is not None and hasattr(reader, "hotkey_is_registered"):
            return ChainHotkeyDirectory(reader=reader)
        return UnavailableHotkeyDirectory()
    if settings.payment_verifier == DEVELOPMENT_PAYMENTS:
        if settings.production:  # pragma: no cover - Settings already refuses this
            raise RuntimeError(
                "the development hotkey directory is not permitted in production"
            )
        return DevelopmentHotkeyDirectory(hotkeys=settings.development_hotkeys)
    return UnavailableHotkeyDirectory()


__all__ = [
    "REASON_UNAVAILABLE",
    "ChainHotkeyDirectory",
    "DevelopmentHotkeyDirectory",
    "HotkeyDirectory",
    "UnavailableHotkeyDirectory",
    "build_hotkey_directory",
]
