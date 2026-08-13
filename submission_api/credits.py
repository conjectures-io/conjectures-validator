"""Credit pricing, purchase packages, and the submission terms.

What a credit costs, what bundles of credits are on offer, and the terms a miner accepts
by submitting. All configured, all read once at startup, none of it derived from client
input.

Money rules, restated because this is where a mistake would be expensive:

* **Integer rao only.** One credit is one verification attempt at `credit_price_rao`. No
  float appears in any arithmetic here.
* **A bonus is extra credits, never a discount.** A package grants `credits + bonus`
  credits for `credits * price` rao. Expressing it as a reduced price would produce a
  non-integer per-credit cost and a rounding decision nobody wants to own.
* **`price_usd` is optional and honest about it.** Converting TAO to USD needs a live
  external rate this validator does not have. Rather than inventing one, the field is
  null unless an operator pins `CREDIT_PRICE_USD`, and the response says when it was
  pinned so a reader can judge how stale it is.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path

# `credits:bonus`, comma-separated. A compact spec rather than a JSON config file,
# because it is three numbers per package and a file would be one more thing to mount.
PACKAGE_SPEC = re.compile(r"^(\d{1,6})(?::(\d{1,6}))?$")

MAX_PACKAGES = 8
MAX_TERMS_BYTES = 256 * 1024

# How credits can be paid for.
#
# `wallet_extension` is a transfer straight to the treasury, signed in the browser and confirmed
# by reading finalized chain state — `payments.py` and `deposit_watcher/`. `tmc_pay` is an invoice
# at the TMC PAY processor, which
# derives its own deposit address, confirms the payment, and settles to the treasury later; see
# `submission_api/tmc_pay.py` for what that changes about the evidence behind a credit.
#
# Both charge the same `CREDIT_PRICE_RAO` per credit. `payment_methods` filters the list by what
# the deployment is actually configured for, because a method the website renders and the API
# cannot serve is worse than one it does not offer.
METHOD_WALLET_EXTENSION = "wallet_extension"
METHOD_TMC_PAY = "tmc_pay"
PAYMENT_METHODS = (METHOD_WALLET_EXTENSION, METHOD_TMC_PAY)


def payment_methods(*, tmc_pay_enabled: bool) -> tuple[str, ...]:
    """The methods this deployment can actually take money through."""
    if tmc_pay_enabled:
        return PAYMENT_METHODS
    return (METHOD_WALLET_EXTENSION,)


class CreditsConfigError(RuntimeError):
    """The credit configuration is unusable and the process must not start."""


@dataclass(frozen=True)
class CreditPackage:
    """One purchasable bundle. `credits` are paid for; `bonus_credits` are not."""

    credits: int
    bonus_credits: int
    price_rao: int

    @property
    def total_credits(self) -> int:
        return self.credits + self.bonus_credits


def parse_packages(spec: str, *, credit_price_rao: int) -> tuple[CreditPackage, ...]:
    """Read `CREDIT_PACKAGES`, e.g. `1,10:1,50:8`.

    Ordered by size and deduplicated on the paid credit count, so the same configuration
    always produces the same list and two entries cannot offer different bonuses for the
    same purchase.
    """
    packages: dict[int, CreditPackage] = {}
    for item in (part.strip() for part in spec.split(",")):
        if not item:
            continue
        matched = PACKAGE_SPEC.fullmatch(item)
        if matched is None:
            raise CreditsConfigError(
                f"CREDIT_PACKAGES entry must be 'credits' or 'credits:bonus', got {item!r}"
            )
        credits_ = int(matched.group(1))
        bonus = int(matched.group(2) or 0)
        if credits_ <= 0:
            raise CreditsConfigError("a credit package must contain at least one credit")
        if bonus > credits_:
            # A bonus larger than the purchase is far more likely a typo than an
            # intended promotion, and it would be a money mistake.
            raise CreditsConfigError(
                f"package bonus {bonus} exceeds its {credits_} paid credits; "
                "check CREDIT_PACKAGES"
            )
        packages[credits_] = CreditPackage(
            credits=credits_,
            bonus_credits=bonus,
            price_rao=credits_ * credit_price_rao,
        )
    if len(packages) > MAX_PACKAGES:
        raise CreditsConfigError(
            f"CREDIT_PACKAGES lists {len(packages)} packages; the maximum is {MAX_PACKAGES}"
        )
    return tuple(packages[key] for key in sorted(packages))


def btcli_command(*, treasury: str, amount_rao: int, rao_per_tao: int) -> str:
    """The ready-to-copy command for a deposit.

    The amount is rendered from integer rao by string arithmetic, not by dividing into a
    float: `amount_rao / 1e9` is exactly the kind of step that silently loses a rao.
    """
    whole, fraction = divmod(amount_rao, rao_per_tao)
    digits = len(str(rao_per_tao)) - 1
    amount = f"{whole}.{fraction:0{digits}d}".rstrip("0").rstrip(".")
    return (
        f"btcli wallet transfer --dest {treasury} --amount {amount or '0'}"
    )


@dataclass(frozen=True)
class SubmissionTerms:
    """The terms a miner accepts by submitting, and the reasons a review may decide.

    Both reason lists are shared with the Stage 3 review page on purpose: the page may
    offer only these codes, and a reviewer must not be able to invent a reason.
    """

    version: str
    body_md: str
    effective_from: dt.date
    approval_reasons: tuple[tuple[str, str], ...]
    disqualification_reasons: tuple[tuple[str, str], ...]

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        version: str,
        effective_from: dt.date,
    ) -> SubmissionTerms:
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise CreditsConfigError(
                f"cannot read the submission terms at {path}: {exc}"
            ) from exc
        if len(raw) > MAX_TERMS_BYTES:
            raise CreditsConfigError(f"the submission terms at {path} are implausibly large")
        try:
            body = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CreditsConfigError("the submission terms are not valid UTF-8") from exc
        if not body.strip():
            raise CreditsConfigError("the submission terms are empty")
        return cls(
            version=version,
            body_md=body,
            effective_from=effective_from,
            approval_reasons=APPROVAL_REASONS,
            disqualification_reasons=DISQUALIFICATION_REASONS,
        )


# The complete set of reason codes a human reviewer may use with an APPROVED
# decision. These are published beside the rejection codes so the review page and
# the policy document consume the same allowlist.
APPROVAL_REASONS: tuple[tuple[str, str], ...] = (
    (
        "REVIEW_APPROVED",
        "The Lean-valid submission passed manual review and earns the displayed "
        "conjecture bounty.",
    ),
    (
        "FORMALIZATION_DEFECT_AWARD",
        "The exact published Lean task was proved or refuted through a material "
        "formalization defect; the submission earns the $750 USD-equivalent award "
        "paid in Subnet 66 Alpha instead of the displayed conjecture bounty.",
    ),
)

APPROVAL_CODES = frozenset(code for code, _ in APPROVAL_REASONS)


# The complete set of reasons a Lean-valid proof may still be refused a reward, as shown
# to a miner before they spend a credit and to a reviewer when they decide. Defined in
# code rather than in the terms markdown so the two cannot drift: the prose explains
# them, this is the list.
DISQUALIFICATION_REASONS: tuple[tuple[str, str], ...] = (
    (
        "ADMITTED_DEPENDENCY",
        "The proof depends on an admitted result — `sorry`, `sorryAx`, or any lemma that "
        "transitively relies on one.",
    ),
    (
        "TRIVIALISED_STATEMENT",
        "The submitted statement is not the challenge statement: it was weakened, "
        "restated, or made vacuous.",
    ),
    (
        "FORBIDDEN_IMPORT",
        "The proof imports the source declaration or another module the task forbids.",
    ),
    (
        "UNPERMITTED_AXIOM",
        "The proof's axiom closure contains an axiom the task does not permit.",
    ),
    (
        "NOT_NOVEL",
        "The result was already available in the pinned environment, or a dated public "
        "source had already solved the same direct problem and the submission substantially "
        "implements that source's solution for the exact target.",
    ),
    (
        "PRIOR_EXTERNAL_FORMALIZATION",
        "Before this submission was accepted, the same reward target had already been "
        "formally established in a publicly documented external proof system.",
    ),
    (
        "DUPLICATE_OF_EARLIER_SUBMISSION",
        "An earlier submission already established this result; one reward is paid per "
        "problem.",
    ),
    (
        "MISATTRIBUTED_WORK",
        "The submission is not the submitter's work to claim.",
    ),
    (
        "ABUSE",
        "The submission is part of an attempt to overload, probe, or game the validator.",
    ),
)

DISQUALIFICATION_CODES = frozenset(code for code, _ in DISQUALIFICATION_REASONS)


__all__ = [
    "APPROVAL_CODES",
    "APPROVAL_REASONS",
    "DISQUALIFICATION_CODES",
    "DISQUALIFICATION_REASONS",
    "MAX_PACKAGES",
    "METHOD_WALLET_EXTENSION",
    "METHOD_TMC_PAY",
    "PAYMENT_METHODS",
    "CreditPackage",
    "CreditsConfigError",
    "SubmissionTerms",
    "btcli_command",
    "parse_packages",
    "payment_methods",
]
