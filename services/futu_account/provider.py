from __future__ import annotations

from typing import List

from services.futu_opend import OpenDClient

from .models import FutuAccountSummary, FutuOrder, FutuPosition
from .sdk_client import FutuSdkClient


class FutuAccountProvider:
    """Read-only account snapshot provider."""

    def get_summary(self) -> FutuAccountSummary:
        probe = OpenDClient().probe()
        if not probe.reachable:
            return FutuAccountSummary(status="disconnected", message=f"OpenD unreachable: {probe.message}")

        sdk = FutuSdkClient()
        avail = sdk.availability()
        if not avail.available:
            return FutuAccountSummary(
                status="connected",
                account_count=0,
                env="SIMULATE",
                message=f"OpenD reachable; futu SDK unavailable: {avail.message}",
            )

        try:
            snapshot = sdk.account_snapshot()
            accounts = snapshot.get("accounts", [])
            assets = snapshot.get("assets", [])
            positions = snapshot.get("positions", [])
            orders = snapshot.get("orders", [])

            asset0 = assets[0] if assets else {}
            pos_items = [
                FutuPosition(
                    code=str(row.get("code", "")),
                    name=str(row.get("stock_name", "")),
                    qty=float(row.get("qty", 0) or 0),
                    market_val=float(row.get("market_val", 0) or 0) if row.get("market_val") is not None else None,
                    pl_ratio=float(row.get("pl_ratio", 0) or 0) if row.get("pl_ratio") is not None else None,
                )
                for row in positions[:10]
            ]
            order_items = [
                FutuOrder(
                    code=str(row.get("code", "")),
                    side=str(row.get("trd_side", "")),
                    qty=float(row.get("qty", 0) or 0),
                    status=str(row.get("order_status", "")),
                )
                for row in orders[:10]
            ]
            return FutuAccountSummary(
                status="connected",
                account_count=len(accounts),
                env="SIMULATE",
                total_assets=float(asset0.get("total_assets", 0) or 0) if asset0 else None,
                cash=float(asset0.get("cash", 0) or 0) if asset0 else None,
                buying_power=float(asset0.get("power", 0) or 0) if asset0 else None,
                positions=pos_items,
                orders=order_items,
                message=snapshot.get("message", "ok"),
            )
        except Exception as e:
            return FutuAccountSummary(
                status="connected",
                account_count=0,
                env="SIMULATE",
                message=f"OpenD reachable; SDK query failed: {e}",
            )

    def get_watchlist_snapshot(self, codes: List[str]) -> dict:
        probe = OpenDClient().probe()
        if not probe.reachable:
            return {"status": "disconnected", "items": [], "message": probe.message}
        sdk = FutuSdkClient()
        avail = sdk.availability()
        if not avail.available:
            return {"status": "connected", "items": [], "message": f"SDK unavailable: {avail.message}"}
        return {
            "status": "connected",
            "items": [{"code": code, "price": None, "change_pct": None} for code in codes],
            "message": "SDK quote query hook reserved",
        }
