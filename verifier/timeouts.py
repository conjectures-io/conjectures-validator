"""Execution budgets shared by the verifier and its worker.

Published task bytes remain immutable. Production verification grants at least the
current default budget, including to older tasks committed with a shorter deadline.
Non-production fixtures retain their declared deadlines.
"""

DEFAULT_TIMEOUT_SECONDS = 4 * 60 * 60
MAX_TIMEOUT_SECONDS = DEFAULT_TIMEOUT_SECONDS


def verification_timeout_seconds(declared_seconds: int, *, production: bool = True) -> int:
    return max(declared_seconds, DEFAULT_TIMEOUT_SECONDS) if production else declared_seconds
