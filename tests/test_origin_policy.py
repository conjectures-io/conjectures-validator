"""The write guard's decision function, on its own.

`submission_api/origin_policy.classify` is the whole of the cross-site rule, and it is a pure
function of two request headers and an allowlist. Testing it here rather than only through the
API means the truth table is written down in one place, needs no database, and does not depend
on which route happened to be convenient to call.

The three-way result is the part worth pinning. `UNPROVEN` is not a polite `REFUSED`: the
middleware lets it through so that the miner CLI and the payment processor's webhook keep
working, and `require_writer` refuses it because it knows a cookie was involved. Collapsing the
two into a boolean is the single change that would either break every non-browser client or
reopen the hole this replaced.
"""

from __future__ import annotations

import pytest

from submission_api.origin_policy import Initiator, allowlist, classify

SITE = "https://conjectures.io"
OTHER = "https://evil.example"
ALLOWED = allowlist((SITE, "https://www.conjectures.io"))
NOTHING = allowlist(())


def verdict(origin=None, fetch_site=None, allowed=ALLOWED) -> Initiator:
    return classify(origin=origin, fetch_site=fetch_site, allowed_origins=allowed)


# --- Origin is conclusive, in both directions ------------------------------------------------


def test_an_allowlisted_origin_is_enough_on_its_own():
    """Including when `Sec-Fetch-Site` says the request is cross-site.

    This is the deliberate widening over the previous rule, and the reason the API can be
    served from an origin other than the website's. A browser reporting an allowlisted
    initiator has said everything the allowlist exists to ask.
    """
    for site in (None, "same-origin", "same-site", "cross-site", "none"):
        assert verdict(origin=SITE, fetch_site=site) is Initiator.ALLOWED, site


def test_an_origin_off_the_allowlist_is_refused_whatever_else_it_claims():
    for site in (None, "same-origin", "none", "cross-site"):
        assert verdict(origin=OTHER, fetch_site=site) is Initiator.REFUSED, site


def test_the_null_origin_is_refused_by_name():
    """A sandboxed iframe, a `data:` URL and a `file://` page all send `Origin: null`.

    It cannot reach the allowlist through configuration — the settings pattern requires a
    scheme and a host — but it is refused here explicitly so that the development wildcard
    cannot admit it either.
    """
    assert verdict(origin="null") is Initiator.REFUSED
    assert verdict(origin="null", allowed=allowlist(("*",))) is Initiator.REFUSED
    assert verdict(origin="null", fetch_site="same-origin") is Initiator.REFUSED


def test_the_wildcard_admits_any_real_origin():
    """Only reachable outside production; `Settings` refuses `*` when `APP_MODE=PROD`."""
    assert verdict(origin=OTHER, allowed=allowlist(("*",))) is Initiator.ALLOWED


def test_an_empty_allowlist_refuses_every_origin():
    """Set-but-empty is a valid, fail-closed configuration: no browser may write here."""
    assert verdict(origin=SITE, allowed=NOTHING) is Initiator.REFUSED


# --- Sec-Fetch-Site is the fallback when Origin did not arrive --------------------------------


@pytest.mark.parametrize("site", ["same-origin", "none"])
def test_a_trusted_fetch_site_passes_without_an_origin(site):
    """The case of an older browser, or an intermediary that stripped `Origin`.

    Nothing strips a `Sec-` header in practice, and no page can set one, so this is a real
    second source rather than a courtesy.
    """
    assert verdict(fetch_site=site) is Initiator.ALLOWED


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_an_untrusted_fetch_site_is_refused_without_an_origin(site):
    """`same-site` is refused alongside `cross-site` on purpose: a sibling subdomain is not
    this origin, and treating it as one is how one subdomain takeover becomes account access."""
    assert verdict(fetch_site=site) is Initiator.REFUSED


def test_a_value_no_browser_sends_is_treated_as_silence():
    """There are exactly four values. A fifth means the sender is not a browser, so it proves
    nothing — and, importantly, disproves nothing either."""
    for site in ("", "SAME-ORIGIN", "same origin", "totally-fine-honest"):
        assert verdict(fetch_site=site) is Initiator.UNPROVEN, site


# --- Silence ----------------------------------------------------------------------------------


def test_neither_header_is_unproven_rather_than_allowed_or_refused():
    """The whole reason the result is not a boolean.

    A request with neither header is not a browser request. The middleware lets it through, so
    the miner CLI, `curl` and the TMC PAY webhook keep working; `require_writer` refuses it,
    because a cookie session is a credential the browser attached by itself and silence is not
    proof. Both behaviours read this one value.
    """
    assert verdict() is Initiator.UNPROVEN


def test_an_empty_origin_string_is_not_an_origin():
    """Middleware reads headers out of the ASGI scope, where absent is `""`. It normalises to
    `None`, and this pins the behaviour if that normalisation is ever dropped."""
    assert verdict(origin="", fetch_site="same-origin") is Initiator.ALLOWED
    assert verdict(origin="") is Initiator.UNPROVEN
