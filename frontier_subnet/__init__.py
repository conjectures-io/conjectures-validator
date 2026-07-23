"""Bittensor transport for Frontier Math proof submissions.

This package deliberately contains no solver. Miners import proof files they
created elsewhere, then serve immutable commitments and reveals for validators.
"""

PROTOCOL_VERSION = 1

__all__ = ["PROTOCOL_VERSION"]
