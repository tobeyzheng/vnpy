from __future__ import annotations

from typing import Any, Protocol

from vnpy.trader.constant import Interval
from vnpy.trader.object import BarData, OrderData, TickData, TradeData
from vnpy_ctastrategy import CtaTemplate, StopOrder

from scripts.classic_multifactor.minute_guard import MinuteTradeGuard, MinuteTradeGuardConfig
from scripts.classic_multifactor.model import ClassicMultiFactorConfig, ClassicMultiFactorModel
from services.strategy.market_rules import market_timezone, resolve_market_from_vt_symbol


class ExecutionHook(Protocol):
    """Pre-trade gate protocol plugged into the CTA strategy.

    ``approve_buy`` / ``approve_sell`` are called right before ``self.buy`` /
    ``self.sell``. Returning ``(False, reason)`` cancels the order; the
    strategy will set ``last_signal = f"hook_blocked:{reason}"`` and skip the
    bar. ``on_order_submitted`` is invoked after a successful ``buy/sell``
    with the resulting ``vt_orderids`` so the hook can register the order
    state for idempotency tracking.
    """

    def approve_buy(self, bar: BarData, qty: int, price: float) -> tuple[bool, str]: ...
    def approve_sell(self, bar: BarData, qty: int, price: float) -> tuple[bool, str]: ...
    def on_order_submitted(
        self,
        bar: BarData,
        side: str,
        qty: int,
        price: float,
        vt_orderids: list[str],
    ) -> None: ...


class ClassicMultiFactorCtaStrategy(CtaTemplate):
    """vn.py CTA template adapter for the no-LLM classic multi-factor model."""

    author = "CodeBuddy"

    # Optional external pre-trade gate plugged in by the live runner
    # (``scripts/classic_multifactor/run_intraday_loop.py`` or
    # ``run_daily_rebalance.py``). Defaults to ``None`` so backtest /
    # ``cta_backtest.py`` keep the original behaviour with zero edits.
    execution_hook: Any = None

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
    data_interval: str = "1m"
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

    # Market regime filter (daily-level trend + volatility gate).
    # regime_filter_mode in {"off", "trend", "vol", "both"}.
    # When data is insufficient (fewer than regime_trend_lookback completed
    # days in the on-memory bar buffer) the filter fails open (allows trade)
    # to avoid starving the strategy during warmup.
    regime_filter_mode: str = "off"
    regime_trend_lookback: int = 5
    regime_ema_span: int = 20
    regime_atr_pct_lo: float = 0.008
    regime_atr_pct_hi: float = 0.05
    regime_atr_days: int = 5

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
        "data_interval",
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
        "regime_filter_mode",
        "regime_trend_lookback",
        "regime_ema_span",
        "regime_atr_pct_lo",
        "regime_atr_pct_hi",
        "regime_atr_days",
    ]
    variables = ["raw_score", "last_signal", "entry_price", "highest_close"]


    def __init__(self, cta_engine, strategy_name: str, vt_symbol: str, setting: dict) -> None:
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        self.bars: list[BarData] = []
        self.active_orderids: set[str] = set()
        self.trade_times = []
        self.last_trade_at = None
        self.entry_at = None
        self._is_warmup = True
        # Daily aggregated OHLC buffer for regime filter.
        # Each entry: {"date": date, "open": float, "high": float,
        #              "low": float, "close": float, "prev_close": float|None}
        self.daily_buf: list[dict] = []
        self._cur_day: object | None = None
        self.exchange_tz = market_timezone(resolve_market_from_vt_symbol(vt_symbol))
        self.model = self._build_model()
        self.minute_guard = self._build_minute_guard()


    def on_init(self) -> None:
        self.model = self._build_model()
        self.minute_guard = self._build_minute_guard()
        self._is_warmup = True
        self.load_bar(
            self._warmup_load_days(),
            interval=self._warmup_load_interval(),
        )
        self._is_warmup = False


    def on_start(self) -> None:
        self._is_warmup = False

    def on_stop(self) -> None:
        pass

    def on_tick(self, tick: TickData) -> None:
        pass

    def on_bar(self, bar: BarData) -> None:
        self._update_daily_buf(bar)
        self.bars.append(bar)
        max_len = self.model.config.warmup_window + 10
        if len(self.bars) > max_len:
            self.bars = self.bars[-max_len:]
        if self._is_warmup:
            self.last_signal = "warmup"
            return
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
            if not self._market_regime_ok(bar):
                self.last_signal = "regime_blocked"
                return
            guard = self.minute_guard.can_enter(
                bar.datetime,
                trade_times=self.trade_times,
                last_trade_at=self.last_trade_at,
                exchange_tz=self.exchange_tz,
            )
            if not guard.allowed:
                self.last_signal = guard.reason
                return
            qty = max(int(decision.target_qty), int(self.fixed_size))
            price = bar.close_price * (1 + self.price_add)
            if self.execution_hook is not None:
                allowed, reason = self.execution_hook.approve_buy(bar, qty, price)
                if not allowed:
                    self.last_signal = f"hook_blocked:{reason}"
                    return
            vt_orderids = self.buy(price, qty)
            self.active_orderids.update(vt_orderids)
            if self.execution_hook is not None:
                try:
                    self.execution_hook.on_order_submitted(bar, "BUY", qty, price, list(vt_orderids))
                except Exception:
                    # Hook bookkeeping failure must never break the strategy loop.
                    pass
        elif decision.side == "SELL" and self.pos > 0:
            hard_exit = decision.reason in {"stop_loss", "atr_stop_loss", "trailing_stop", "atr_trailing_stop", "take_profit", "atr_take_profit"}
            guard = self.minute_guard.can_exit(bar.datetime, entry_at=self.entry_at, hard_exit=hard_exit)
            if not guard.allowed:
                self.last_signal = guard.reason
                return
            qty = abs(int(self.pos))
            price = bar.close_price * (1 - self.price_add)
            if self.execution_hook is not None:
                allowed, reason = self.execution_hook.approve_sell(bar, qty, price)
                if not allowed:
                    self.last_signal = f"hook_blocked:{reason}"
                    return
            vt_orderids = self.sell(price, qty)
            self.active_orderids.update(vt_orderids)
            if self.execution_hook is not None:
                try:
                    self.execution_hook.on_order_submitted(bar, "SELL", qty, price, list(vt_orderids))
                except Exception:
                    pass


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
            MinuteTradeGuardConfig.from_setting({
                "max_intraday_trades": self.max_intraday_trades,
                "entry_cooldown_minutes": self.entry_cooldown_minutes,
                "min_hold_minutes": self.min_hold_minutes,
                "no_new_entry_after": self.no_new_entry_after,
            })
        )

    @staticmethod
    def warmup_load_days(required_bars: int, data_interval: str) -> int:
        """Convert required warmup bars into load_bar(days=...) semantics.

        vn.py ``load_bar`` expects natural-day count, not number of bars.
        For minute strategies we therefore convert the required 1m warmup bars
        into a conservative day window with weekend/holiday slack instead of
        mistakenly requesting hundreds of natural days.
        """
        bars = max(int(required_bars or 0), 1)
        interval_text = str(data_interval or "1m").strip().lower()
        if interval_text == "1d":
            return bars
        minutes_per_trading_day = 240
        buffer_days = 2
        return max(1, (bars + minutes_per_trading_day - 1) // minutes_per_trading_day + buffer_days)

    @staticmethod
    def resolve_warmup_load_interval(data_interval: str) -> Interval:
        interval_text = str(data_interval or "1m").strip().lower()
        if interval_text == "1d":
            return Interval.DAILY
        return Interval.MINUTE

    def _warmup_load_days(self) -> int:
        return self.warmup_load_days(self.model.config.warmup_window, self.data_interval)

    def _warmup_load_interval(self) -> Interval:
        return self.resolve_warmup_load_interval(self.data_interval)

    # ------------------------------------------------------------------
    # Market regime filter helpers
    # ------------------------------------------------------------------
    def _update_daily_buf(self, bar: BarData) -> None:
        """Aggregate 1m bar into a rolling daily OHLC buffer.

        Only keeps ``max_keep_days`` entries to bound memory. The last entry
        is always the currently forming day (mutated in place until the day
        rolls over).
        """
        try:
            day = bar.datetime.date()
        except Exception:
            return

        max_keep_days = max(
            int(self.regime_trend_lookback),
            int(self.regime_ema_span),
            int(self.regime_atr_days),
            20,
        ) + 5

        if self._cur_day != day:
            prev_close = self.daily_buf[-1]["close"] if self.daily_buf else None
            self.daily_buf.append(
                {
                    "date": day,
                    "open": float(bar.open_price),
                    "high": float(bar.high_price),
                    "low": float(bar.low_price),
                    "close": float(bar.close_price),
                    "prev_close": prev_close,
                }
            )
            self._cur_day = day
            if len(self.daily_buf) > max_keep_days:
                self.daily_buf = self.daily_buf[-max_keep_days:]
        else:
            cur = self.daily_buf[-1]
            cur["high"] = max(cur["high"], float(bar.high_price))
            cur["low"] = min(cur["low"], float(bar.low_price))
            cur["close"] = float(bar.close_price)

    def _market_regime_ok(self, bar: BarData) -> bool:
        """Return True if current market regime satisfies the configured gates.

        Fails open (returns True) when the filter is off or there is not
        enough daily history yet.
        """
        mode = (self.regime_filter_mode or "off").strip().lower()
        if mode == "off":
            return True

        lookback = max(int(self.regime_trend_lookback), 1)
        ema_span = max(int(self.regime_ema_span), 2)
        atr_days = max(int(self.regime_atr_days), 1)
        # Need at least lookback+1 completed days + 1 forming day for a
        # meaningful EMA / ATR. Fail open if not enough.
        need_days = max(lookback, ema_span, atr_days) + 1
        if len(self.daily_buf) < need_days:
            return True

        closes = [d["close"] for d in self.daily_buf]
        # EMA on closes (including today's forming close)
        alpha = 2.0 / (ema_span + 1)
        ema = closes[0]
        for c in closes[1:]:
            ema = alpha * c + (1 - alpha) * ema

        last_close = closes[-1]
        trend_ok = last_close > ema

        # Daily ATR/close ratio over the last ``atr_days`` completed days
        # (exclude the forming current day for stability).
        tr_list: list[float] = []
        for d in self.daily_buf[-(atr_days + 1):-1]:
            pc = d["prev_close"]
            hi = d["high"]
            lo = d["low"]
            if pc is None:
                tr = hi - lo
            else:
                tr = max(hi - lo, abs(hi - pc), abs(lo - pc))
            tr_list.append(tr)
        if not tr_list:
            return True
        atr_val = sum(tr_list) / len(tr_list)
        ref_close = self.daily_buf[-2]["close"]
        if ref_close <= 0:
            return True
        atr_pct = atr_val / ref_close
        vol_ok = (
            float(self.regime_atr_pct_lo) <= atr_pct <= float(self.regime_atr_pct_hi)
        )

        if mode == "trend":
            return trend_ok
        if mode == "vol":
            return vol_ok
        if mode == "both":
            return trend_ok and vol_ok
        # Unknown mode -> fail open
        return True

