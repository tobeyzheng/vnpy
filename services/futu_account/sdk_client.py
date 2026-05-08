from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, List, Optional

from services.futu_opend import OpenDConfig

@dataclass
class SdkAvailability:
    available: bool
    message: str


class FutuLiveAccountMismatchError(RuntimeError):
    """Raised when live-strict account selection cannot find a unique expected account."""


class FutuSdkClient:
    def __init__(
        self,
        config: OpenDConfig | None = None,
        *,
        live_strict: bool = False,
        expect_trd_env: str | None = None,
        expect_acc_type: str | None = None,
        expect_market: str | None = None,
        expect_last4: str | None = None,
    ):
        self.config = config or OpenDConfig()
        self._futu = None
        self._import_error: Optional[str] = None
        self.account_last4 = os.getenv("FUTU_ACCOUNT_LAST4", "").strip()
        # live-strict selection configuration (defaults keep legacy behavior untouched)
        self.live_strict = bool(live_strict)
        self.expect_trd_env = (expect_trd_env or "").upper() or None
        self.expect_acc_type = (expect_acc_type or "").upper() or None
        self.expect_market = (expect_market or "").upper() or None
        # expect_last4 is strictly optional hardening for strict selection:
        # only honor it when explicitly provided by the caller; never fall back to
        # account_last4 here, otherwise strict mode would silently filter by 6219.
        self.expect_last4 = (expect_last4 or "").strip() or None
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

        # 1) 按账户后4位优先（仅当显式配置 FUTU_ACCOUNT_LAST4 时启用）
        if self.account_last4:
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

    @staticmethod
    def _row_matches_last4(row: dict[str, Any], expect_last4: str) -> tuple[bool, str]:
        """Return (matched, matched_field) for an account row against expected last4."""
        for key in ("uni_card_num", "card_num", "acc_id", "sim_acc_id", "real_acc_id", "acc_num"):
            value = row.get(key)
            if value is None:
                continue
            text = str(value)
            if text.endswith(expect_last4):
                return True, key
        return False, ""

    @staticmethod
    def _row_brief(row: dict[str, Any]) -> dict[str, Any]:
        """Compact view for error/log messages (no PII beyond last4)."""
        def _last4(v: Any) -> str:
            s = "" if v is None else str(v)
            return s[-4:] if s else ""
        return {
            "acc_id_last4": _last4(row.get("acc_id")),
            "uni_card_last4": _last4(row.get("uni_card_num")),
            "card_last4": _last4(row.get("card_num")),
            "trd_env": str(row.get("trd_env", "")).upper(),
            "acc_type": str(row.get("acc_type", "")).upper(),
            "acc_status": str(row.get("acc_status", "")).upper(),
            "trdmarket_auth": list(row.get("trdmarket_auth") or []),
        }

    def _pick_account_strict(self, accounts_df):
        """Live-strict selection by OpenD-returned triple (trd_env + acc_type + acc_status=ACTIVE).

        Required filters (always applied when configured):
          - trd_env  == self.expect_trd_env (e.g. REAL)
          - acc_type == self.expect_acc_type (e.g. MARGIN)
          - acc_status == ACTIVE (DISABLED/other statuses are rejected)
          - expect_market in trdmarket_auth (if expect_market configured)

        Optional hardening:
          - If self.expect_last4 is explicitly set (non-empty), also require last4 to match.
            When not set, last4 is NOT required — selection is driven purely by the triple.

        Exactly-one match required; 0 or >1 matches both raise FutuLiveAccountMismatchError.
        """
        records = accounts_df.to_dict(orient="records") if hasattr(accounts_df, "to_dict") else []
        if not records:
            raise FutuLiveAccountMismatchError("no accounts returned from OpenD")

        # last4 is now an optional hardening condition; only apply when explicitly provided.
        expect_last4 = (self.expect_last4 or "").strip() or None

        matches: list[tuple[dict[str, Any], str]] = []
        rejections: list[dict[str, Any]] = []
        for row in records:
            reasons: list[str] = []
            if self.expect_trd_env and str(row.get("trd_env", "")).upper() != self.expect_trd_env:
                reasons.append(f"trd_env!={self.expect_trd_env}")
            if self.expect_acc_type and str(row.get("acc_type", "")).upper() != self.expect_acc_type:
                reasons.append(f"acc_type!={self.expect_acc_type}")
            status = str(row.get("acc_status", "")).upper()
            if status and status != "ACTIVE":
                reasons.append(f"acc_status={status}")
            if self.expect_market:
                auth = [str(x).upper() for x in (row.get("trdmarket_auth") or [])]
                if auth and self.expect_market not in auth:
                    reasons.append(f"market {self.expect_market} not in trdmarket_auth")
            matched_field = "triple"
            if expect_last4:
                matched_last4, last4_field = self._row_matches_last4(row, expect_last4)
                if not matched_last4:
                    reasons.append(f"last4!={expect_last4}")
                else:
                    matched_field = last4_field
            if reasons:
                brief = self._row_brief(row)
                brief["reject_reasons"] = reasons
                rejections.append(brief)
                continue
            matches.append((row, matched_field))

        if len(matches) == 1:
            row, matched_field = matches[0]
            return row, f"strict:{matched_field}", records
        if not matches:
            raise FutuLiveAccountMismatchError(
                f"no account matches strict criteria (expect trd_env={self.expect_trd_env},"
                f" acc_type={self.expect_acc_type}, market={self.expect_market},"
                f" acc_status=ACTIVE, last4={expect_last4}); candidates={rejections}"
            )
        # multiple matches -> ambiguous, refuse to proceed
        ambiguous = [self._row_brief(r) for r, _ in matches]
        raise FutuLiveAccountMismatchError(
            f"multiple accounts matched strict criteria (expect trd_env={self.expect_trd_env},"
            f" acc_type={self.expect_acc_type}, market={self.expect_market},"
            f" acc_status=ACTIVE, last4={expect_last4}); matched={ambiguous}"
        )

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
                if self.live_strict:
                    raise FutuLiveAccountMismatchError("OpenD returned empty account list in live-strict mode")
                return {"accounts": [], "positions": [], "orders": [], "message": "no accounts"}

            if self.live_strict:
                row, picked_by, records = self._pick_account_strict(accounts)
            else:
                row, picked_by, records = self._pick_account(accounts)
            if row is None:
                return {"accounts": [], "positions": [], "orders": [], "message": "no matched account"}

            acc_id = int(row.get("acc_id") or row.get("sim_acc_id") or row.get("real_acc_id"))
            trd_env = self._resolve_trd_env(row)
            trd_env_name = "SIMULATE" if trd_env == futu.TrdEnv.SIMULATE else "REAL"
            acc_type_name = str(row.get("acc_type", "")).upper()
            auth_list = list(row.get("trdmarket_auth") or [])
            uni_last4 = (str(row.get("uni_card_num") or "")[-4:]) or ""

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
                "message": (
                    f"ok; picked_by={picked_by}; acc_id={acc_id}; env={trd_env_name};"
                    f" acc_type={acc_type_name}; auth={auth_list}; uni_last4={uni_last4}"
                ),
                "picked_env": trd_env_name,
                "picked_acc_id": acc_id,
                "picked_acc_type": acc_type_name,
                "picked_trdmarket_auth": auth_list,
                "picked_uni_last4": uni_last4,
            }
        finally:
            ctx.close()
