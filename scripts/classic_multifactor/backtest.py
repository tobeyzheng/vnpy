from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from statistics import mean, pstdev
from typing import Any

from vnpy.trader.object import BarData

from scripts.classic_multifactor.minute_guard import MinuteTradeGuard, MinuteTradeGuardConfig
from scripts.classic_multifactor.model import ClassicMultiFactorModel

from scripts.classic_multifactor.risk import ClassicOrderRiskManager


@dataclass
class TradeRecord:
    datetime: str
    side: str
    price: float
    qty: int
    commission: float
    cash: float
    position: int
    equity: float
    raw_score: float
    reason: str


@dataclass
class EquityPoint:
    datetime: str
    equity: float
    cash: float
    position: int
    close_price: float
    drawdown_pct: float


class ClassicSingleSymbolBacktester:
    """Lightweight single-symbol account harness using vn.py BarData and shared strategy/risk modules."""

    def __init__(self, *, model: ClassicMultiFactorModel, risk_manager: ClassicOrderRiskManager, capital: float, minute_guard: MinuteTradeGuard | None = None):
        self.model = model
        self.risk_manager = risk_manager
        self.capital = float(capital)
        self.minute_guard = minute_guard or MinuteTradeGuard(MinuteTradeGuardConfig())


    def run(self, vt_symbol: str, bars: list[BarData]) -> dict[str, Any]:
        if len(bars) < self.model.config.warmup_window + 1:
            return {"status": "no_data", "message": "历史K线不足", "bar_count": len(bars)}
        cash = self.capital
        position = 0
        entry_price = 0.0
        highest_close = 0.0
        peak_equity = cash
        trades: list[TradeRecord] = []
        equity_curve: list[EquityPoint] = []
        factors: list[dict[str, Any]] = []
        trade_times = []
        last_trade_at = None
        entry_at = None

        for index in range(self.model.config.warmup_window, len(bars) - 1):

            signal_bars = bars[: index + 1]
            signal_bar = bars[index]
            trade_bar = bars[index + 1]
            equity_before = cash + position * signal_bar.close_price
            highest_for_decision = max(highest_close, signal_bar.close_price) if position > 0 else 0.0
            decision = self.model.decide_target(
                vt_symbol=vt_symbol,
                bars=signal_bars,
                current_qty=position,
                entry_price=entry_price,
                highest_close=highest_for_decision,
                equity=equity_before,
                cash=cash,
                trade_price=trade_bar.open_price or trade_bar.close_price,
            )
            if decision.factor:
                factors.append(asdict(decision.factor))
            target_qty = decision.target_qty
            side = decision.side
            price = trade_bar.open_price or trade_bar.close_price
            if side in {"BUY", "SELL"} and price > 0:
                if side == "BUY":
                    guard = self.minute_guard.can_enter(
                        trade_bar.datetime,
                        trade_times=trade_times,
                        last_trade_at=last_trade_at,
                        exchange_tz="America/New_York",
                    )
                else:
                    hard_exit = decision.reason in {"stop_loss", "atr_stop_loss", "trailing_stop", "atr_trailing_stop", "take_profit", "atr_take_profit"}
                    guard = self.minute_guard.can_exit(trade_bar.datetime, entry_at=entry_at, hard_exit=hard_exit)
                if not guard.allowed:
                    continue
                risk = self.risk_manager.size_and_check(

                    symbol=vt_symbol,
                    side=side,
                    price=price,
                    cash=cash,
                    equity=equity_before,
                    current_qty=position,
                    target_qty=target_qty,
                    factor=decision.factor,
                )
                if risk.allowed and risk.qty > 0:
                    fill_price = self.apply_slippage(price, side)
                    qty = risk.qty
                    turnover = fill_price * qty
                    commission = max(1.0, turnover * self.model.config.commission_rate)
                    if side == "BUY":
                        cash -= turnover + commission
                        position += qty
                        entry_price = fill_price
                        highest_close = signal_bar.close_price
                        entry_at = trade_bar.datetime
                        trade_side = "buy"

                    else:
                        qty = min(qty, position)
                        turnover = fill_price * qty
                        commission = max(1.0, turnover * self.model.config.commission_rate)
                        cash += turnover - commission
                        position -= qty
                        if position == 0:
                            entry_price = 0.0
                            highest_close = 0.0
                            entry_at = None
                        trade_side = "sell"
                    trade_times.append(trade_bar.datetime)
                    last_trade_at = trade_bar.datetime
                    equity_after = cash + position * trade_bar.close_price

                    trades.append(TradeRecord(trade_bar.datetime.isoformat(), trade_side, round(fill_price, 4), qty, round(commission, 4), round(cash, 2), position, round(equity_after, 2), decision.factor.raw_score if decision.factor else 0.0, decision.reason))
            if position > 0:
                highest_close = max(highest_close, trade_bar.close_price)
            equity = cash + position * trade_bar.close_price
            peak_equity = max(peak_equity, equity)
            drawdown = (equity / peak_equity - 1.0) * 100 if peak_equity else 0.0
            equity_curve.append(EquityPoint(trade_bar.datetime.isoformat(), round(equity, 2), round(cash, 2), position, round(trade_bar.close_price, 4), round(drawdown, 4)))

        if position > 0 and bars:
            last_bar = bars[-1]
            fill_price = self.apply_slippage(last_bar.close_price, "SELL")
            turnover = fill_price * position
            commission = max(1.0, turnover * self.model.config.commission_rate)
            cash += turnover - commission
            trades.append(TradeRecord(last_bar.datetime.isoformat(), "sell", round(fill_price, 4), position, round(commission, 4), round(cash, 2), 0, round(cash, 2), factors[-1]["raw_score"] if factors else 0.0, "final_close"))
            position = 0
        return {
            "status": "ok",
            "bar_count": len(bars),
            "stats": self.calculate_stats(equity_curve, trades),
            "latest_factor": factors[-1] if factors else None,
            "trades": [asdict(trade) for trade in trades],
            "equity_curve": [asdict(row) for row in equity_curve],
        }

    def apply_slippage(self, price: float, side: str) -> float:
        slip = price * self.model.config.slippage_bps / 10000.0
        return price + slip if side.upper() == "BUY" else max(0.01, price - slip)

    def calculate_stats(self, equity_curve: list[EquityPoint], trades: list[TradeRecord]) -> dict[str, Any]:
        if not equity_curve:
            return {"initial_capital": self.capital, "final_equity": self.capital, "total_return_pct": 0.0, "trade_count": len(trades)}
        equities = [row.equity for row in equity_curve]
        returns = [(equities[i] / equities[i - 1] - 1.0) for i in range(1, len(equities)) if equities[i - 1]]
        avg_ret = mean(returns) if returns else 0.0
        std_ret = pstdev(returns) if len(returns) > 1 else 0.0
        final_equity = equities[-1]
        total_return_pct = (final_equity / self.capital - 1.0) * 100 if self.capital else 0.0
        max_drawdown_pct = min(row.drawdown_pct for row in equity_curve)
        sharpe = avg_ret / std_ret * math.sqrt(252) if std_ret > 0 else 0.0
        years = max(len(equity_curve) / 252, 1 / 252)
        annual_return_pct = ((final_equity / self.capital) ** (1 / years) - 1.0) * 100 if self.capital and final_equity > 0 else 0.0
        return {
            "initial_capital": round(self.capital, 2),
            "final_equity": round(final_equity, 2),
            "total_return_pct": round(total_return_pct, 4),
            "annual_return_pct": round(annual_return_pct, 4),
            "max_drawdown_pct": round(max_drawdown_pct, 4),
            "sharpe_ratio": round(sharpe, 4),
            "trade_count": len(trades),
            "buy_count": sum(1 for trade in trades if trade.side == "buy"),
            "sell_count": sum(1 for trade in trades if trade.side == "sell"),
        }
