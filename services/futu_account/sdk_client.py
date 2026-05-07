from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional

from services.futu_opend import OpenDConfig


@dataclass
class SdkAvailability:
    available: bool
    message: str


class FutuSdkClient:
    def __init__(self, config: OpenDConfig | None = None):
        self.config = config or OpenDConfig()
        self._futu = None
        self._import_error: Optional[str] = None
        try:
            import futu  # type: ignore
            self._futu = futu
        except Exception as e:  # pragma: no cover
            self._import_error = str(e)

    def availability(self) -> SdkAvailability:
        if self._futu is None:
            return SdkAvailability(False, self._import_error or "futu sdk import failed")
        return SdkAvailability(True, "ok")

    def list_accounts(self) -> list[dict[str, Any]]:
        if self._futu is None:
            raise RuntimeError(self._import_error or "futu sdk unavailable")
        futu = self._futu
        ctx = futu.OpenSecTradeContext(host=self.config.host, port=self.config.port)
        try:
            ret, data = ctx.get_acc_list()
            if ret != futu.RET_OK:
                raise RuntimeError(str(data))
            if hasattr(data, "to_dict"):
                return data.to_dict(orient="records")
            return []
        finally:
            ctx.close()

    def account_snapshot(self) -> dict[str, Any]:
        if self._futu is None:
            raise RuntimeError(self._import_error or "futu sdk unavailable")
        futu = self._futu
        ctx = futu.OpenSecTradeContext(host=self.config.host, port=self.config.port)
        try:
            ret, accounts = ctx.get_acc_list()
            if ret != futu.RET_OK:
                raise RuntimeError(str(accounts))
            if getattr(accounts, "empty", True):
                return {"accounts": [], "positions": [], "orders": [], "message": "no accounts"}

            first = accounts.iloc[0]
            acc_id = int(first["acc_id"])
            trd_env = futu.TrdEnv.SIMULATE if self.config.trd_env.upper() == "SIMULATE" else futu.TrdEnv.REAL

            ret, assets = ctx.accinfo_query(trd_env=trd_env, acc_id=acc_id, refresh_cache=True)
            if ret != futu.RET_OK:
                raise RuntimeError(f"accinfo_query failed: {assets}")

            ret, positions = ctx.position_list_query(trd_env=trd_env, acc_id=acc_id, refresh_cache=True)
            if ret != futu.RET_OK:
                raise RuntimeError(f"position_list_query failed: {positions}")

            ret, orders = ctx.order_list_query(trd_env=trd_env, acc_id=acc_id, refresh_cache=True)
            if ret != futu.RET_OK:
                raise RuntimeError(f"order_list_query failed: {orders}")

            return {
                "accounts": accounts.to_dict(orient="records"),
                "assets": assets.to_dict(orient="records"),
                "positions": positions.to_dict(orient="records"),
                "orders": orders.to_dict(orient="records"),
                "message": "ok",
            }
        finally:
            ctx.close()
