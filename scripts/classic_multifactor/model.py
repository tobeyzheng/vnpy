from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from vnpy.trader.object import BarData
from vnpy.trader.utility import ArrayManager


@dataclass(frozen=True)
class ClassicMultiFactorConfig:
    fast_window: int = 10
    slow_window: int = 60
    momentum_window: int = 20
    atr_window: int = 14
    entry_score: float = 0.62
    exit_score: float = 0.46
    stop_loss_pct: float = 0.08
    take_profit_pct: float = 0.22
    trailing_stop_pct: float = 0.12
    max_position_pct: float = 0.35
    max_order_value: float = 5000.0
    commission_rate: float = 0.0003
    slippage_bps: float = 5.0
    signal_interval_minutes: int = 1
    confirm_bars: int = 1
    min_volume_ratio: float = 0.0
    min_atr_pct: float = 0.0
    min_trend_score: float = 0.60

    stop_atr: float = 0.0
    take_profit_atr: float = 0.0
    trailing_atr: float = 0.0

    @property
    def warmup_window(self) -> int:
        return (max(self.fast_window, self.slow_window, self.momentum_window, self.atr_window, 20) + 2) * max(int(self.signal_interval_minutes), 1)



@dataclass(frozen=True)
class FactorSnapshot:
    datetime: str
    symbol: str
    close: float
    fast_ma: float
    slow_ma: float
    return_5d: float
    return_20d: float
    rsi: float
    volume_ratio: float
    atr_value: float
    atr_pct: float
    trend_score: float

    momentum_score: float
    volume_score: float
    low_risk_score: float
    breakout_score: float
    raw_score: float
    signal: str
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TargetDecision:
    side: str
    target_qty: int
    reason: str
    factor: FactorSnapshot | None


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    if not math.isfinite(value):
        return low
    return max(low, min(high, value))


def finite(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


class ClassicMultiFactorModel:
    """No-LLM deterministic multi-factor model built on vn.py ArrayManager."""

    def __init__(self, config: ClassicMultiFactorConfig):
        self.config = config

    def evaluate_bars(self, vt_symbol: str, bars: list[BarData]) -> FactorSnapshot | None:
        signal_bars = self._signal_bars(bars)
        if len(signal_bars) < self._signal_warmup_window():
            return None
        return self._evaluate_signal_bars(vt_symbol, signal_bars)

    def _evaluate_signal_bars(self, vt_symbol: str, bars: list[BarData]) -> FactorSnapshot | None:
        if len(bars) < self._signal_warmup_window():
            return None
        am = self._array_manager(bars)
        close = finite(am.close[-1])

        if close <= 0:
            return None

        fast_ma = finite(am.sma(self.config.fast_window))
        slow_ma = finite(am.sma(self.config.slow_window))
        return_5d = finite(am.roc(5)) / 100.0
        return_20d = finite(am.roc(self.config.momentum_window)) / 100.0
        rsi_value = finite(am.rsi(14), 50.0)
        atr_value = finite(am.atr(self.config.atr_window))
        atr_ratio = atr_value / close if close else 0.0
        recent_volume = [finite(v) for v in am.volume[-20:] if finite(v) > 0]
        avg_volume = sum(recent_volume) / len(recent_volume) if recent_volume else 0.0
        volume_ratio = finite(am.volume[-1]) / avg_volume if avg_volume else 1.0
        high20 = max(finite(v) for v in am.high[-20:])

        trend_score = 0.0
        trend_score += 0.35 if fast_ma > slow_ma else 0.0
        trend_score += 0.25 if close > fast_ma else 0.0
        trend_score += 0.25 if close > slow_ma else 0.0
        trend_score += 0.15 if high20 > 0 and close >= high20 * 0.98 else 0.0
        momentum_score = clamp(0.5 + return_20d * 2.5 + return_5d)
        volume_score = clamp(volume_ratio / 2.0)
        low_risk_score = clamp(1.0 - atr_ratio * 8.0)
        breakout_score = 1.0 if high20 > 0 and close >= high20 * 0.995 else 0.5 if high20 > 0 and close >= high20 * 0.97 else 0.0
        raw_score = round(
            0.35 * trend_score
            + 0.25 * momentum_score
            + 0.15 * volume_score
            + 0.15 * low_risk_score
            + 0.10 * breakout_score,
            4,
        )

        blocked_reasons: list[str] = []
        if volume_ratio < self.config.min_volume_ratio:
            blocked_reasons.append("volume_ratio_low")
        if atr_ratio < self.config.min_atr_pct:
            blocked_reasons.append("atr_pct_too_low")

        if raw_score >= self.config.entry_score and trend_score >= self.config.min_trend_score and return_20d > 0 and not blocked_reasons:

            signal = "long_entry"
            reason = "classic_multifactor_entry"

        elif raw_score <= self.config.exit_score or (slow_ma > 0 and close < slow_ma):
            signal = "exit_or_flat"
            reason = "classic_multifactor_exit"
        elif blocked_reasons:
            signal = "hold"
            reason = "filtered:" + ",".join(blocked_reasons)
        else:
            signal = "hold"
            reason = "classic_multifactor_hold"


        return FactorSnapshot(
            datetime=bars[-1].datetime.isoformat(),
            symbol=vt_symbol,
            close=round(close, 4),
            fast_ma=round(fast_ma, 4),
            slow_ma=round(slow_ma, 4),
            return_5d=round(return_5d, 6),
            return_20d=round(return_20d, 6),
            rsi=round(rsi_value, 4),
            volume_ratio=round(volume_ratio, 4),
            atr_value=round(atr_value, 6),
            atr_pct=round(atr_ratio, 6),

            trend_score=round(trend_score, 4),
            momentum_score=round(momentum_score, 4),
            volume_score=round(volume_score, 4),
            low_risk_score=round(low_risk_score, 4),
            breakout_score=round(breakout_score, 4),
            raw_score=raw_score,
            signal=signal,
            reason=reason,
            metadata={"source": "vnpy.ArrayManager", "windows": self._windows(), "blocked_reasons": blocked_reasons, "signal_interval_minutes": self.config.signal_interval_minutes},

        )

    def decide_target(self, *, vt_symbol: str, bars: list[BarData], current_qty: int, entry_price: float, highest_close: float, equity: float, cash: float, trade_price: float) -> TargetDecision:
        factor = self.evaluate_bars(vt_symbol, bars)
        if not factor:
            return TargetDecision("HOLD", current_qty, "warming_up", None)
        if current_qty > 0:
            pnl_pct = factor.close / entry_price - 1.0 if entry_price else 0.0
            trail_pct = factor.close / highest_close - 1.0 if highest_close else 0.0
            atr_value = max(float(factor.atr_value), 0.0)
            atr_stop = atr_value * self.config.stop_atr if self.config.stop_atr > 0 else 0.0
            atr_take_profit = atr_value * self.config.take_profit_atr if self.config.take_profit_atr > 0 else 0.0
            atr_trailing = atr_value * self.config.trailing_atr if self.config.trailing_atr > 0 else 0.0
            if atr_stop > 0 and factor.close <= entry_price - atr_stop:
                return TargetDecision("SELL", 0, "atr_stop_loss", factor)
            if atr_take_profit > 0 and factor.close >= entry_price + atr_take_profit:
                return TargetDecision("SELL", 0, "atr_take_profit", factor)
            if atr_trailing > 0 and highest_close > 0 and factor.close <= highest_close - atr_trailing:
                return TargetDecision("SELL", 0, "atr_trailing_stop", factor)
            if pnl_pct <= -self.config.stop_loss_pct:
                return TargetDecision("SELL", 0, "stop_loss", factor)
            if pnl_pct >= self.config.take_profit_pct:
                return TargetDecision("SELL", 0, "take_profit", factor)
            if trail_pct <= -self.config.trailing_stop_pct:
                return TargetDecision("SELL", 0, "trailing_stop", factor)

            if factor.signal == "exit_or_flat":
                return TargetDecision("SELL", 0, factor.reason, factor)
            return TargetDecision("HOLD", current_qty, factor.reason, factor)
        if factor.signal != "long_entry" or trade_price <= 0:
            return TargetDecision("HOLD", 0, factor.reason, factor)
        if not self._entry_confirmed(vt_symbol, bars):
            return TargetDecision("HOLD", 0, "entry_not_confirmed", factor)

        order_value = min(equity * self.config.max_position_pct, self.config.max_order_value, cash)
        target_qty = max(0, int(order_value // trade_price))
        return TargetDecision("BUY" if target_qty > 0 else "HOLD", target_qty, factor.reason, factor)

    def _entry_confirmed(self, vt_symbol: str, bars: list[BarData]) -> bool:
        if self.config.confirm_bars <= 1:
            return True
        signal_bars = self._signal_bars(bars)
        if len(signal_bars) < self._signal_warmup_window() + self.config.confirm_bars - 1:
            return False
        for offset in range(self.config.confirm_bars):
            end = len(signal_bars) - offset
            factor = self._evaluate_signal_bars(vt_symbol, signal_bars[:end])
            if not factor or factor.signal != "long_entry":
                return False
        return True

    def _signal_bars(self, bars: list[BarData]) -> list[BarData]:
        interval = max(int(self.config.signal_interval_minutes), 1)
        if interval <= 1:
            return bars
        result: list[BarData] = []
        chunk: list[BarData] = []
        for bar in bars:
            chunk.append(bar)
            if len(chunk) >= interval:
                result.append(self._aggregate_chunk(chunk))
                chunk = []
        return result

    def _aggregate_chunk(self, chunk: list[BarData]) -> BarData:
        first = chunk[0]
        last = chunk[-1]
        return BarData(
            symbol=last.symbol,
            exchange=last.exchange,
            datetime=last.datetime,
            interval=last.interval,
            volume=sum(float(bar.volume or 0) for bar in chunk),
            turnover=sum(float(bar.turnover or 0) for bar in chunk),
            open_interest=last.open_interest,
            open_price=first.open_price,
            high_price=max(float(bar.high_price or 0) for bar in chunk),
            low_price=min(float(bar.low_price or 0) for bar in chunk),
            close_price=last.close_price,
            gateway_name=last.gateway_name,
        )

    def _signal_warmup_window(self) -> int:
        return max(self.config.fast_window, self.config.slow_window, self.config.momentum_window, self.config.atr_window, 20) + 2

    def _array_manager(self, bars: list[BarData]) -> ArrayManager:
        size = max(self._signal_warmup_window(), len(bars[-self._signal_warmup_window():]))
        am = ArrayManager(size=size)
        for bar in bars[-size:]:
            am.update_bar(bar)
        return am

    def _windows(self) -> dict[str, int]:

        return {
            "fast_window": self.config.fast_window,
            "slow_window": self.config.slow_window,
            "momentum_window": self.config.momentum_window,
            "atr_window": self.config.atr_window,
            "min_trend_score": self.config.min_trend_score,
            "signal_interval_minutes": self.config.signal_interval_minutes,

            "confirm_bars": self.config.confirm_bars,
        }

