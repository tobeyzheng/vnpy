from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import List

from services.futu_opend import OpenDClient

from .models import FutuAccountSummary, FutuOrder, FutuPosition
from .quote_client import FutuQuoteClient
from .sdk_client import FutuLiveAccountMismatchError, FutuSdkClient


# Module-level TTL cache for deal_list_query. OpenD rate-limits this call and
# portfolio mode may query the same underlying account several times per loop;
# caching avoids hammering the gateway while still refreshing within one
# 5-minute cycle. The cache is keyed by ``(pid, cache_bucket)`` so forked
# sub-processes do not share stale data.
_DEAL_CACHE_TTL_SECONDS = 30.0
_deal_cache_lock = threading.Lock()
_deal_cache: dict[str, tuple[float, list[dict]]] = {}


def _deal_cache_get(key: str) -> list[dict] | None:
    with _deal_cache_lock:
        entry = _deal_cache.get(key)
        if entry is None:
            return None
        expiry, payload = entry
        if expiry < time.time():
            _deal_cache.pop(key, None)
            return None
        return list(payload)


def _deal_cache_set(key: str, payload: list[dict]) -> None:
    with _deal_cache_lock:
        _deal_cache[key] = (time.time() + _DEAL_CACHE_TTL_SECONDS, list(payload))


class FutuAccountProvider:
    """Read-only account snapshot provider."""

    def __init__(
        self,
        *,
        live_strict: bool = False,
        expect_trd_env: str | None = None,
        expect_acc_type: str | None = None,
        expect_market: str | None = None,
        expect_last4: str | None = None,
    ):
        self.live_strict = bool(live_strict)
        self.expect_trd_env = expect_trd_env
        self.expect_acc_type = expect_acc_type
        self.expect_market = expect_market
        self.expect_last4 = expect_last4

    def _build_sdk(self) -> FutuSdkClient:
        return FutuSdkClient(
            live_strict=self.live_strict,
            expect_trd_env=self.expect_trd_env,
            expect_acc_type=self.expect_acc_type,
            expect_market=self.expect_market,
            expect_last4=self.expect_last4,
        )

    # ------------------------------------------------------------------
    # Deal fetch with TTL cache + retries
    # ------------------------------------------------------------------
    def _cache_key(self) -> str:
        import os
        parts = [
            str(os.getpid()),
            str(self.expect_trd_env or ""),
            str(self.expect_acc_type or ""),
            str(self.expect_market or ""),
            str(self.expect_last4 or ""),
            "strict" if self.live_strict else "lenient",
        ]
        return "|".join(parts)

    def _fetch_deals_today(self, *, retries: int = 3, retry_delay: float = 0.4) -> list[dict]:
        """Fetch today's raw deal records with caching and bounded retries.

        Raises the final exception on persistent failure so callers can fail
        closed (requirement 7.4).
        """
        key = self._cache_key()
        cached = _deal_cache_get(key)
        if cached is not None:
            return cached

        last_exc: Exception | None = None
        for attempt in range(1, max(retries, 1) + 1):
            try:
                sdk = self._build_sdk()
                deals = sdk.deal_list_today() or []
                _deal_cache_set(key, deals)
                return list(deals)
            except Exception as exc:  # pragma: no cover - depends on OpenD
                last_exc = exc
                if attempt >= retries:
                    break
                time.sleep(retry_delay * attempt)
        assert last_exc is not None
        raise last_exc

    def get_today_trades(self, symbol: str | None = None) -> list[datetime]:
        """获取今日成交记录的时间戳列表（基于 Futu deal_list_query 成交流水）。

        Args:
            symbol: 可选标的符号，支持 "NVDA.US" / "US.NVDA" / "NVDA" 三种格式；
                    为空或 None 时返回当前账户今日全部成交记录。

        Returns:
            今日成交记录的 datetime 列表，按时间升序排列。
            出现异常时抛出，由调用方决定是否降级。
        """
        deals = self._fetch_deals_today()
        today = datetime.now().date()
        want = self._normalize_symbol_key(symbol) if symbol else None

        trade_times: list[datetime] = []
        for row in deals:
            code = str(row.get("code", ""))
            if want and self._normalize_symbol_key(code) != want:
                continue
            create_time_str = (
                row.get("create_time")
                or row.get("deal_time")
                or row.get("update_time")
                or row.get("updated_time")
            )
            if not create_time_str:
                continue
            parsed = self._parse_futu_datetime(str(create_time_str))
            if parsed is None:
                continue
            if parsed.date() != today:
                continue
            trade_times.append(parsed)

        trade_times.sort()
        return trade_times

    def get_today_trade_details(self, symbol: str | None = None) -> list[dict]:
        """Return today's deals as structured dicts (price/qty/side/time/symbol).

        Used by LiveRiskContextBuilder to derive ``daily_new_pct`` without
        relying on filesystem timestamps of ``OrderStateStore`` files, which
        were unreliable (ctime mutates on status updates on ext4/xfs).
        """
        deals = self._fetch_deals_today()
        today = datetime.now().date()
        want = self._normalize_symbol_key(symbol) if symbol else None

        out: list[dict] = []
        for row in deals:
            code = str(row.get("code", ""))
            if want and self._normalize_symbol_key(code) != want:
                continue
            create_time_str = (
                row.get("create_time")
                or row.get("deal_time")
                or row.get("update_time")
                or row.get("updated_time")
            )
            if not create_time_str:
                continue
            parsed = self._parse_futu_datetime(str(create_time_str))
            if parsed is None or parsed.date() != today:
                continue
            side = str(row.get("trd_side") or row.get("side") or "").upper()
            qty = float(row.get("qty", row.get("volume", 0)) or 0)
            price = float(row.get("price", 0) or 0)
            out.append({
                "symbol": self._normalize_symbol_key(code) or code,
                "raw_code": code,
                "side": side,
                "qty": qty,
                "price": price,
                "notional": round(qty * price, 4),
                "datetime": parsed,
            })
        out.sort(key=lambda r: r["datetime"])
        return out

    @staticmethod
    def _normalize_symbol_key(symbol: str) -> str:
        """Normalize symbol/code to a comparable key.

        Accepts 'NVDA.US' (pipeline format), 'US.NVDA' (futu format) and bare 'NVDA'.
        """
        text = str(symbol or "").upper().strip()
        if not text:
            return ""
        if "." in text:
            left, right = text.split(".", 1)
            # market prefix form: US.NVDA / HK.00700
            if left in {"US", "HK", "SH", "SZ", "SHA", "SHE"}:
                return right
            # symbol.market form: NVDA.US
            return left
        return text

    @staticmethod
    def _parse_futu_datetime(text: str) -> datetime | None:
        """Parse Futu deal/order time strings. Futu uses '%Y-%m-%d %H:%M:%S'."""
        if not text:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None

    def get_summary(self) -> FutuAccountSummary:
        probe = OpenDClient().probe()
        if not probe.reachable:
            return FutuAccountSummary(status="disconnected", message=f"OpenD unreachable: {probe.message}")

        sdk = self._build_sdk()
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
        except FutuLiveAccountMismatchError as e:
            return FutuAccountSummary(
                status="live_account_mismatch",
                account_count=0,
                env=(self.expect_trd_env or "").upper() or "UNKNOWN",
                message=f"live-strict account selection failed: {e}",
            )
        except Exception as e:
            return FutuAccountSummary(
                status="connected",
                account_count=0,
                env=(self.expect_trd_env or "SIMULATE").upper(),
                message=f"OpenD reachable; SDK query failed: {e}",
            )

        accounts = snapshot.get("accounts", [])
        assets = snapshot.get("assets", [])
        positions = snapshot.get("positions", [])
        orders = snapshot.get("orders", [])
        picked_env = (snapshot.get("picked_env") or "").upper() or "UNKNOWN"

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
            env=picked_env,
            total_assets=float(asset0.get("total_assets", 0) or 0) if asset0 else None,
            cash=float(asset0.get("cash", 0) or 0) if asset0 else None,
            buying_power=float(asset0.get("power", 0) or 0) if asset0 else None,
            positions=pos_items,
            orders=order_items,
            message=snapshot.get("message", "ok"),
        )

    def get_watchlist_snapshot(self, codes: List[str]) -> dict:
        probe = OpenDClient().probe()
        if not probe.reachable:
            return {"status": "disconnected", "items": [], "message": probe.message}
        quote = FutuQuoteClient()
        ok, msg = quote.availability()
        if not ok:
            return {"status": "connected", "items": [], "message": f"SDK unavailable: {msg}"}
        try:
            rows = quote.get_snapshot(codes)
            items = []
            for row in rows:
                last_price = row.get("last_price")
                prev_close = row.get("prev_close_price")
                change_pct = None
                try:
                    if last_price is not None and prev_close not in (None, 0, 0.0):
                        change_pct = round((float(last_price) - float(prev_close)) / float(prev_close) * 100, 3)
                except Exception:
                    change_pct = None
                items.append(
                    {
                        "code": str(row.get("code", "")),
                        "price": last_price,
                        "change_pct": change_pct,
                        "volume": row.get("volume"),
                        "turnover": row.get("turnover"),
                        "amplitude": row.get("amplitude"),
                        "bid_price": row.get("bid_price"),
                        "ask_price": row.get("ask_price"),
                    }
                )
            return {"status": "connected", "items": items, "message": "ok"}
        except Exception as e:
            return {"status": "connected", "items": [], "message": f"quote query failed: {e}"}
