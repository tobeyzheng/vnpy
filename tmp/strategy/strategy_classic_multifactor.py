# -*- coding: utf-8 -*-
# Classic multi-factor strategy ported to Futu Quant (StrategyBase) runtime.
#
# WHY THIS FILE EXISTS
# --------------------
# 1:1 logical port of `scripts/classic_multifactor/run_intraday_loop.py`
# (vn.py + FutuGateway) into Futu's cloud "pure-code" strategy container,
# documented in `tmp/futu_quant.md`.
#
# FUTU CODE-STRATEGY HARD CONSTRAINTS HONOURED
# --------------------------------------------
# - The whole file may contain ONLY:
#     * `class Strategy(StrategyBase):` declaration
#     * the 5 conventional methods (initialize / trigger_symbols /
#       global_variables / custom_indicator / handle_data)
#     * code INSIDE those 5 methods
#   No module-level `def`, no module-level `if`, no module-level statements.
#   ALL helper functions therefore live as nested locals inside
#   `initialize()` and are stored on `self.` so `handle_data()` can call
#   them via `self._xxx(...)`.
# - GlobalType only supports FLOAT / INT / BOOL (no STRING).
# - `max(a, b, *args)` requires >=2 explicit args; `round(value)` accepts 1.
# - Time API: use `device_time(TimeZone.DEVICE_TIME_ZONE)`; do NOT call any
#   standard-library time/date constructor.
# - Reserved built-in names to AVOID as local variables:
#     cash, volume_ratio, current_price, etc.


class Strategy(StrategyBase):

    def initialize(self):
        declare_strategy_type(AlgoStrategyType.SECURITY)
        self.trigger_symbols()
        self.custom_indicator()
        self.global_variables()

        # ------------------------------------------------------------------
        # Runtime state.
        # ------------------------------------------------------------------
        self.entry_price       = 0.0
        self.highest_close     = 0.0
        self.entry_minute      = None
        self.last_trade_minute = None
        self.intraday_trades   = []
        self.current_day       = 0
        self.last_signal       = ""
        self.last_raw_score    = 0.0

        # ------------------------------------------------------------------
        # Helper functions.  They are defined as nested locals inside
        # `initialize()` (the only doc-allowed place for user code besides
        # the other 4 conventional methods) and bound onto `self.` so that
        # `handle_data()` can call them as plain methods.
        # ------------------------------------------------------------------

        def _clamp01(value):
            try:
                v = float(value)
            except Exception:
                return 0.0
            if v != v:  # NaN guard
                return 0.0
            if v < 0.0:
                return 0.0
            if v > 1.0:
                return 1.0
            return v
        self._clamp01 = _clamp01

        def _sma(values, window):
            if window <= 0 or len(values) < window:
                return 0.0
            total = 0.0
            for x in values[-window:]:
                total = total + float(x)
            return total / float(window)
        self._sma = _sma

        def _roc(values, lookback):
            if lookback <= 0 or len(values) <= lookback:
                return 0.0
            prev = float(values[-1 - lookback])
            if prev == 0.0:
                return 0.0
            return float(values[-1]) / prev - 1.0
        self._roc = _roc

        def _rsi(values, window):
            if window <= 0 or len(values) <= window:
                return 50.0
            gains = 0.0
            losses = 0.0
            i = -window
            while i < 0:
                diff = float(values[i]) - float(values[i - 1])
                if diff >= 0.0:
                    gains = gains + diff
                else:
                    losses = losses + (-diff)
                i = i + 1
            if losses == 0.0:
                return 100.0
            rs = (gains / float(window)) / (losses / float(window))
            return 100.0 - 100.0 / (1.0 + rs)
        self._rsi = _rsi

        def _atr(highs, lows, closes, window):
            if window <= 0 or len(highs) <= window:
                return 0.0
            total = 0.0
            count = 0
            i = -window
            while i < 0:
                h = float(highs[i])
                l = float(lows[i])
                if i - 1 >= -len(closes):
                    pc = float(closes[i - 1])
                else:
                    pc = float(closes[i])
                tr = h - l
                cand = pc - h
                if cand < 0.0:
                    cand = -cand
                if cand > tr:
                    tr = cand
                cand = pc - l
                if cand < 0.0:
                    cand = -cand
                if cand > tr:
                    tr = cand
                total = total + tr
                count = count + 1
                i = i + 1
            if count == 0:
                return 0.0
            return total / float(count)
        self._atr = _atr

        def _max_in_list(values):
            # Fold-style maximum across an iterable; the platform builtin
            # requires >=2 explicit positional args and refuses iterables.
            if not values:
                return 0.0
            best = float(values[0])
            j = 1
            while j < len(values):
                v = float(values[j])
                if v > best:
                    best = v
                j = j + 1
            return best
        self._max_in_list = _max_in_list

        def _min_in_list(values):
            if not values:
                return 0.0
            best = float(values[0])
            j = 1
            while j < len(values):
                v = float(values[j])
                if v < best:
                    best = v
                j = j + 1
            return best
        self._min_in_list = _min_in_list

        def _round4(value):
            # Round to 4 decimals; the platform `round(value)` accepts only
            # one argument so we cannot pass `ndigits`.
            return float(int(float(value) * 10000.0 + 0.5)) / 10000.0
        self._round4 = _round4

        def _device_minute_key():
            dt = device_time(TimeZone.DEVICE_TIME_ZONE)
            return (dt.year * 100000000 + dt.month * 1000000
                    + dt.day * 10000 + dt.hour * 100 + dt.minute)
        self._device_minute_key = _device_minute_key

        def _device_day_key():
            dt = device_time(TimeZone.DEVICE_TIME_ZONE)
            return dt.year * 10000 + dt.month * 100 + dt.day
        self._device_day_key = _device_day_key

        def _device_hhmm():
            dt = device_time(TimeZone.DEVICE_TIME_ZONE)
            return dt.hour * 60 + dt.minute
        self._device_hhmm = _device_hhmm

        def _minutes_distance(key_a, key_b):
            a_day = key_a // 10000
            b_day = key_b // 10000
            a_min = (key_a % 10000) // 100 * 60 + (key_a % 100)
            b_min = (key_b % 10000) // 100 * 60 + (key_b % 100)
            if a_day == b_day:
                d = b_min - a_min
                if d < 0:
                    d = -d
                return d
            # Different day -- treat as "very far" so cooldown/hold expires.
            return 24 * 60
        self._minutes_distance = _minutes_distance

        def _fetch_bar_series(symbol, kind, n):
            # Pull the most recent ``n`` 1-minute CLOSED bars (oldest -> newest).
            # `bar_*(select=k)` k accepts 1..500.  In Futu backtest mode
            # `handle_data` fires on bar close, so `select=1` already refers
            # to the latest CLOSED bar -- no need to skip it like vnpy on_bar.
            out = []
            k = n
            while k >= 1:
                v = 0.0
                if kind == "open":
                    v = bar_open(symbol=symbol,  bar_type=BarType.K_1M,
                                 select=k, session_type=THType.ALL)
                elif kind == "close":
                    v = bar_close(symbol=symbol, bar_type=BarType.K_1M,
                                  select=k, session_type=THType.ALL)
                elif kind == "high":
                    v = bar_high(symbol=symbol,  bar_type=BarType.K_1M,
                                 select=k, session_type=THType.ALL)
                elif kind == "low":
                    v = bar_low(symbol=symbol,   bar_type=BarType.K_1M,
                                select=k, session_type=THType.ALL)
                elif kind == "volume":
                    v = bar_volume(symbol=symbol, bar_type=BarType.K_1M,
                                   select=k, session_type=THType.ALL)
                if v is None:
                    v = 0.0
                out.append(float(v))
                k = k - 1
            return out
        self._fetch_bar_series = _fetch_bar_series

        def _compute_factor_snapshot(symbol, params, shift):
            fast_w = int(params["fast_window"])
            slow_w = int(params["slow_window"])
            mom_w  = int(params["momentum_window"])
            atr_w  = int(params["atr_window"])

            warmup = max(fast_w, slow_w)
            warmup = max(warmup, mom_w)
            warmup = max(warmup, atr_w)
            warmup = max(warmup, 20)
            warmup = warmup + 1

            closes = self._fetch_bar_series(symbol, "close",  warmup + shift)
            highs  = self._fetch_bar_series(symbol, "high",   warmup + shift)
            lows   = self._fetch_bar_series(symbol, "low",    warmup + shift)
            vols   = self._fetch_bar_series(symbol, "volume", warmup + shift)

            if len(closes) < warmup + shift or len(highs) < warmup + shift:
                return None
            if len(lows) < warmup + shift or len(vols) < warmup + shift:
                return None

            if shift > 0:
                closes = closes[:-shift]
                highs  = highs[:-shift]
                lows   = lows[:-shift]
                vols   = vols[:-shift]

            close = float(closes[-1])
            if close <= 0.0:
                return None

            fast_ma   = self._sma(closes, fast_w)
            slow_ma   = self._sma(closes, slow_w)
            return_5d = self._roc(closes, 5)
            return_20d = self._roc(closes, mom_w)
            rsi_value = self._rsi(closes, 14)
            atr_value = self._atr(highs, lows, closes, atr_w)
            if close > 0.0:
                atr_ratio = atr_value / close
            else:
                atr_ratio = 0.0

            recent_vol = []
            for v in vols[-20:]:
                fv = float(v)
                if fv > 0.0:
                    recent_vol.append(fv)
            if recent_vol:
                avg_vol = 0.0
                for fv in recent_vol:
                    avg_vol = avg_vol + fv
                avg_vol = avg_vol / float(len(recent_vol))
            else:
                avg_vol = 0.0
            last_vol = float(vols[-1])
            if avg_vol > 0.0:
                vol_ratio = last_vol / avg_vol
            else:
                vol_ratio = 1.0

            if len(highs) >= 20:
                high20 = self._max_in_list(highs[-20:])
            else:
                high20 = 0.0

            # ----- score components -----
            trend_score = 0.0
            if fast_ma > slow_ma:
                trend_score = trend_score + 0.35
            if close > fast_ma:
                trend_score = trend_score + 0.25
            if close > slow_ma:
                trend_score = trend_score + 0.25
            if high20 > 0.0 and close >= high20 * 0.98:
                trend_score = trend_score + 0.15

            momentum_score = self._clamp01(0.5 + return_20d * 2.5 + return_5d)
            volume_score   = self._clamp01(vol_ratio / 2.0)
            low_risk_score = self._clamp01(1.0 - atr_ratio * 8.0)

            if high20 > 0.0 and close >= high20 * 0.995:
                breakout_score = 1.0
            elif high20 > 0.0 and close >= high20 * 0.97:
                breakout_score = 0.5
            else:
                breakout_score = 0.0

            raw_score = self._round4(
                0.35 * trend_score
                + 0.25 * momentum_score
                + 0.15 * volume_score
                + 0.15 * low_risk_score
                + 0.10 * breakout_score
            )

            blocked = []
            if vol_ratio < float(params["min_volume_ratio"]):
                blocked.append("volume_ratio_low")
            if atr_ratio < float(params["min_atr_pct"]):
                blocked.append("atr_pct_too_low")

            if (raw_score >= float(params["entry_score"])
                    and trend_score >= float(params["min_trend_score"])
                    and return_20d > 0.0
                    and not blocked):
                signal = "long_entry"
                reason = "classic_multifactor_entry"
            elif (raw_score <= float(params["exit_score"])
                  or (slow_ma > 0.0 and close < slow_ma)):
                signal = "exit_or_flat"
                reason = "classic_multifactor_exit"
            elif blocked:
                signal = "hold"
                reason = "filtered:" + ",".join(blocked)
            else:
                signal = "hold"
                reason = "classic_multifactor_hold"

            return {
                "close":          close,
                "fast_ma":        fast_ma,
                "slow_ma":        slow_ma,
                "return_5d":      return_5d,
                "return_20d":     return_20d,
                "rsi":            rsi_value,
                "vol_ratio":      vol_ratio,
                "atr_value":      atr_value,
                "atr_pct":        atr_ratio,
                "trend_score":    trend_score,
                "momentum_score": momentum_score,
                "volume_score":   volume_score,
                "low_risk_score": low_risk_score,
                "breakout_score": breakout_score,
                "raw_score":      raw_score,
                "signal":         signal,
                "reason":         reason,
                "blocked":        blocked,
                "high20":         high20,
            }
        self._compute_factor_snapshot = _compute_factor_snapshot

        def _entry_confirmed(symbol, params, confirm_bars):
            n = confirm_bars
            if n < 1:
                n = 1
            if n <= 1:
                return True
            j = 1
            while j < n:
                f = self._compute_factor_snapshot(symbol, params, j)
                if f is None or f["signal"] != "long_entry":
                    return False
                j = j + 1
            return True
        self._entry_confirmed = _entry_confirmed

        def _exit_reason(factor, params):
            close = float(factor["close"])
            atr_v = float(factor["atr_value"])
            if atr_v < 0.0:
                atr_v = 0.0

            entry_price   = float(self.entry_price)
            highest_close = float(self.highest_close)
            if entry_price > 0.0:
                pnl_pct = close / entry_price - 1.0
            else:
                pnl_pct = 0.0
            if highest_close > 0.0:
                trail_pct = close / highest_close - 1.0
            else:
                trail_pct = 0.0

            stop_atr_mul        = float(params["stop_atr"])
            take_profit_atr_mul = float(params["take_profit_atr"])
            trailing_atr_mul    = float(params["trailing_atr"])

            atr_stop = 0.0
            if stop_atr_mul > 0.0:
                atr_stop = atr_v * stop_atr_mul
            atr_tp = 0.0
            if take_profit_atr_mul > 0.0:
                atr_tp = atr_v * take_profit_atr_mul
            atr_trail = 0.0
            if trailing_atr_mul > 0.0:
                atr_trail = atr_v * trailing_atr_mul

            if atr_stop > 0.0 and close <= entry_price - atr_stop:
                return "atr_stop_loss"
            if atr_tp > 0.0 and close >= entry_price + atr_tp:
                return "atr_take_profit"
            if (atr_trail > 0.0 and highest_close > 0.0
                    and close <= highest_close - atr_trail):
                return "atr_trailing_stop"
            if pnl_pct <= -float(params["stop_loss_pct"]):
                return "stop_loss"
            if pnl_pct >= float(params["take_profit_pct"]):
                return "take_profit"
            if trail_pct <= -float(params["trailing_stop_pct"]):
                return "trailing_stop"
            return None
        self._exit_reason = _exit_reason

        def _is_hard_exit(reason):
            if reason == "stop_loss":
                return True
            if reason == "atr_stop_loss":
                return True
            if reason == "trailing_stop":
                return True
            if reason == "atr_trailing_stop":
                return True
            if reason == "take_profit":
                return True
            if reason == "atr_take_profit":
                return True
            return False
        self._is_hard_exit = _is_hard_exit

        def _check_minute_guard_entry(params):
            cap = int(params["max_intraday_trades"])
            if cap > 0 and len(self.intraday_trades) >= cap:
                return "max_intraday_trades_reached"
            cutoff_hour = int(params["cutoff_hour"])
            cutoff_min  = int(params["cutoff_minute"])
            if cutoff_hour >= 0:
                now_min = self._device_hhmm()
                cut_min = cutoff_hour * 60 + cutoff_min
                if now_min >= cut_min:
                    return "no_new_entry_after_cutoff"
            cooldown = int(params["entry_cooldown_minutes"])
            if cooldown > 0 and self.last_trade_minute is not None:
                last = self.last_trade_minute
                delta = self._minutes_distance(last,
                                               self._device_minute_key())
                if delta < cooldown:
                    return "entry_cooldown"
            return None
        self._check_minute_guard_entry = _check_minute_guard_entry

        def _check_minute_guard_exit(params, hard_exit):
            if hard_exit:
                return None
            hold = int(params["min_hold_minutes"])
            if hold > 0 and self.entry_minute is not None:
                delta = self._minutes_distance(self.entry_minute,
                                               self._device_minute_key())
                if delta < hold:
                    return "min_hold_minutes_not_met"
            return None
        self._check_minute_guard_exit = _check_minute_guard_exit

        def _record_trade(side, price):
            self.last_trade_minute = self._device_minute_key()
            self.intraday_trades.append(self.last_trade_minute)
            if side == "BUY":
                self.entry_price   = float(price)
                self.highest_close = float(price)
                self.entry_minute  = self.last_trade_minute
            else:
                self.entry_price   = 0.0
                self.highest_close = 0.0
                self.entry_minute  = None
        self._record_trade = _record_trade

        def _maybe_update_trailing_peak(factor):
            close = float(factor["close"])
            if close > float(self.highest_close):
                self.highest_close = close
        self._maybe_update_trailing_peak = _maybe_update_trailing_peak

        def _params_dict():
            return {
                "fast_window":            self.fast_window,
                "slow_window":            self.slow_window,
                "momentum_window":        self.momentum_window,
                "atr_window":             self.atr_window,
                "entry_score":            self.entry_score,
                "exit_score":             self.exit_score,
                "min_trend_score":        self.min_trend_score,
                "min_volume_ratio":       self.min_volume_ratio,
                "min_atr_pct":            self.min_atr_pct,
                "confirm_bars":           self.confirm_bars,
                "stop_loss_pct":          self.stop_loss_pct,
                "take_profit_pct":        self.take_profit_pct,
                "trailing_stop_pct":      self.trailing_stop_pct,
                "stop_atr":               self.stop_atr,
                "take_profit_atr":        self.take_profit_atr,
                "trailing_atr":           self.trailing_atr,
                "capital":                self.capital,
                "max_position_pct":       self.max_position_pct,
                "max_order_value":        self.max_order_value,
                "price_add":              self.price_add,
                "fixed_size":             self.fixed_size,
                "max_intraday_trades":    self.max_intraday_trades,
                "entry_cooldown_minutes": self.entry_cooldown_minutes,
                "min_hold_minutes":       self.min_hold_minutes,
                "cutoff_hour":            self.cutoff_hour,
                "cutoff_minute":          self.cutoff_minute,
            }
        self._params_dict = _params_dict

    def trigger_symbols(self):
        self.target = declare_trig_symbol()

    def custom_indicator(self):
        # Indicators are computed inline from bar_* helpers; no MyLang/Python
        # indicator registration is needed.
        pass

    def global_variables(self):
        # ---- factor windows ----
        self.fast_window      = show_variable(10, GlobalType.INT)
        self.slow_window      = show_variable(60, GlobalType.INT)
        self.momentum_window  = show_variable(20, GlobalType.INT)
        self.atr_window       = show_variable(14, GlobalType.INT)

        # ---- score thresholds ----
        self.entry_score      = show_variable(0.62, GlobalType.FLOAT)
        self.exit_score       = show_variable(0.46, GlobalType.FLOAT)
        self.min_trend_score  = show_variable(0.60, GlobalType.FLOAT)
        self.min_volume_ratio = show_variable(0.0,  GlobalType.FLOAT)
        self.min_atr_pct      = show_variable(0.0,  GlobalType.FLOAT)
        self.confirm_bars     = show_variable(1,    GlobalType.INT)

        # ---- pct stops ----
        self.stop_loss_pct     = show_variable(0.08, GlobalType.FLOAT)
        self.take_profit_pct   = show_variable(0.22, GlobalType.FLOAT)
        self.trailing_stop_pct = show_variable(0.12, GlobalType.FLOAT)

        # ---- ATR stops (0 disables) ----
        self.stop_atr        = show_variable(0.0, GlobalType.FLOAT)
        self.take_profit_atr = show_variable(0.0, GlobalType.FLOAT)
        self.trailing_atr    = show_variable(0.0, GlobalType.FLOAT)

        # ---- sizing ----
        self.capital          = show_variable(20000.0, GlobalType.FLOAT)
        self.max_position_pct = show_variable(0.35,    GlobalType.FLOAT)
        self.max_order_value  = show_variable(5000.0,  GlobalType.FLOAT)
        self.price_add        = show_variable(0.001,   GlobalType.FLOAT)
        self.fixed_size       = show_variable(1,       GlobalType.INT)

        # ---- minute guard ----
        self.max_intraday_trades    = show_variable(0, GlobalType.INT)
        self.entry_cooldown_minutes = show_variable(0, GlobalType.INT)
        self.min_hold_minutes       = show_variable(0, GlobalType.INT)
        # cutoff_hour < 0 disables the cutoff.
        self.cutoff_hour            = show_variable(-1, GlobalType.INT)
        self.cutoff_minute          = show_variable(0,  GlobalType.INT)

        # ---- safety toggle: when False only `alert(...)` is sent ----
        # Default True so backtest / SIM actually submit orders; flip to
        # False on the platform UI for REAL dry-run protection.
        self.LIVE_SUBMIT            = show_variable(True, GlobalType.BOOL)

    def handle_data(self):
        symbol = self.target
        params = self._params_dict()

        # 1) Daily reset.
        today = self._device_day_key()
        if self.current_day != today:
            self.current_day     = today
            self.intraday_trades = []

        # 2) Compute factor snapshot for the latest closed bar.
        factor = self._compute_factor_snapshot(symbol, params, 0)
        if factor is None:
            self.last_signal = "warming_up"
            return
        self.last_raw_score = factor["raw_score"]

        # 3) Position branch.
        held = position_holding_qty(symbol=symbol)
        if held is None:
            held = 0.0
        held_qty = int(held)

        # ----- HOLDING LONG -----
        if held_qty > 0:
            self._maybe_update_trailing_peak(factor)

            reason = self._exit_reason(factor, params)
            do_exit = False
            exit_label = ""
            if reason is not None:
                do_exit = True
                exit_label = reason
            elif factor["signal"] == "exit_or_flat":
                do_exit = True
                exit_label = factor["reason"]

            if not do_exit:
                self.last_signal = factor["reason"]
                return

            hard = self._is_hard_exit(exit_label)
            blocked = self._check_minute_guard_exit(params, hard)
            if blocked is not None:
                self.last_signal = blocked
                alert(title="exit blocked", content=blocked)
                return

            ref_price = float(factor["close"])
            if ref_price <= 0.0:
                return
            sell_price = ref_price * (1.0 - float(params["price_add"]))
            sell_qty   = held_qty

            msg = ("SELL " + str(symbol) + " qty=" + str(sell_qty)
                   + " price=" + str(sell_price)
                   + " reason=" + str(exit_label))
            if self.LIVE_SUBMIT:
                close_positions(symbol=symbol, qty=sell_qty)
                self._record_trade("SELL", ref_price)
                alert(title="live SELL submitted", content=msg)
            else:
                alert(title="dry_run SELL", content=msg)
            self.last_signal = exit_label
            return

        # ----- FLAT -----
        if factor["signal"] != "long_entry":
            self.last_signal = factor["reason"]
            return

        if not self._entry_confirmed(symbol, params, int(params["confirm_bars"])):
            self.last_signal = "entry_not_confirmed"
            return

        # Sizing.
        equity_value = float(params["capital"])
        cash_budget  = float(params["capital"])
        trade_price  = float(factor["close"])
        order_value = min(equity_value * float(params["max_position_pct"]),
                          float(params["max_order_value"]))
        order_value = min(order_value, cash_budget)
        if trade_price <= 0.0:
            self.last_signal = "qty_zero"
            return
        qty = int(order_value // trade_price)
        if qty < int(params["fixed_size"]):
            qty = int(params["fixed_size"])
        if qty <= 0:
            self.last_signal = "qty_zero"
            return

        blocked = self._check_minute_guard_entry(params)
        if blocked is not None:
            self.last_signal = blocked
            alert(title="entry blocked", content=blocked)
            return

        buy_price = trade_price * (1.0 + float(params["price_add"]))
        msg = ("BUY " + str(symbol) + " qty=" + str(qty)
               + " price=" + str(buy_price)
               + " score=" + str(factor["raw_score"]))
        if self.LIVE_SUBMIT:
            place_limit(symbol=symbol, price=buy_price, qty=qty,
                        side=OrderSide.BUY,
                        time_in_force=TimeInForce.DAY)
            self._record_trade("BUY", trade_price)
            alert(title="live BUY submitted", content=msg)
        else:
            alert(title="dry_run BUY", content=msg)
        self.last_signal = factor["reason"]
