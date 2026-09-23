"""Pluggable competitions: one router, one engine per competition, one adapter each.

`base` is the contract, `registry` opens the engines, `catalog` lists the adapters. See
`base.py` for what an adapter owns and what the API keeps for itself.
"""

from submission_api.competitions.base import CompetitionAdapter, SubmissionState, Unsupported
from submission_api.competitions.registry import (
    Competition,
    CompetitionConfig,
    CompetitionConfigError,
    CompetitionRegistry,
    UnknownCompetition,
    build_registry,
    database_url_variable,
)

__all__ = [
    "Competition",
    "CompetitionAdapter",
    "CompetitionConfig",
    "CompetitionConfigError",
    "CompetitionRegistry",
    "SubmissionState",
    "UnknownCompetition",
    "Unsupported",
    "build_registry",
    "database_url_variable",
]
