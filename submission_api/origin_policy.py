"""Who initiated this state-changing request, and is that good enough to let it through.

One pure function, `classify`, shared by the two places that need the answer: the middleware
that refuses obviously cross-site writes before a route runs, and `dependencies.require_writer`,
which is the authoritative gate for a write authenticated by an *ambient* credential.

**The threat.** CSRF is the confused-deputy problem of ambient credentials. A cookie is attached
by the browser to any request to this origin, including one that a page on `evil.example` caused.
So a write authenticated by a cookie needs proof that the request was initiated by something
entitled to initiate it, and the proof has to be something a hostile page cannot produce.

**Why two request headers are that proof.** Both are on the Fetch spec's *forbidden header name*
list, which means no page — not `fetch`, not `XMLHttpRequest`, not a `<form>`, not
`sendBeacon` — can set or override them. The browser writes them, and the browser is not the
attacker:

* `Origin` is sent by every current browser on every state-changing request, cross-origin *and*
  same-origin, for `fetch`/XHR and for form submissions in all three encodings. Its value is the
  origin of the document that initiated the request, so a page on `evil.example` announces
  itself. A document with an opaque origin — a sandboxed iframe, a `data:` URL, a `file://`
  page, some cross-origin redirect chains — sends the literal string `null`, which is refused
  below by name rather than left to the allowlist.
* `Sec-Fetch-Site` says how the initiator relates to the target: `same-origin`, `same-site`,
  `cross-site`, or `none` for a user-initiated load with no initiator at all. `same-site` is
  *not* trusted here: a sibling subdomain is not this origin, and treating it as one is how a
  single subdomain takeover becomes account access.

**Where each one is blind, and why the pair is not.** `Origin` is near-universal but can be
stripped by an intermediary and is absent from ancient browsers. `Sec-Fetch-Site` is
unstrippable in practice but only reached general availability with Safari 16.4 in 2023. Neither
gap is exploitable on its own, because an attacker cannot *choose* which headers the victim's
browser sends — the victim's browser sends what it sends. The gap that matters is a browser that
sends **neither**, and that case is `UNPROVEN` below rather than `ALLOWED`, so the caller holding
an ambient credential fails closed on it.

**`ALLOWED` on `Origin` alone is deliberate, and it is the reason this is not simply the old
check with a piece missing.** An allowlisted origin is *the* trust boundary for writes: it is
the site permitted to act on a signed-in visitor's behalf. Insisting on `Sec-Fetch-Site:
same-origin` *as well* breaks the ordinary deployment where the website and the API are
different origins — `conjectures.io` calling `api.conjectures.io` is `same-site`, and
`www.conjectures.io` calling `conjectures.io` is too. Requiring both means the API can only ever
be reverse-proxied under the website's own origin.

**What this does not defend against, by construction.** Script running *on* an allowlisted
origin — an XSS on the website — passes every check here, because the browser truthfully reports
an allowlisted initiator. That was equally true of the token this replaces: the token had to be
readable by page script in order to be echoed into a header, so same-origin script could always
read it. Cross-site request forgery and cross-site scripting are different bugs with different
mitigations; this function addresses the first one only.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum
from functools import lru_cache

# Methods that cannot change state, and so need no proof of initiator. Everything else does.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

# The wildcard the CORS allowlist accepts outside production. Mirrored here so a development
# deployment that has not enumerated its origins is not a deployment whose writes all fail.
WILDCARD = "*"

# The origin of a document that has none: a sandboxed iframe, a `data:` URL, a `file://` page,
# and some cross-origin redirect chains. Refused explicitly rather than by failing to match the
# allowlist, so that the wildcard above cannot admit it either.
NULL_ORIGIN = "null"

# `Sec-Fetch-Site` has exactly four values. Splitting them into two named sets rather than
# testing one membership and treating the remainder as "unknown" is what keeps a value the
# browser did not send from being read as a value it did.
TRUSTED_FETCH_SITES = frozenset({"same-origin", "none"})
UNTRUSTED_FETCH_SITES = frozenset({"same-site", "cross-site"})


class Initiator(Enum):
    """What the request proved about where it came from.

    Three outcomes rather than a boolean, because "the browser said this is cross-site" and "no
    browser said anything" are different facts and the two callers act on them differently. The
    middleware refuses only `REFUSED`, so a non-browser client keeps working; `require_writer`
    refuses anything that is not `ALLOWED`, because it knows the request carries a credential the
    browser attaches on its own.
    """

    #: A browser named an allowlisted initiator, or named this very origin.
    ALLOWED = "allowed"
    #: A browser named an initiator that is not permitted to write here.
    REFUSED = "refused"
    #: Neither header arrived. Not a browser, or a browser too old to say.
    UNPROVEN = "unproven"


@lru_cache(maxsize=8)
def allowlist(origins: Iterable[str]) -> frozenset[str]:
    """The allowlist as a set, built once per distinct configuration.

    Cached because `classify` runs on every write and `Settings` holds the allowlist as a tuple
    — deliberately, so that the same configuration always produces the same ordered value in a
    log line. There are two of these tuples in a process and they never change, so the cache is
    a two-entry table, not a leak.
    """
    return frozenset(origins)


def classify(
    *,
    origin: str | None,
    fetch_site: str | None,
    allowed_origins: frozenset[str],
) -> Initiator:
    """Judge one request from its two initiator headers.

    `Origin` is consulted first and is conclusive in both directions. A browser that sent it
    has named the document responsible for the request, and if that document is on the
    allowlist there is nothing further to establish — including when `Sec-Fetch-Site` says
    `cross-site`, which is exactly what an allowlisted website on another origin produces.

    `Sec-Fetch-Site` is the fallback for the case where `Origin` did not arrive: an older
    browser, or an intermediary that stripped it. `same-origin` and `none` pass; `same-site`
    and `cross-site` are refused; anything else is treated as absent, because those are the
    only four values a browser emits and a fifth means the sender is not one.
    """
    if origin:
        if origin == NULL_ORIGIN:
            return Initiator.REFUSED
        if WILDCARD in allowed_origins or origin in allowed_origins:
            return Initiator.ALLOWED
        return Initiator.REFUSED
    if fetch_site in TRUSTED_FETCH_SITES:
        return Initiator.ALLOWED
    if fetch_site in UNTRUSTED_FETCH_SITES:
        return Initiator.REFUSED
    return Initiator.UNPROVEN


__all__ = [
    "NULL_ORIGIN",
    "SAFE_METHODS",
    "TRUSTED_FETCH_SITES",
    "UNTRUSTED_FETCH_SITES",
    "WILDCARD",
    "Initiator",
    "allowlist",
    "classify",
]
