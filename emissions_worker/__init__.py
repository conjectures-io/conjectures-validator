"""Route every Subnet 66 epoch's validator weight to the treasury UID."""

from emissions_worker.worker import NETUID, TREASURY_UID, TreasuryWeightWorker

__all__ = ["NETUID", "TREASURY_UID", "TreasuryWeightWorker"]
