from __future__ import annotations

from vnpy.trader.object import BarData, OrderData, TickData, TradeData
from vnpy_ctastrategy import CtaTemplate, StopOrder

from scripts.classic_multifactor.minute_guard import MinuteTradeGuard, MinuteTradeGuardConfig
from scripts.classic_multifactor.model import ClassicMultiFactorConfig, ClassicMultiFactorModel



class ClassicMultiFactorCtaStrategy(CtaTemplate):
    """vn.py CTA template adapter for the no-LLM classic multi-factor model."""

    author = "CodeBuddy"

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
    capital: float = 20000.0
    price_add: float = 0.001
    fixed_size: int = 1
    signal_interval_minutes: int = 1
    confirm_bars: int = 1
    min_volume_ratio: float = 0.0
    min_atr_pct: float = 0.0
    min_trend_score: float = 0.60

    stop_atr: float = 0.0
    take_profit_atr: float = 0.0
    trailing_atr: float = 0.0
    max_intraday_trades: int = 0
    entry_cooldown_minutes: int = 0
    min_hold_minutes: int = 0
    no_new_entry_after: str = ""

    raw_score: float = 0.0

    last_signal: str = ""
    entry_price: float = 0.0
    highest_close: float = 0.0

    parameters = [
        "fast_window",
        "slow_window",
        "momentum_window",
        "atr_window",
        "entry_score",
        "exit_score",
        "stop_loss_pct",
        "take_profit_pct",
        "trailing_stop_pct",
        "max_position_pct",
        "max_order_value",
        "capital",
        "price_add",
        "fixed_size",
        "signal_interval_minutes",
        "confirm_bars",
        "min_volume_ratio",
        "min_atr_pct",
        "min_trend_score",

        "stop_atr",
        "take_profit_atr",
        "trailing_atr",
        "max_intraday_trades",
        "entry_cooldown_minutes",
        "min_hold_minutes",
        "no_new_entry_after",
    ]
    variables = ["raw_score", "last_signal", "entry_price", "highest_close"]


    def __init__(self, cta_engine, strategy_name: str, vt_symbol: str, setting: dict) -> None:
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        self.bars: list[BarData] = []
        self.active_orderids: set[str] = set()
        self.trade_times = []
        self.last_trade_at = None
        self.entry_at = None
        self.model = self._build_model()
        self.minute_guard = self._build_minute_guard()


    def on_init(self) -> None:
        self.model = self._build_model()
        self.minute_guard = self._build_minute_guard()
        self.load_bar(self.model.config.warmup_window)


    def on_start(self) -> None:
        pass

    def on_stop(self) -> None:
        pass

    def on_tick(self, tick: TickData) -> None:
        pass

    def on_bar(self, bar: BarData) -> None:
        self.bars.append(bar)
        max_len = self.model.config.warmup_window + 10
        if len(self.bars) > max_len:
            self.bars = self.bars[-max_len:]
        if self.active_orderids:
            self.last_signal = "waiting_order"
            return
        highest = max(self.highest_close, bar.close_price) if self.pos > 0 else 0.0
        decision = self.model.decide_target(
            vt_symbol=bar.vt_symbol,
            bars=self.bars,
            current_qty=int(self.pos),
            entry_price=self.entry_price,
            highest_close=highest,
            equity=float(self.capital),
            cash=float(self.capital),
            trade_price=bar.close_price,
        )
        self.last_signal = decision.reason
        if decision.factor:
            self.raw_score = decision.factor.raw_score
        if self.pos > 0:
            self.highest_close = highest
        if decision.side == "BUY" and self.pos <= 0 and decision.target_qty > 0:
            guard = self.minute_guard.can_enter(bar.datetime, trade_times=self.trade_times, last_trade_at=self.last_trade_at)
            if not guard.allowed:
                self.last_signal = guard.reason
                return
            qty = max(int(decision.target_qty), int(self.fixed_size))
            vt_orderids = self.buy(bar.close_price * (1 + self.price_add), qty)
            self.active_orderids.update(vt_orderids)
        elif decision.side == "SELL" and self.pos > 0:
            hard_exit = decision.reason in {"stop_loss", "atr_stop_loss", "trailing_stop", "atr_trailing_stop", "take_profit", "atr_take_profit"}
            guard = self.minute_guard.can_exit(bar.datetime, entry_at=self.entry_at, hard_exit=hard_exit)
            if not guard.allowed:
                self.last_signal = guard.reason
                return
            vt_orderids = self.sell(bar.close_price * (1 - self.price_add), abs(self.pos))
            self.active_orderids.update(vt_orderids)


    def on_trade(self, trade: TradeData) -> None:
        if trade.volume <= 0:
            return
        if trade.datetime:
            self.trade_times.append(trade.datetime)
            self.last_trade_at = trade.datetime
        if trade.direction and trade.direction.value in {"多", "LONG"}:
            self.entry_price = trade.price
            self.highest_close = max(self.highest_close, trade.price)
            self.entry_at = trade.datetime
        elif self.pos <= 0:
            self.entry_price = 0.0
            self.highest_close = 0.0
            self.entry_at = None


    def on_order(self, order: OrderData) -> None:
        if order.is_active():
            self.active_orderids.add(order.vt_orderid)
        else:
            self.active_orderids.discard(order.vt_orderid)

    def on_stop_order(self, stop_order: StopOrder) -> None:
        pass

    def _build_model(self) -> ClassicMultiFactorModel:
        return ClassicMultiFactorModel(
            ClassicMultiFactorConfig(
                fast_window=int(self.fast_window),
                slow_window=int(self.slow_window),
                momentum_window=int(self.momentum_window),
                atr_window=int(self.atr_window),
                entry_score=float(self.entry_score),
                exit_score=float(self.exit_score),
                stop_loss_pct=float(self.stop_loss_pct),
                take_profit_pct=float(self.take_profit_pct),
                trailing_stop_pct=float(self.trailing_stop_pct),
                max_position_pct=float(self.max_position_pct),
                max_order_value=float(self.max_order_value),
                signal_interval_minutes=int(self.signal_interval_minutes),
                confirm_bars=int(self.confirm_bars),
                min_volume_ratio=float(self.min_volume_ratio),
                min_atr_pct=float(self.min_atr_pct),
                min_trend_score=float(self.min_trend_score),

                stop_atr=float(self.stop_atr),
                take_profit_atr=float(self.take_profit_atr),
                trailing_atr=float(self.trailing_atr),
            )
        )

    def _build_minute_guard(self) -> MinuteTradeGuard:
        return MinuteTradeGuard(
            MinuteTradeGuardConfig(
                max_intraday_trades=int(self.max_intraday_trades),
                entry_cooldown_minutes=int(self.entry_cooldown_minutes),
                min_hold_minutes=int(self.min_hold_minutes),
                no_new_entry_after=str(self.no_new_entry_after),
            )
        )

