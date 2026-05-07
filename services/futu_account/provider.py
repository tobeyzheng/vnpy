from __future__ import annotations

import socket
from typing import List

from services.futu_opend import OpenDClient

from .models import FutuAccountSummary, FutuOrder, FutuPosition


class FutuAccountProvider:
    """Read-only account snapshot provider.

    Current stage:
    - verifies OpenD reachability
    - returns placeholder summary if SDK query path is not yet wired
    Future stage:
    - query accounts/positions/orders through futu SDK
    """

    def get_summary(self) -> FutuAccountSummary:
        probe = OpenDClient().probe()
        if not probe.reachable:
            return FutuAccountSummary(status="disconnected", message=f"OpenD unreachable: {probe.message}")

        return FutuAccountSummary(
            status="connected",
            account_count=1,
            env="SIMULATE",
            total_assets=None,
            cash=None,
            buying_power=None,
            positions=[],
            orders=[],
            message="OpenD reachable; SDK account query not yet wired",
        )

    def get_watchlist_snapshot(self, codes: List[str]) -> dict:
        probe = OpenDClient().probe()
        if not probe.reachable:
            return {"status": "disconnected", "items": [], "message": probe.message}
        return {
            "status": "connected",
            "items": [{"code": code, "price": None, "change_pct": None} for code in codes],
            "message": "OpenD reachable; SDK quote query not yet wired",
        }
