"""Every competition this API knows how to serve.

The one list to edit. Adding a competition is an adapter module (see `base.py` and
`miniz_oxide/` for a worked one) plus a line here; removing one is deleting its line. Whether
a deployment actually serves it is configuration -- `COMPETITIONS` and the competition's
database URL -- so an adapter can ship before its database exists.
"""

from __future__ import annotations

from collections.abc import Callable

from submission_api.competitions.base import CompetitionAdapter
from submission_api.competitions.miniz_oxide import MinizOxide

ADAPTERS: dict[str, Callable[[], CompetitionAdapter]] = {
    MinizOxide.info.slug: MinizOxide,
}

__all__ = ["ADAPTERS"]
