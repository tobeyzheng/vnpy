from __future__ import annotations

import math
from datetime import datetime, time
from zoneinfo import ZoneInfo

from vnpy.trader.constant import Direction, Interval

from vnpy.trader.object import BarData, OrderData, TickData, TradeData
from vnpy.trader.utility import round_to
from vnpy_ctastrategy import CtaTemplate, StopOrder

NY_TZ = ZoneInfo("America/New_York")


class SoxlCtaStrategy(CtaTemplate):
    """SOXL long-only CTA strategy for Futu simulated trading."""

    author = "CodeBuddy"

    fast_window: int = 10
    slow_window: int = 120
    atr_window: int = 10
    risk_pct: float = 0.03
    max_pos_pct: float = 0.5
    stop_atr: float = 1.5
    trail_atr: float = 2.5
    max_drawdown_pct: float = 0.15
    capital: float = 10_000
    init_days: int = 360
    min_volume: int = 1
    price_add: float = 0.001
    rebalance_seconds: int = 1800
    trade_in_session_only: bool = True

    fast_ma: float = 0
    slow_ma: float = 0
    atr_value: float = 0
    entry_price: float = 0
    intra_trade_high: float = 0
    target_pos: int = 0
    peak_equity: float = 10_000
    drawdown_pct: float = 0
    trading_stopped: bool = False
    active_order_count: int = 0
    last_signal: str = ""

    parameters = [
        "fast_window",
        "slow_window",
        "atr_window",
        "risk_pct",
        "max_pos_pct",
        "stop_atr",
        "trail_atr",
        "max_drawdown_pct",
        "capital",
        "init_days",
        "min_volume",
        "price_add",
        "rebalance_seconds",
        "trade_in_session_only",
    ]

    variables = [
        "fast_ma",
        "slow_ma",
        "atr_value",
        "entry_price",
        "intra_trade_high",
        "target_pos",
        "peak_equity",
        "drawdown_pct",
        "trading_stopped",
        "active_order_count",
        "last_signal",
    ]

    def __init__(self, cta_engine, strategy_name: str, vt_symbol: str, setting: dict) -> None:
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        self.bars: list[BarData] = []
        self.active_orderids: set[str] = set()
        self.last_rebalance_at: datetime | None = None

    def on_init(self) -> None:
        self.write_log("SOXL CTA策略初始化")
        self.load_bar(self.init_days, interval=Interval.DAILY, use_database=False)
        self.peak_equity = max(self.peak_equity, self.capital)

    def on_start(self) -> None:
        self.write_log("SOXL CTA策略启动")

    def on_stop(self) -> None:
        self.write_log("SOXL CTA策略停止")

    def on_tick(self, tick: TickData) -> None:
        if not self.trading:
            return
        if self.trade_in_session_only and not self.is_us_market_time():
            return
        if self.last_rebalance_at:
            delta = datetime.now(NY_TZ) - self.last_rebalance_at
            if delta.total_seconds() < self.rebalance_seconds:
                return

        price = tick.last_price or tick.ask_price_1 or tick.bid_price_1
        if not price:
            return

        bar = BarData(
            symbol=tick.symbol,
            exchange=tick.exchange,
            datetime=tick.datetime,
            interval=Interval.DAILY,
            open_price=price,
            high_price=max(price, tick.high_price or price),
            low_price=min(price, tick.low_price or price),
            close_price=price,
            gateway_name=tick.gateway_name,
        )
        self.evaluate(bar, live_tick=tick)
        self.last_rebalance_at = datetime.now(NY_TZ)

    def on_bar(self, bar: BarData) -> None:
        self.bars.append(bar)
        max_len = max(self.init_days + 10, self.slow_window + self.atr_window + 10)
        if len(self.bars) > max_len:
            self.bars = self.bars[-max_len:]

        if not self.inited:
            self.update_indicators(bar)
            return

        self.evaluate(bar)

    def evaluate(self, bar: BarData, live_tick: TickData | None = None) -> None:
        if len(self.bars) < max(self.slow_window, self.atr_window) + 2:
            self.last_signal = "warming_up"
            self.put_event()
            return

        if self.active_orderids:
            self.last_signal = "waiting_order"
            self.put_event()
            return

        self.cancel_all()
        self.update_indicators(bar)
        self.update_drawdown(bar.close_price)

        if self.trading_stopped:
            if self.pos > 0:
                self.sell_order(bar, abs(self.pos), "risk_stop")
            self.put_event()
            return

        if self.pos > 0:
            self.intra_trade_high = max(self.intra_trade_high, bar.high_price, bar.close_price)
            exit_reason = self.get_exit_reason(bar.close_price)
            if exit_reason:
                self.target_pos = 0
                self.sell_order(bar, abs(self.pos), exit_reason)
        else:
            self.target_pos = self.calculate_target_pos(bar.close_price)
            if self.target_pos > 0:
                self.buy_order(bar, self.target_pos, "trend_entry")
            else:
                self.last_signal = "flat"

        self.put_event()

    def update_indicators(self, current_bar: BarData) -> None:
        bars = [*self.bars]
        if bars and current_bar.datetime == bars[-1].datetime:
            bars[-1] = current_bar
        else:
            bars.append(current_bar)

        closes = [bar.close_price for bar in bars]
        index = len(bars) - 1
        self.fast_ma = self.sma(closes, self.fast_window, index)
        self.slow_ma = self.sma(closes, self.slow_window, index)
        self.atr_value = self.atr(bars, self.atr_window, index)

    def calculate_target_pos(self, price: float) -> int:
        if not self.fast_ma or not self.slow_ma or not self.atr_value:
            return 0
        if self.fast_ma <= self.slow_ma or price <= self.slow_ma:
            return 0

        risk_per_share = max(self.atr_value * self.stop_atr, price * 0.02)
        risk_volume = math.floor(self.capital * self.risk_pct / risk_per_share)
        value_volume = math.floor(self.capital * self.max_pos_pct / price)
        volume = max(0, min(risk_volume, value_volume))
        return self.round_volume(volume)

    def get_exit_reason(self, price: float) -> str:
        if self.entry_price and price <= self.entry_price - self.stop_atr * self.atr_value:
            return "fixed_stop"
        if self.intra_trade_high and price <= self.intra_trade_high - self.trail_atr * self.atr_value:
            return "trailing_stop"
        if self.fast_ma <= self.slow_ma:
            return "trend_exit"
        return ""

    def buy_order(self, bar: BarData, volume: int, reason: str) -> None:
        price = round_to(bar.close_price * (1 + self.price_add), 0.01)
        vt_orderids = self.buy(price, volume)
        self.active_orderids.update(vt_orderids)
        self.active_order_count = len(self.active_orderids)
        self.last_signal = reason

    def sell_order(self, bar: BarData, volume: int, reason: str) -> None:
        price = round_to(bar.close_price * (1 - self.price_add), 0.01)
        vt_orderids = self.sell(price, volume)
        self.active_orderids.update(vt_orderids)
        self.active_order_count = len(self.active_orderids)
        self.last_signal = reason

    def on_order(self, order: OrderData) -> None:
        if not order.is_active():
            self.active_orderids.discard(order.vt_orderid)
        else:
            self.active_orderids.add(order.vt_orderid)
        self.active_order_count = len(self.active_orderids)
        self.put_event()

    def on_trade(self, trade: TradeData) -> None:
        if trade.volume <= 0:
            return
        if trade.direction == Direction.LONG:
            self.entry_price = trade.price
            self.intra_trade_high = max(self.intra_trade_high, trade.price)
        elif self.pos <= 0:
            self.entry_price = 0
            self.intra_trade_high = 0
        self.put_event()


    def on_stop_order(self, stop_order: StopOrder) -> None:
        pass

    def update_drawdown(self, price: float) -> None:
        if self.pos > 0 and self.entry_price:
            equity = self.capital + self.pos * (price - self.entry_price)
        else:
            equity = self.capital

        self.peak_equity = max(self.peak_equity, equity)
        if self.peak_equity:
            self.drawdown_pct = max(0, (self.peak_equity - equity) / self.peak_equity)
        if self.drawdown_pct >= self.max_drawdown_pct:
            self.trading_stopped = True
            self.last_signal = "max_drawdown_stop"


    def round_volume(self, volume: int) -> int:
        if self.min_volume <= 1:
            return int(volume)
        return int(volume // self.min_volume * self.min_volume)

    @staticmethod
    def sma(values: list[float], window: int, index: int) -> float:
        if window <= 0 or index + 1 < window:
            return 0
        return sum(values[index - window + 1: index + 1]) / window

    @staticmethod
    def atr(bars: list[BarData], window: int, index: int) -> float:
        if window <= 0 or index < window:
            return 0

        trs: list[float] = []
        for i in range(index - window + 1, index + 1):
            bar = bars[i]
            prev_close = bars[i - 1].close_price
            trs.append(
                max(
                    bar.high_price - bar.low_price,
                    abs(bar.high_price - prev_close),
                    abs(bar.low_price - prev_close),
                )
            )
        return sum(trs) / len(trs)

    @staticmethod
    def is_us_market_time() -> bool:
        now = datetime.now(NY_TZ)
        if now.weekday() >= 5:
            return False
        return time(9, 35) <= now.time() <= time(15, 55)
