from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from services.futu_account.models import FutuAccountSummary
from services.trade_state import OrderStateStore


@dataclass(frozen=True)
class LiveRiskContext:
    total_nav: float
    cash: float
    buying_power: float
    market_exposure_pct: float
    symbol_exposure_pct: float
    daily_new_pct: float
    current_drawdown_pct: float
    position_count: int
    market_existing_value: float = 0.0
    symbol_existing_value: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LiveRiskContextBuilder:
    def __init__(self, order_store: OrderStateStore):
        self.order_store = order_store

    def build(self, account: FutuAccountSummary, *, symbol: str = "", order_value: float = 0.0) -> LiveRiskContext:
        total_nav = self._total_nav(account)
        cash = float(account.cash or 0.0)
        buying_power = float(account.buying_power or cash)
        current_market_value = sum(float(p.market_val or 0.0) for p in account.positions)
        symbol_market_value = sum(float(p.market_val or 0.0) for p in account.positions if self._same_symbol(p.code, symbol))
        projected_symbol_value = symbol_market_value + max(float(order_value or 0.0), 0.0)
        daily_new_value = self._today_buy_notional()
        return LiveRiskContext(
            total_nav=round(total_nav, 4),
            cash=round(cash, 4),
            buying_power=round(buying_power, 4),
            market_exposure_pct=self._pct(current_market_value, total_nav),
            symbol_exposure_pct=self._pct(projected_symbol_value, total_nav),
            daily_new_pct=self._pct(daily_new_value, total_nav),
            current_drawdown_pct=self._drawdown(account, symbol=symbol),
            position_count=len(account.positions),
            market_existing_value=round(max(float(current_market_value or 0.0), 0.0), 4),
            symbol_existing_value=round(max(float(symbol_market_value or 0.0), 0.0), 4),
        )

    def _total_nav(self, account: FutuAccountSummary) -> float:
        total_assets = float(account.total_assets or 0.0)
        if total_assets > 0:
            return total_assets
        return max(float(account.cash or 0.0) + sum(float(p.market_val or 0.0) for p in account.positions), 0.0)

    def _today_buy_notional(self) -> float:
        today = date.today()
        total = 0.0
        for state in self.order_store.list():
            path = self.order_store.root / f"{state.request_id}.json"
            try:
                st = path.stat()
            except OSError:
                continue
            # Prefer birth/ctime (creation time) over mtime: an order placed yesterday
            # whose status keeps mutating today (fills, reconciles) would otherwise
            # be mis-counted toward today's notional.
            created_ts = getattr(st, "st_birthtime", None) or st.st_ctime or st.st_mtime
            try:
                if date.fromtimestamp(created_ts) != today:
                    continue
            except (OSError, ValueError, OverflowError):
                continue
            if state.side == "BUY" and state.status in {"approved", "submitting", "submitted", "partial_filled", "filled"}:
                total += max(float(state.qty or 0), 0.0) * max(float(state.price or 0.0), 0.0)
        return total

    def _drawdown(self, account: FutuAccountSummary, *, symbol: str = "") -> float:
        # Per-symbol drawdown: only inspect the position matching the requested symbol.
        # If the account has no position for this symbol, drawdown is treated as 0.
        if not symbol:
            return 0.0
        losses = [
            abs(float(p.pl_ratio or 0.0)) / 100.0
            for p in account.positions
            if self._same_symbol(p.code, symbol)
            and p.pl_ratio is not None
            and float(p.pl_ratio or 0.0) < 0
        ]
        return round(max(losses, default=0.0), 4)

    def _pct(self, value: float, total: float) -> float:
        return round(max(float(value or 0.0), 0.0) / total, 6) if total > 0 else 0.0

    def _same_symbol(self, code: str, symbol: str) -> bool:
        if not symbol:
            return False
        left = str(code).replace("HK.", "").replace("US.", "").split(".")[0].upper()
        right = str(symbol).replace("HK.", "").replace("US.", "").split(".")[0].upper()
        return left == right
