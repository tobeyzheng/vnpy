from __future__ import annotations

import os
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
        self.account_last4 = os.getenv("FUTU_ACCOUNT_LAST4", "6219")
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

    def _pick_account(self, accounts_df):
        records = accounts_df.to_dict(orient="records") if hasattr(accounts_df, "to_dict") else []
        if not records:
            return None, None, records

        # 1) 按账户后4位优先
        for row in records:
            for key in ["acc_id", "sim_acc_id", "real_acc_id", "card_num", "acc_num"]:
                value = row.get(key)
                if value is not None and str(value).endswith(self.account_last4):
                    return row, key, records

        # 2) 优先模拟账户字段
        for row in records:
            for key in ["trd_env", "trade_env", "env"]:
                value = str(row.get(key, "")).upper()
                if "SIM" in value:
                    return row, key, records

        # 3) fallback 第一条
        return records[0], "fallback", records

    def _resolve_trd_env(self, row: dict[str, Any]):
        if self._futu is None:
            raise RuntimeError(self._import_error or "futu sdk unavailable")
        futu = self._futu

        configured = (self.config.trd_env or "SIMULATE").upper()
        for key in ["trd_env", "trade_env", "env"]:
            value = str(row.get(key, "")).upper()
            if "SIM" in value:
                return futu.TrdEnv.SIMULATE
            if "REAL" in value:
                return futu.TrdEnv.REAL

        return futu.TrdEnv.SIMULATE if configured == "SIMULATE" else futu.TrdEnv.REAL

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

            row, picked_by, records = self._pick_account(accounts)
            if row is None:
                return {"accounts": [], "positions": [], "orders": [], "message": "no matched account"}

            acc_id = int(row.get("acc_id") or row.get("sim_acc_id") or row.get("real_acc_id"))
            trd_env = self._resolve_trd_env(row)
            trd_env_name = "SIMULATE" if trd_env == futu.TrdEnv.SIMULATE else "REAL"

            ret, assets = ctx.accinfo_query(trd_env=trd_env, acc_id=acc_id, refresh_cache=True)
            if ret != futu.RET_OK:
                raise RuntimeError(f"accinfo_query failed: {assets}; picked_by={picked_by}; acc_id={acc_id}; env={trd_env_name}")

            ret, positions = ctx.position_list_query(trd_env=trd_env, acc_id=acc_id, refresh_cache=True)
            if ret != futu.RET_OK:
                raise RuntimeError(f"position_list_query failed: {positions}; acc_id={acc_id}; env={trd_env_name}")

            ret, orders = ctx.order_list_query(trd_env=trd_env, acc_id=acc_id, refresh_cache=True)
            if ret != futu.RET_OK:
                raise RuntimeError(f"order_list_query failed: {orders}; acc_id={acc_id}; env={trd_env_name}")

            return {
                "accounts": records,
                "assets": assets.to_dict(orient="records"),
                "positions": positions.to_dict(orient="records"),
                "orders": orders.to_dict(orient="records"),
                "message": f"ok; picked_by={picked_by}; acc_id={acc_id}; env={trd_env_name}",
            }
        finally:
            ctx.close()
