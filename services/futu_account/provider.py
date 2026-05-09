from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, List

from services.futu_opend import OpenDClient

from .models import FutuAccountSummary, FutuOrder, FutuPosition
from .quote_client import FutuQuoteClient
from .sdk_client import FutuLiveAccountMismatchError, FutuSdkClient

if TYPE_CHECKING:  # pragma: no cover - type hints only
    from vnpy.trader.engine import MainEngine
    from vnpy.trader.object import AccountData, OrderData, PositionData, TradeData

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


# Default freshness window for OmsEngine-driven snapshots. When the most recent
# EVENT_ACCOUNT/POSITION/ORDER/TRADE is older than this, the provider falls
# back to a one-shot SDK poll and emits a degradation event (requirement R3).
_DEFAULT_OMS_STALE_SECONDS = 60.0


class FutuAccountProvider:
    """Read-only account snapshot provider.

    Two data sources are supported:

    1. **OmsEngine** (preferred, event-driven) — attach a ``MainEngine`` via
       :meth:`attach_main_engine` so the provider reads account / position /
       order / trade snapshots from the vn.py OMS caches fed by FutuGateway
       pushes. No extra OpenD round-trips.
    2. **FutuSdkClient** (fallback, polling) — when no MainEngine is attached,
       or when OMS events are older than ``oms_stale_seconds``, the provider
       falls back to its historical SDK path (``account_snapshot`` /
       ``deal_list_today``). Every degradation is logged to
       ``state/runs/events.jsonl``.

    Public method signatures are unchanged from the SDK-only version so
    existing callers in ``sim_task`` / ``close_task`` / ``healthcheck`` keep
    working with zero edits.
    """

    def __init__(
        self,
        *,
        live_strict: bool = False,
        expect_trd_env: str | None = None,
        expect_acc_type: str | None = None,
        expect_market: str | None = None,
        expect_last4: str | None = None,
        main_engine: "MainEngine | None" = None,
        oms_stale_seconds: float = _DEFAULT_OMS_STALE_SECONDS,
        events_log_path: Path | None = None,
    ):
        self.live_strict = bool(live_strict)
        self.expect_trd_env = expect_trd_env
        self.expect_acc_type = expect_acc_type
        self.expect_market = expect_market
        self.expect_last4 = expect_last4

        # OMS integration (optional)
        self._main_engine: "MainEngine | None" = None
        self._oms_stale_seconds = float(oms_stale_seconds or _DEFAULT_OMS_STALE_SECONDS)
        self._last_event_ts: float = 0.0
        self._event_lock = threading.Lock()
        self._registered_event_types: set[str] = set()
        self.events_log_path = events_log_path
        if main_engine is not None:
            self.attach_main_engine(main_engine)

    # ------------------------------------------------------------------
    # OmsEngine integration
    # ------------------------------------------------------------------
    def attach_main_engine(self, main_engine: "MainEngine") -> None:
        """Bind a vn.py MainEngine as the primary data source.

        Registers EVENT_ACCOUNT / EVENT_POSITION / EVENT_ORDER / EVENT_TRADE
        callbacks that refresh the internal freshness heartbeat so the provider
        can decide whether to trust OMS caches or fall back to SDK polling.
        """
        if main_engine is None:
            return
        self._main_engine = main_engine
        try:
            from vnpy.trader.event import (
                EVENT_ACCOUNT,
                EVENT_ORDER,
                EVENT_POSITION,
                EVENT_TRADE,
            )
        except Exception as exc:  # pragma: no cover - vnpy always available in this project
            self._log_event("oms_attach_failed", level="ERROR", detail=str(exc))
            self._main_engine = None
            return
        event_engine = getattr(main_engine, "event_engine", None)
        if event_engine is None:
            self._log_event("oms_attach_no_event_engine", level="ERROR")
            return
        for event_type in (EVENT_ACCOUNT, EVENT_POSITION, EVENT_ORDER, EVENT_TRADE):
            if event_type in self._registered_event_types:
                continue
            event_engine.register(event_type, self._on_broker_event)
            self._registered_event_types.add(event_type)
        # Seed the heartbeat so is_oms_fresh() returns True immediately after
        # a successful FutuGateway.connect (which already emits EVENT_ACCOUNT /
        # EVENT_POSITION synchronously via query_account/query_position).
        self._last_event_ts = time.time()

    def detach_main_engine(self) -> None:
        """Break the OMS binding. Falls back to pure SDK polling."""
        self._main_engine = None
        self._registered_event_types.clear()
        self._last_event_ts = 0.0

    def _on_broker_event(self, _event) -> None:
        """EventEngine callback — only purpose is to refresh the heartbeat."""
        with self._event_lock:
            self._last_event_ts = time.time()

    def is_oms_fresh(self) -> bool:
        """Return True if OMS snapshots are recent enough to trust.

        False when either no MainEngine is attached, or the most recent
        broker event is older than ``oms_stale_seconds``.
        """
        if self._main_engine is None:
            return False
        if self._last_event_ts <= 0:
            return False
        return (time.time() - self._last_event_ts) <= self._oms_stale_seconds

    # ------------------------------------------------------------------
    # SDK fallback wiring
    # ------------------------------------------------------------------
    def _build_sdk(self) -> FutuSdkClient:
        return FutuSdkClient(
            live_strict=self.live_strict,
            expect_trd_env=self.expect_trd_env,
            expect_acc_type=self.expect_acc_type,
            expect_market=self.expect_market,
            expect_last4=self.expect_last4,
        )

    def _cache_key(self) -> str:
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

    # ------------------------------------------------------------------
    # Public API — event-driven OMS first, SDK fallback
    # ------------------------------------------------------------------
    def get_today_trades(self, symbol: str | None = None) -> list[datetime]:
        """Return today's trade timestamps (sorted asc).

        Prefers OmsEngine.get_all_trades() when a MainEngine is attached and
        the push heartbeat is fresh; otherwise falls back to
        ``deal_list_query`` via SDK and logs a degradation event.
        """
        if self.is_oms_fresh():
            return self._today_trades_from_oms(symbol)
        if self._main_engine is not None:
            self._log_event(
                "oms_stale_fallback_sdk",
                level="WARN",
                detail=f"get_today_trades symbol={symbol}",
            )
        deals = self._fetch_deals_today()
        return self._today_trades_from_deals(deals, symbol)

    def get_today_trade_details(self, symbol: str | None = None) -> list[dict]:
        """Return today's deals as structured dicts (price/qty/side/time/symbol).

        Used by LiveRiskContextBuilder to derive ``daily_new_pct`` directly
        from the broker's own deal flow. Same fallback strategy as
        :meth:`get_today_trades`.
        """
        if self.is_oms_fresh():
            return self._today_trade_details_from_oms(symbol)
        if self._main_engine is not None:
            self._log_event(
                "oms_stale_fallback_sdk",
                level="WARN",
                detail=f"get_today_trade_details symbol={symbol}",
            )
        deals = self._fetch_deals_today()
        return self._today_trade_details_from_deals(deals, symbol)

    def get_summary(self) -> FutuAccountSummary:
        """Return a ``FutuAccountSummary`` composed from account/position/order.

        When a MainEngine is attached and OMS is fresh, reads from
        ``get_all_accounts/positions/orders``; otherwise falls back to the
        historical ``sdk.account_snapshot()`` path (which also handles
        ``live_account_mismatch`` detection).
        """
        if self.is_oms_fresh():
            summary = self._summary_from_oms()
            if summary is not None:
                return summary
        if self._main_engine is not None:
            self._log_event(
                "oms_stale_fallback_sdk",
                level="WARN",
                detail="get_summary",
            )
        return self._summary_from_sdk()

    # ------------------------------------------------------------------
    # OMS-backed implementations
    # ------------------------------------------------------------------
    def _today_trades_from_oms(self, symbol: str | None) -> list[datetime]:
        assert self._main_engine is not None
        today_local = datetime.now().date()
        want = self._normalize_symbol_key(symbol) if symbol else None
        out: list[datetime] = []
        for trade in self._main_engine.get_all_trades():
            dt = self._coerce_local_datetime(getattr(trade, "datetime", None))
            if dt is None or dt.date() != today_local:
                continue
            if want and self._normalize_symbol_key(getattr(trade, "symbol", "")) != want:
                continue
            out.append(dt)
        out.sort()
        return out

    def _today_trade_details_from_oms(self, symbol: str | None) -> list[dict]:
        assert self._main_engine is not None
        today_local = datetime.now().date()
        want = self._normalize_symbol_key(symbol) if symbol else None
        out: list[dict] = []
        for trade in self._main_engine.get_all_trades():
            dt = self._coerce_local_datetime(getattr(trade, "datetime", None))
            if dt is None or dt.date() != today_local:
                continue
            code = str(getattr(trade, "symbol", "") or "")
            if want and self._normalize_symbol_key(code) != want:
                continue
            qty = float(getattr(trade, "volume", 0) or 0)
            price = float(getattr(trade, "price", 0) or 0)
            side = getattr(getattr(trade, "direction", None), "value", None) or ""
            side = str(side).upper()
            # vn.py Direction values are "多" / "空"; map to BUY / SELL to keep
            # downstream filters (which test side.startswith("BUY")) stable.
            if side == "多" or side == "LONG":
                side = "BUY"
            elif side == "空" or side == "SHORT":
                side = "SELL"
            out.append({
                "symbol": self._normalize_symbol_key(code) or code,
                "raw_code": code,
                "side": side,
                "qty": qty,
                "price": price,
                "notional": round(qty * price, 4),
                "datetime": dt,
            })
        out.sort(key=lambda r: r["datetime"])
        return out

    def _summary_from_oms(self) -> FutuAccountSummary | None:
        """Build a FutuAccountSummary from OmsEngine caches.

        Returns None when the caches are not yet populated (e.g. within the
        first few hundred ms after connect) so the caller can gracefully fall
        back to SDK path.
        """
        assert self._main_engine is not None
        try:
            accounts: list[AccountData] = list(self._main_engine.get_all_accounts())
            positions: list[PositionData] = list(self._main_engine.get_all_positions())
            orders: list[OrderData] = list(self._main_engine.get_all_orders())
        except Exception as exc:  # pragma: no cover - defensive
            self._log_event("oms_read_failed", level="ERROR", detail=str(exc))
            return None
        if not accounts and not positions and not orders:
            return None

        env = (self.expect_trd_env or "").upper() or "UNKNOWN"
        acct = accounts[0] if accounts else None
        total_assets = float(getattr(acct, "balance", 0) or 0) if acct else None
        # AccountData has no separate cash/buying_power in vn.py core; expose
        # balance as total_assets, frozen→cash/buying_power subtraction heuristic.
        frozen = float(getattr(acct, "frozen", 0) or 0) if acct else 0.0
        cash_val = (total_assets - frozen) if (acct and total_assets is not None) else None
        buying_power = cash_val

        pos_items = [
            FutuPosition(
                code=str(getattr(p, "symbol", "")),
                name=str(getattr(p, "symbol", "")),
                qty=float(getattr(p, "volume", 0) or 0),
                market_val=(float(getattr(p, "volume", 0) or 0) * float(getattr(p, "price", 0) or 0))
                if getattr(p, "price", None) is not None
                else None,
                pl_ratio=None,
            )
            for p in positions[:10]
        ]
        order_items = [
            FutuOrder(
                code=str(getattr(o, "symbol", "")),
                side=str(getattr(getattr(o, "direction", None), "value", "")),
                qty=float(getattr(o, "volume", 0) or 0),
                status=str(getattr(getattr(o, "status", None), "value", "")),
            )
            for o in orders[:10]
        ]

        # Live-strict identity check via OMS account gateway_name + accountid.
        # When expect_trd_env is set but no AccountData has arrived yet, we
        # return None to defer to SDK path (which has the richer acc_list
        # selection).
        if self.live_strict and acct is None:
            return None

        return FutuAccountSummary(
            status="connected",
            account_count=len(accounts),
            env=env,
            total_assets=total_assets,
            cash=cash_val,
            buying_power=buying_power,
            positions=pos_items,
            orders=order_items,
            message=f"oms; accounts={len(accounts)}; positions={len(positions)}; orders={len(orders)}",
        )

    # ------------------------------------------------------------------
    # SDK-backed helpers (legacy path, also used for fallback)
    # ------------------------------------------------------------------
    def _today_trades_from_deals(self, deals: list[dict], symbol: str | None) -> list[datetime]:
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
            if parsed is None or parsed.date() != today:
                continue
            trade_times.append(parsed)
        trade_times.sort()
        return trade_times

    def _today_trade_details_from_deals(self, deals: list[dict], symbol: str | None) -> list[dict]:
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

    def _summary_from_sdk(self) -> FutuAccountSummary:
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

    # ------------------------------------------------------------------
    # Degradation event logger
    # ------------------------------------------------------------------
    def _log_event(self, event: str, *, level: str = "INFO", detail: str | None = None) -> None:
        """Append a one-line JSON record to ``state/runs/events.jsonl``.

        Never raises — logging must not break the trading path. When
        ``events_log_path`` is unset we try a sensible default rooted at the
        current working directory's ``state/runs/``; if that fails silently we
        at least print to stderr so CI can surface it.
        """
        path = self.events_log_path
        if path is None:
            try:
                path = Path.cwd() / "state" / "runs" / "events.jsonl"
            except Exception:
                path = None
        payload = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "source": "futu_account_provider",
            "level": level,
            "event": event,
        }
        if detail:
            payload["detail"] = detail
        try:
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception:  # pragma: no cover - best effort only
            try:
                import sys
                sys.stderr.write(f"[futu_account_provider] {level} {event} {detail or ''}\n")
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Static helpers (unchanged)
    # ------------------------------------------------------------------
    @staticmethod
    def _coerce_local_datetime(value) -> datetime | None:
        """Normalize vn.py TradeData.datetime to a naive *local* datetime.

        vn.py generally stores tz-aware datetimes from the gateway; we drop tz
        info after converting to local so ``today_local = datetime.now().date()``
        comparisons stay consistent with the SDK path.
        """
        if value is None:
            return None
        if not isinstance(value, datetime):
            return None
        if value.tzinfo is None:
            return value
        try:
            return value.astimezone().replace(tzinfo=None)
        except Exception:
            return value.replace(tzinfo=None)

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
