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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

# `credits:bonus`, comma-separated. A compact spec rather than a JSON config file,
# because it is three numbers per package and a file would be one more thing to mount.
PACKAGE_SPEC = re.compile(r"^(\d{1,6})(?::(\d{1,6}))?$")

MAX_PACKAGES = 8
MAX_TERMS_BYTES = 256 * 1024

# How many credits one purchase may PAY FOR, across every payment method and funding path.
#
# The single source of truth for the rule. `parse_packages` refuses a `CREDIT_PACKAGES` entry
# that exceeds it, so a deployment cannot be configured into offering a package the write paths
# would then reject — which is the failure this constant exists to make impossible. The two
# request models that take a credit count (`me.DepositRequest` and `tmc_pay.PurchaseRequest`)
# bound themselves by it rather than by a literal, so raising the cap is one edit here.
#
# **Paid credits, not granted credits.** A package bonus is extra credits the validator gives
# away, not something a buyer can ask for — the request models only ever carry a paid count, so
# that count is what has to be bounded. `10:3` grants 13 credits under a cap of 10 and that is
# correct: nobody can request 13.
MAX_CREDITS_PER_PURCHASE = 10

# How credits can be paid for.
#
# `btcli` is a transfer straight to the treasury, confirmed by reading finalized chain state —
# `payments.py` and `deposit_watcher/`. `POST /v1/me/deposits` hands back the exact command to
# run. `tmc_pay` is an invoice at the TMC PAY processor, which derives its own deposit address,
# confirms the payment, and settles to the treasury later; see `submission_api/tmc_pay.py` for
# what that changes about the evidence behind a credit.
#
# Every method charges the same `CREDIT_PRICE_RAO` per credit. `payment_methods` returns all of
# them the deployment can actually take money through, because a method the website renders and
# the API cannot serve is worse than one it never offers.
METHOD_BTCLI = "btcli"
METHOD_TMC_PAY = "tmc_pay"

# NOT SERVED YET, deliberately. The Talisman / tao.com browser-extension flow signs the *same*
# treasury transfer in the page instead of in a terminal, so it needs no new evidence path — the
# finalized-transfer reader already confirms it, and `POST /v1/me/deposits` already declares the
# expectation it is checked against. What is missing is only a frontend that can sign.
#
# Uncomment this and add it to `PAYMENT_METHODS` in the same change that ships that frontend.
# Announcing it before then is the same mistake as announcing a method the API cannot serve: the
# purchase page would render a button that goes nowhere.
# METHOD_WALLET_EXTENSION = "wallet_extension"

PAYMENT_METHODS = (METHOD_BTCLI, METHOD_TMC_PAY)


def payment_methods(*, tmc_pay_enabled: bool) -> tuple[str, ...]:
    """Every method this deployment can actually take money through.

    `btcli` is always available: it needs nothing configured beyond the treasury address that
    payments already require. TMC PAY is added only when the processor credentials are present,
    so the list is the deployment's real capability rather than the build's.
    """
    if tmc_pay_enabled:
        return PAYMENT_METHODS
    return (METHOD_BTCLI,)


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
    """Read `CREDIT_PACKAGES`, e.g. `1,5:1,10:3`.

    Ordered by size and deduplicated on the paid credit count, so the same configuration
    always produces the same list and two entries cannot offer different bonuses for the
    same purchase.

    **`MAX_CREDITS_PER_PURCHASE` is enforced here, at startup, and that is the point.** The
    write paths bound themselves by the same constant, so a package larger than the cap could
    only ever be a package the API would refuse to sell — a purchase page advertising a
    purchase that 422s. Refused at startup rather than filtered out of the pricing response,
    because a deployment silently serving fewer packages than it was configured with is a
    misconfiguration that looks like working software.
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
        if credits_ > MAX_CREDITS_PER_PURCHASE:
            raise CreditsConfigError(
                f"CREDIT_PACKAGES offers a {credits_}-credit package, but at most "
                f"{MAX_CREDITS_PER_PURCHASE} credit(s) may be bought per purchase; "
                "raise MAX_CREDITS_PER_PURCHASE to sell larger packages"
            )
        if bonus > credits_:
            # A bonus larger than the purchase is far more likely a typo than an
            # intended promotion, and it would be a money mistake. This is the only bound on a
            # bonus: the cap above governs what a buyer may *pay for*, and a grant the validator
            # chooses to make is not bounded by what anyone can request.
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


def bonus_schedule(packages: Iterable[CreditPackage]) -> dict[int, int]:
    """Paid credit count -> bonus credits, for the packages that actually grant one.

    The form the crediting paths want. They are handed a rao amount and have to answer "does
    this purchase earn a bonus", which is a lookup on the paid count — not a scan of a list of
    dataclasses that also carries prices they already know.

    Packages with no bonus are omitted rather than mapped to zero, so an empty result means
    "no deal is on offer" and a funding path can skip the whole question on identity.
    """
    return {
        item.credits: item.bonus_credits for item in packages if item.bonus_credits > 0
    }


@lru_cache(maxsize=8)
def bonus_schedule_for(spec: str, *, credit_price_rao: int) -> Mapping[int, int]:
    """The deals a deployment is configured for, from the raw `CREDIT_PACKAGES` spec.

    For the crediting paths that hold a `Settings` but not the assembled `Services` — notably
    `routers.tmc_pay.apply_invoice`, whose narrow signature is what lets
    `scripts/reconcile_tmc_pay.py` reuse it without building the whole service graph. Deriving the
    schedule from settings there rather than threading it in keeps that property, and means the
    reconciler grants exactly the deals the API advertises without anyone remembering to pass them.

    Cached because it is reached on every settlement and a process has one configuration. Keyed on
    the spec and the price, so a test building several `Settings` gets the right answer for each
    rather than the first one's. The result is a read-only view: a cached mutable dict handed to
    several callers is a bug waiting for one of them to write to it.

    Startup has already validated the spec — `app.py` parses the same string through
    `parse_packages` — so a `CreditsConfigError` here would mean a process running on a
    configuration it refused to start with, and is deliberately left to propagate.
    """
    return MappingProxyType(
        bonus_schedule(parse_packages(spec, credit_price_rao=credit_price_rao))
    )


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
    "MAX_CREDITS_PER_PURCHASE",
    "MAX_PACKAGES",
    "METHOD_BTCLI",
    "METHOD_TMC_PAY",
    "PAYMENT_METHODS",
    "CreditPackage",
    "CreditsConfigError",
    "SubmissionTerms",
    "bonus_schedule",
    "bonus_schedule_for",
    "btcli_command",
    "parse_packages",
    "payment_methods",
]
