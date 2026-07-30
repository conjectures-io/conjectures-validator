"""Miner-facing paid submission API for conjectures.io Bittensor Subnet 66.

This package is the network-facing trust domain. It authenticates miners, admits proof
bundles, records durable submission state, and hands bounded proof bytes to the isolated
Lean verifier. It must never share a process or container with that verifier, and payment
credentials and validator wallet keys must never be reachable from it. See docs/SUBNET.md
and SECURITY.md for the boundary.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
