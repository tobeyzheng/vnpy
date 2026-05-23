# -*- coding: utf-8 -*-
# US Multi-Symbol Phase 2 — futumd-compatible single-file strategy (v2,
# trend-following redesign).
#
# Why a v2 file:
#   The v1 file used a 5-AND filter chain (MA + RSI + vol_ratio +
#   ATR-cap + concurrent), a fixed take-profit + trailing-drawdown exit,
#   and a budget formula that double-multiplied position_pct.
#   Empirically this kept cash 80%+ idle on a 5-year backtest and
#   capped total return ~37% versus a buy-and-hold of the same pool
#   above 300%.  v2 replaces that with a classic Donchian / Chandelier
#   trend-follower (Faber 2007 / Clenow / Kaminski-Lo 2014) while
#   keeping v1 untouched for A/B comparison.
#
# Hard constraints (identical to v1):
#   1) Single file, zero local imports. stdlib only.
#   2) NEVER from phase2.* / from services.*
#   3) Every helper is a private method on Strategy; no module-level
#      helper functions.
#   4) Public lifecycle: initialize / trigger_symbols / custom_indicator
#      / global_variables / handle_data / _enter_position / _exit_position
#   5) LIVE_SUBMIT ships False.
#   6) declare_trig_symbol() count <= 20.
#   7) State lives only in self._state[symbol] — no disk I/O.

try:  # pragma: no cover — Futu platform path
    StrategyBase  # type: ignore[name-defined]
    declare_strategy_type  # type: ignore[name-defined]
    declare_trig_symbol  # type: ignore[name-defined]
    show_variable  # type: ignore[name-defined]
    AlgoStrategyType  # type: ignore[name-defined]
    GlobalType  # type: ignore[name-defined]
    Currency  # type: ignore[name-defined]
    BarType  # type: ignore[name-defined]
    THType  # type: ignore[name-defined]
    OrderSide  # type: ignore[name-defined]
    TimeInForce  # type: ignore[name-defined]
    bar_close  # type: ignore[name-defined]
    bar_high  # type: ignore[name-defined]
    bar_low  # type: ignore[name-defined]
    bar_volume  # type: ignore[name-defined]
    net_asset  # type: ignore[name-defined]
    cash  # type: ignore[name-defined]
    position_holding_qty  # type: ignore[name-defined]
    place_limit  # type: ignore[name-defined]
    close_positions  # type: ignore[name-defined]
    alert  # type: ignore[name-defined]
except NameError:
    class StrategyBase:  # type: ignore[no-redef]
        pass

    class _Enum:  # type: ignore[no-redef]
        SECURITY = "SECURITY"
        BOOL = "BOOL"; INT = "INT"; FLOAT = "FLOAT"
        USD = "USD"
        K_DAY = "K_DAY"
        RTH = "RTH"
        BUY = "BUY"; SELL = "SELL"
        DAY = "DAY"

    AlgoStrategyType = _Enum  # type: ignore[assignment]
    GlobalType = _Enum  # type: ignore[assignment]
    Currency = _Enum  # type: ignore[assignment]
    BarType = _Enum  # type: ignore[assignment]
    THType = _Enum  # type: ignore[assignment]
    OrderSide = _Enum  # type: ignore[assignment]
    TimeInForce = _Enum  # type: ignore[assignment]

    def declare_strategy_type(_t):
        return None

    def declare_trig_symbol():
        return "_TRIG_PLACEHOLDER_"

    def show_variable(value, _gtype):
        return value

    def bar_close(symbol="", bar_type=None, select=1, session_type=None):
        return 0.0

    def bar_high(symbol="", bar_type=None, select=1, session_type=None):
        return 0.0

    def bar_low(symbol="", bar_type=None, select=1, session_type=None):
        return 0.0

    def bar_volume(symbol="", bar_type=None, select=1, session_type=None):
        return 0.0

    def net_asset(currency=None):
        return 0.0

    def cash(currency=None):
        return 0.0

    def position_holding_qty(symbol=""):
        return 0

    def place_limit(symbol="", price=0.0, qty=0, side=None, time_in_force=None):
        return None

    def close_positions(symbol="", qty=0):
        return None

    def alert(title="", content=""):
        return None


class Strategy(StrategyBase):

    def initialize(self):
        declare_strategy_type(AlgoStrategyType.SECURITY)
        self.trigger_symbols()
        self.custom_indicator()
        self.global_variables()

        self._state = {}
        for sym in self._pool:
            self._state[sym] = {
                "entry_price": 0.0,
                "highest_price": 0.0,
                "bars_since_last_entry": 0,
                "bars_since_last_exit": 1000000,
                "last_entry_price": 0.0,
                "last_signal": "",
                "last_close": 0.0,
                "last_atr": 0.0,
                "used_slices": 0,
            }

        self._orders_today = 0
        self._last_bar_marker = None

    def trigger_symbols(self):
        self._pool = [
            "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN",
            "TSLA", "AVGO", "AMD", "JPM", "XOM", "UNH",
        ]
        if len(self._pool) > 20:
            raise ValueError("phase2 hard cap: pool size <= 20")
        self._targets = []
        for _ in self._pool:
            self._targets.append(declare_trig_symbol())

    def custom_indicator(self):
        pass

    def global_variables(self):
        # ---- iter1 trend-following parameters (give trends room to breathe) ----
        # ma_long is kept as a metadata pin (not used as an entry gate any
        # more) so legacy reporters that read it don't break.  Entry now
        # only requires SMA(ma_regime) regime + Donchian breakout.
        self.ma_long = show_variable(100, GlobalType.INT)
        self.ma_regime = show_variable(200, GlobalType.INT)
        # Trend-exit MA pushed to 120 — empirically SMA(50) caused the
        # NVDA / AVGO 'wash & re-enter' loop in 2023-2024 (close prints
        # below SMA50 at every healthy pullback).
        self.ma_exit = show_variable(120, GlobalType.INT)
        # Donchian shortened from 55 → 20 to catch trend turns earlier.
        self.donchian_in = show_variable(20, GlobalType.INT)
        self.donchian_out = show_variable(10, GlobalType.INT)

        # Slower ATR (40 vs 22) makes the Chandelier rail less reactive
        # to single-day volatility spikes.
        self.atr_window = show_variable(40, GlobalType.INT)
        # Chandelier multiplier 3 → 5 — NVDA / TSLA annualised vol ~50%+
        # routinely punches through 3·ATR pullbacks in healthy uptrends.
        self.chandelier_k = show_variable(5.0, GlobalType.FLOAT)
        self.target_vol = show_variable(0.20, GlobalType.FLOAT)

        self.pool_budget_pct = show_variable(0.98, GlobalType.FLOAT)
        # Fixed pool only has 6 names; 8 effectively disables the cap.
        self.max_concurrent_holdings = show_variable(8, GlobalType.INT)
        self.cash_buffer_pct = show_variable(0.02, GlobalType.FLOAT)
        self.max_orders_per_day = show_variable(10, GlobalType.INT)
        # Longer cooldown so a stop-out does not immediately re-enter on
        # the same noise.
        self.cooldown_bars_after_exit = show_variable(30, GlobalType.INT)

        self.LIVE_SUBMIT = show_variable(False, GlobalType.BOOL)

        # ---- Compatibility shims (kept so existing optimizer / report
        # writers that read these names off the strategy don't crash) ----
        self.fast_window = show_variable(0, GlobalType.INT)
        self.slow_window = show_variable(0, GlobalType.INT)
        self.rsi_window = show_variable(0, GlobalType.INT)
        self.position_pct = show_variable(0.0, GlobalType.FLOAT)
        self.max_slices = show_variable(1, GlobalType.INT)
        self.stop_loss_pct = show_variable(0.0, GlobalType.FLOAT)
        self.take_profit_pct = show_variable(0.0, GlobalType.FLOAT)
        self.trailing_drawdown_pct = show_variable(0.0, GlobalType.FLOAT)
        self.atr_pct_max = show_variable(1.0, GlobalType.FLOAT)
        self.volume_ratio_threshold = show_variable(0.0, GlobalType.FLOAT)
        self.rsi_oversold = show_variable(0.0, GlobalType.FLOAT)
        self.rsi_overbought = show_variable(100.0, GlobalType.FLOAT)
        self.min_add_interval = show_variable(0, GlobalType.INT)
        self.min_add_loss_pct = show_variable(0.0, GlobalType.FLOAT)

    # -- indicator helpers ---------------------------------------------- #

    def _sma(self, values, window):
        if window <= 0 or len(values) < window:
            return 0.0
        total = 0.0
        for x in values[-window:]:
            total += float(x)
        return total / window

    def _highest(self, values, window):
        if window <= 0 or len(values) < window:
            return 0.0
        m = float(values[-window])
        i = len(values) - window + 1
        while i < len(values):
            v = float(values[i])
            if v > m:
                m = v
            i += 1
        return m

    def _atr(self, highs, lows, closes, window):
        n = min(len(highs), len(lows), len(closes))
        if n < window + 1 or window <= 0:
            return 0.0
        tr_sum = 0.0
        for i in range(-window, 0):
            h = float(highs[i])
            l = float(lows[i])
            pc = float(closes[i - 1])
            tr = max(h - l, abs(h - pc), abs(l - pc))
            tr_sum += tr
        return tr_sum / window

    # -- per-bar driver ------------------------------------------------- #

    def handle_data(self):
        first_sym = self._pool[0]
        try:
            marker = bar_close(symbol=first_sym, bar_type=BarType.K_DAY,
                               select=1, session_type=THType.RTH)
        except Exception:
            marker = None
        if marker != self._last_bar_marker:
            self._orders_today = 0
            self._last_bar_marker = marker

        warmup = int(self.ma_regime) + int(self.atr_window) + 5
        nav_snapshot = float(net_asset(currency=Currency.USD) or 0.0)

        candidates = []
        held_count = 0

        for sym in self._pool:
            st = self._state[sym]
            st["bars_since_last_entry"] += 1
            st["bars_since_last_exit"] += 1

            closes = []
            highs = []
            lows = []
            volumes = []
            k = warmup
            while k > 0:
                cv = bar_close(symbol=sym, bar_type=BarType.K_DAY,
                               select=k, session_type=THType.RTH)
                hv = bar_high(symbol=sym, bar_type=BarType.K_DAY,
                              select=k, session_type=THType.RTH)
                lv = bar_low(symbol=sym, bar_type=BarType.K_DAY,
                             select=k, session_type=THType.RTH)
                vv = bar_volume(symbol=sym, bar_type=BarType.K_DAY,
                                select=k, session_type=THType.RTH)
                closes.append(float(cv) if cv else 0.0)
                highs.append(float(hv) if hv else 0.0)
                lows.append(float(lv) if lv else 0.0)
                volumes.append(float(vv) if vv else 0.0)
                k = k - 1

            if len(closes) < warmup:
                st["last_signal"] = "数据不足"
                continue
            current_price = closes[-1]
            if current_price <= 0:
                st["last_signal"] = "无效价格"
                continue

            ma_long_val = self._sma(closes, int(self.ma_long))
            ma_regime_val = self._sma(closes, int(self.ma_regime))
            ma_exit_val = self._sma(closes, int(self.ma_exit))
            atr_val = self._atr(highs, lows, closes, int(self.atr_window))
            don_in = int(self.donchian_in)
            if don_in > 0 and len(highs) > don_in:
                prior_highs = highs[:-1]
                donchian_high = self._highest(prior_highs, don_in)
            else:
                donchian_high = 0.0

            st["last_close"] = current_price
            st["last_atr"] = atr_val

            held_qty = int(position_holding_qty(symbol=sym) or 0)
            if held_qty > 0:
                held_count += 1
                if st["highest_price"] == 0.0 or current_price > st["highest_price"]:
                    st["highest_price"] = current_price

                exit_reason = None
                # priority 1 — Chandelier stop
                if atr_val > 0 and st["highest_price"] > 0:
                    chandelier_stop = (
                        st["highest_price"]
                        - float(self.chandelier_k) * atr_val
                    )
                    if current_price <= chandelier_stop:
                        exit_reason = "chandelier_stop"
                # priority 2 — trend exit
                if exit_reason is None and ma_exit_val > 0:
                    if current_price < ma_exit_val:
                        exit_reason = "trend_exit"

                if exit_reason is not None:
                    self._exit_position(sym, current_price, exit_reason)
                    continue

            if st["bars_since_last_exit"] < int(self.cooldown_bars_after_exit):
                continue

            cond_regime = ma_regime_val > 0 and current_price > ma_regime_val
            # iter1: removed the SMA(ma_long) duplicate gate; SMA(200)
            # regime + Donchian breakout is sufficient.
            cond_breakout = donchian_high > 0 and current_price >= donchian_high
            cond_concurrent = held_count < int(self.max_concurrent_holdings)

            if cond_regime and cond_breakout and cond_concurrent:
                score = 0.0
                if atr_val > 0 and ma_regime_val > 0:
                    score = (current_price - ma_regime_val) / atr_val
                candidates.append({
                    "symbol": sym,
                    "price": current_price,
                    "score": score,
                    "atr": atr_val,
                    "reasons": ["Regime+SMA{0}+Donchian{1}".format(
                        int(self.ma_long), int(self.donchian_in))],
                })

        if not candidates:
            return
        candidates.sort(key=lambda x: x["score"], reverse=True)

        if int(self.max_concurrent_holdings) <= 0 or float(self.pool_budget_pct) <= 0:
            return
        per_symbol_budget = (
            nav_snapshot * float(self.pool_budget_pct)
            / int(self.max_concurrent_holdings)
        )
        cash_floor = nav_snapshot * float(self.cash_buffer_pct)

        for c in candidates:
            if self._orders_today >= int(self.max_orders_per_day):
                break
            sym = c["symbol"]
            price = float(c["price"])
            atr_val = float(c["atr"])
            if price <= 0:
                continue
            avail_cash = float(cash(currency=Currency.USD) or 0.0)
            spendable = avail_cash - cash_floor
            if spendable <= 0:
                self._state[sym]["last_signal"] = "现金缓冲不足"
                continue

            order_value = per_symbol_budget
            if atr_val > 0 and price > 0 and float(self.target_vol) > 0:
                atr_pct = atr_val / price
                annualised_vol = atr_pct * 15.874  # sqrt(252)
                if annualised_vol > 0:
                    scale = float(self.target_vol) / annualised_vol
                    # iter1: widened bracket so high-vol names like NVDA
                    # don't get permanently underweighted (was 0.4-1.5).
                    if scale < 0.6:
                        scale = 0.6
                    if scale > 2.0:
                        scale = 2.0
                    order_value = per_symbol_budget * scale

            if order_value > spendable:
                order_value = spendable
            qty = int(order_value // price)
            if qty <= 0:
                self._state[sym]["last_signal"] = "资金不足"
                continue
            if (avail_cash - qty * price) < cash_floor - 1e-9:
                continue
            self._enter_position(sym, price, c["reasons"])
            self._orders_today += 1

    # -- order helpers -------------------------------------------------- #

    def _enter_position(self, symbol, price, reasons):
        st = self._state[symbol]
        held_qty = int(position_holding_qty(symbol=symbol) or 0)
        if held_qty > 0:
            st["last_signal"] = "已持仓不加仓"
            return

        nav_now = float(net_asset(currency=Currency.USD) or 0.0)
        if int(self.max_concurrent_holdings) <= 0:
            return
        per_symbol_budget = (
            nav_now * float(self.pool_budget_pct)
            / int(self.max_concurrent_holdings)
        )
        avail_cash = float(cash(currency=Currency.USD) or 0.0)
        cash_floor = nav_now * float(self.cash_buffer_pct)
        spendable = avail_cash - cash_floor
        if spendable <= 0:
            st["last_signal"] = "资金不足"
            return

        order_value = per_symbol_budget
        atr_val = float(st.get("last_atr", 0.0) or 0.0)
        if atr_val > 0 and price > 0 and float(self.target_vol) > 0:
            atr_pct = atr_val / price
            annualised_vol = atr_pct * 15.874
            if annualised_vol > 0:
                scale = float(self.target_vol) / annualised_vol
                if scale < 0.6:
                    scale = 0.6
                if scale > 2.0:
                    scale = 2.0
                order_value = per_symbol_budget * scale

        if order_value > spendable:
            order_value = spendable
        qty = int(order_value // price)
        if qty <= 0:
            st["last_signal"] = "资金不足"
            return

        buy_price = price * 1.001
        reason_str = ",".join(reasons)
        msg = "BUY {sym} qty={q} price={p:.2f} (v2 trend) 原因:{r}".format(
            sym=symbol, q=qty, p=buy_price, r=reason_str,
        )

        if self.LIVE_SUBMIT:
            place_limit(symbol=symbol, price=buy_price, qty=qty,
                        side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
            st["entry_price"] = price
            st["highest_price"] = price
            st["used_slices"] = 1
            st["bars_since_last_entry"] = 0
            st["last_entry_price"] = price
            alert(title="实盘买入", content=msg)
        else:
            st["entry_price"] = price
            st["highest_price"] = price
            st["used_slices"] = 1
            st["bars_since_last_entry"] = 0
            st["last_entry_price"] = price
            alert(title="模拟买入", content=msg)

        st["last_signal"] = "买入(v2) {r}".format(r=reason_str)

    def _exit_position(self, symbol, price, reason):
        st = self._state[symbol]
        held_qty = int(position_holding_qty(symbol=symbol) or 0)
        if held_qty <= 0:
            return
        sell_price = price * 0.999
        pnl_pct = 0.0
        if st["entry_price"] > 0:
            pnl_pct = (price / st["entry_price"] - 1.0) * 100
        msg = "SELL {sym} qty={q} price={p:.2f} (v2 trend) 原因:{r} 盈亏:{pn:.1f}%".format(
            sym=symbol, q=held_qty, p=sell_price, r=reason, pn=pnl_pct,
        )
        if self.LIVE_SUBMIT:
            close_positions(symbol=symbol, qty=held_qty)
            alert(title="实盘卖出", content=msg)
        else:
            alert(title="模拟卖出", content=msg)
        st["entry_price"] = 0.0
        st["used_slices"] = 0
        st["bars_since_last_entry"] = 0
        st["bars_since_last_exit"] = 0
        st["last_entry_price"] = 0.0
        st["highest_price"] = 0.0
        st["last_signal"] = "卖出(v2) {r} 盈亏:{pn:.1f}%".format(
            r=reason, pn=pnl_pct,
        )


if __name__ == "__main__":  # pragma: no cover
    import sys as _sys
    if "--check" in _sys.argv:
        s = Strategy()
        s.initialize()
        print("[futumd-strategy-v2] LIVE_SUBMIT={lv} | pool={n} | "
              "max_concurrent={mc} | pool_budget_pct={pb} | "
              "donchian_in={di} | ma_regime={mr} | chandelier_k={ck}".format(
                  lv=s.LIVE_SUBMIT, n=len(s._pool),
                  mc=s.max_concurrent_holdings, pb=s.pool_budget_pct,
                  di=s.donchian_in, mr=s.ma_regime, ck=s.chandelier_k,
              ))
        raise SystemExit(0)
    raise SystemExit(0)
