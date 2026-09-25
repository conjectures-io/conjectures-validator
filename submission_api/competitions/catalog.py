"""Every competition this API knows how to serve.

The one list to edit. Adding a competition is an adapter module (see `base.py` and
`lz77/` for a worked one) plus a line here; removing one is deleting its line. Whether
a deployment actually serves it is configuration -- `COMPETITIONS` and the competition's
database URL -- so an adapter can ship before its database exists.
"""

from __future__ import annotations

from collections.abc import Callable

from submission_api.competitions.base import CompetitionAdapter
from submission_api.competitions.lz77 import Lz77

ADAPTERS: dict[str, Callable[[], CompetitionAdapter]] = {
    Lz77.info.slug: Lz77,
}

__all__ = ["ADAPTERS"]
