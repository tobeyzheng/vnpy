# -*- coding: utf-8 -*-
# US Multi-Symbol Phase 2 — futumd-compatible single-file strategy.
#
# Hard constraints (DO NOT relax without a new plan):
#   1) Single file, zero local imports.  Allowed imports: stdlib only.
#   2) NEVER `from phase2.* import ...` / `from services.* import ...`.
#   3) Every helper (indicators / risk / budget) is a private method on
#      `Strategy` (prefix `_`); NO module-level helper functions.
#   4) Public lifecycle surface is identical to the NVDA reference
#      `tmp/strategy/us_nvda_1d_strategy_multifactor.py`:
#         initialize / trigger_symbols / custom_indicator /
#         global_variables / handle_data / _enter_position / _exit_position
#   5) `LIVE_SUBMIT` ships False.  Flipping True requires a new plan.
#   6) `declare_trig_symbol()` count <= 20 (project hard cap).
#   7) State lives only in `self._state[symbol]` — no disk I/O.
#
# This file is intentionally written so it can be uploaded to the Futu
# quant platform as-is.  When run outside Futu (CI / py_compile /
# `--check`), a tiny stdlib-only stub block at the bottom of the file
# stands in for the platform symbols so syntax & contract tests stay green.


# --------------------------------------------------------------------- #
# Platform shim — only takes effect when this file is imported outside
# the Futu sandbox.  Inside Futu the platform pre-defines every name
# below globally and this whole try/except block is a no-op.
# --------------------------------------------------------------------- #
try:  # pragma: no cover — platform path
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
    # Stub region — pure stdlib, no third-party deps.
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

    def declare_strategy_type(_t):  # type: ignore[no-redef]
        return None

    def declare_trig_symbol():  # type: ignore[no-redef]
        return "_TRIG_PLACEHOLDER_"

    def show_variable(value, _gtype):  # type: ignore[no-redef]
        return value

    def bar_close(symbol="", bar_type=None, select=1, session_type=None):  # type: ignore[no-redef]
        return 0.0

    def bar_high(symbol="", bar_type=None, select=1, session_type=None):  # type: ignore[no-redef]
        return 0.0

    def bar_low(symbol="", bar_type=None, select=1, session_type=None):  # type: ignore[no-redef]
        return 0.0

    def bar_volume(symbol="", bar_type=None, select=1, session_type=None):  # type: ignore[no-redef]
        return 0.0

    def net_asset(currency=None):  # type: ignore[no-redef]
        return 0.0

    def cash(currency=None):  # type: ignore[no-redef]
        return 0.0

    def position_holding_qty(symbol=""):  # type: ignore[no-redef]
        return 0

    def place_limit(symbol="", price=0.0, qty=0, side=None, time_in_force=None):  # type: ignore[no-redef]
        return None

    def close_positions(symbol="", qty=0):  # type: ignore[no-redef]
        return None

    def alert(title="", content=""):  # type: ignore[no-redef]
        return None


class Strategy(StrategyBase):
    # ----------------------------------------------------------------- #
    # Lifecycle (mirrors NVDA reference one-to-one).
    # ----------------------------------------------------------------- #

    def initialize(self):
        declare_strategy_type(AlgoStrategyType.SECURITY)
        self.trigger_symbols()
        self.custom_indicator()
        self.global_variables()

        # Per-symbol state — kept in a dict so adding/removing pool
        # members never changes attribute names.
        self._state = {}
        for sym in self._pool:
            self._state[sym] = {
                "entry_price": 0.0,
                "used_slices": 0,
                "bars_since_last_entry": 0,
                "bars_since_last_exit": 1000000,
                "last_entry_price": 0.0,
                "highest_price": 0.0,
                "base_capital": 0.0,
                "last_signal": "",
                "last_fast_ma": 0.0,
                "last_slow_ma": 0.0,
                "last_rsi": 50.0,
            }

        # Daily counters — reset by handle_data when the bar advances.
        self._orders_today = 0
        self._last_bar_marker = None

    def trigger_symbols(self):
        # Constant pool — kept inline (no YAML load) so the file remains
        # zero-dependency.  Edit this list together with
        # ``phase2/strategy/config/pool_config.yaml`` to keep both sides
        # in sync; the unit test cross-checks they match.
        self._pool = [
            "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN",
            "TSLA", "AVGO", "AMD", "JPM", "XOM", "UNH",
        ]
        if len(self._pool) > 20:
            raise ValueError("phase2 hard cap: pool size <= 20")

        # Declare each pool member as a trigger symbol — exactly one
        # call per pool member, as required by the futumd contract.
        self._targets = []
        for _ in self._pool:
            self._targets.append(declare_trig_symbol())

    def custom_indicator(self):
        # Indicators are computed per-bar in handle_data.
        pass

    def global_variables(self):
        # ---- Per-symbol money management (NVDA-aligned) ----
        self.fast_window = show_variable(5, GlobalType.INT)
        self.slow_window = show_variable(20, GlobalType.INT)
        self.rsi_window = show_variable(14, GlobalType.INT)
        self.rsi_oversold = show_variable(30.0, GlobalType.FLOAT)
        self.rsi_overbought = show_variable(65.0, GlobalType.FLOAT)
        self.volume_ratio_threshold = show_variable(1.2, GlobalType.FLOAT)
        self.atr_pct_max = show_variable(0.08, GlobalType.FLOAT)

        self.stop_loss_pct = show_variable(0.05, GlobalType.FLOAT)
        self.take_profit_pct = show_variable(0.10, GlobalType.FLOAT)
        self.trailing_drawdown_pct = show_variable(0.05, GlobalType.FLOAT)

        self.position_pct = show_variable(0.10, GlobalType.FLOAT)
        self.max_slices = show_variable(5, GlobalType.INT)
        self.min_add_interval = show_variable(10, GlobalType.INT)
        self.min_add_loss_pct = show_variable(0.02, GlobalType.FLOAT)

        # ---- Portfolio-level (new in phase 2) ----
        self.pool_budget_pct = show_variable(0.80, GlobalType.FLOAT)
        self.max_concurrent_holdings = show_variable(5, GlobalType.INT)
        self.cash_buffer_pct = show_variable(0.05, GlobalType.FLOAT)
        self.max_orders_per_day = show_variable(10, GlobalType.INT)
        self.cooldown_bars_after_exit = show_variable(5, GlobalType.INT)

        # ---- Hard switch ----
        self.LIVE_SUBMIT = show_variable(False, GlobalType.BOOL)

    # ----------------------------------------------------------------- #
    # Indicator helpers — name-aligned with NVDA reference.
    # ----------------------------------------------------------------- #

    def _sma(self, values, window):
        if window <= 0 or len(values) < window:
            return 0.0
        total = 0.0
        for x in values[-window:]:
            total += float(x)
        return total / window

    def _rsi(self, prices, window):
        if window <= 1 or len(prices) < window + 1:
            return 50.0
        changes = []
        for i in range(1, len(prices)):
            changes.append(float(prices[i]) - float(prices[i - 1]))
        gains = []
        losses = []
        for c in changes[:window]:
            gains.append(max(0.0, c))
            losses.append(max(0.0, -c))
        avg_gain = sum(gains) / window
        avg_loss = sum(losses) / window
        for c in changes[window:]:
            g = max(0.0, c)
            l = max(0.0, -c)
            avg_gain = (avg_gain * (window - 1) + g) / window
            avg_loss = (avg_loss * (window - 1) + l) / window
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _volume_ratio(self, volumes, window):
        if window <= 0 or len(volumes) < window + 1:
            return 1.0
        recent_vol = float(volumes[-2])
        avg_vol = 0.0
        for v in volumes[-window - 1:-2]:
            avg_vol += float(v)
        avg_vol = avg_vol / window
        if avg_vol == 0:
            return 1.0
        return recent_vol / avg_vol

    def _atr_pct(self, highs, lows, closes, window):
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
        a = tr_sum / window
        last_close = float(closes[-1])
        if last_close <= 0:
            return 0.0
        return a / last_close

    # ----------------------------------------------------------------- #
    # Per-bar driver — iterates the pool and serialises orders.
    # ----------------------------------------------------------------- #

    def handle_data(self):
        # Reset daily order counter when a new bar arrives.  We use the
        # most-recent close of the first pool symbol as a coarse bar
        # marker; on Futu's daily timeframe it changes once per session.
        first_sym = self._pool[0]
        try:
            marker = bar_close(symbol=first_sym, bar_type=BarType.K_DAY,
                               select=1, session_type=THType.RTH)
        except Exception:
            marker = None
        if marker != self._last_bar_marker:
            self._orders_today = 0
            self._last_bar_marker = marker

        warmup = int(self.slow_window) + int(self.rsi_window) + 5
        nav_snapshot = float(net_asset(currency=Currency.USD) or 0.0)

        # Pass 1 — score every pool member, run the exit chain on holdings,
        # and collect entry candidates.
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

            fast_ma = self._sma(closes, int(self.fast_window))
            slow_ma = self._sma(closes, int(self.slow_window))
            rsi_value = self._rsi(closes, int(self.rsi_window))
            vol_ratio = self._volume_ratio(volumes, 20)
            atrp = self._atr_pct(highs, lows, closes, 14)

            held_qty = int(position_holding_qty(symbol=sym) or 0)
            if held_qty > 0:
                held_count += 1
                # ----- Update trailing high & evaluate exit chain -----
                if st["highest_price"] == 0.0 or current_price > st["highest_price"]:
                    st["highest_price"] = current_price

                exit_reason = None
                # priority 1 — hard stop
                if st["entry_price"] > 0:
                    loss_pct = (st["entry_price"] - current_price) / st["entry_price"]
                    if loss_pct >= float(self.stop_loss_pct):
                        exit_reason = "hard_stop"
                # priority 2 — trailing take-profit
                if exit_reason is None and st["entry_price"] > 0:
                    pnl_pct = (current_price / st["entry_price"]) - 1.0
                    if pnl_pct >= float(self.take_profit_pct) and st["highest_price"] > 0:
                        dd = (st["highest_price"] - current_price) / st["highest_price"]
                        if dd >= float(self.trailing_drawdown_pct):
                            exit_reason = "trailing_tp"
                # priority 3 — trend reverse (fast MA crosses below slow MA)
                if exit_reason is None and fast_ma > 0 and slow_ma > 0:
                    if fast_ma < slow_ma and st["last_fast_ma"] >= st["last_slow_ma"]:
                        exit_reason = "trend_reverse"
                # priority 4 — volatility breakout
                if exit_reason is None and atrp > float(self.atr_pct_max) * 1.5:
                    exit_reason = "vol_breakout"

                if exit_reason is not None:
                    self._exit_position(sym, current_price, exit_reason)
                    st["last_fast_ma"] = fast_ma
                    st["last_slow_ma"] = slow_ma
                    st["last_rsi"] = rsi_value
                    continue

            # ----- Cooldown gate -----
            if st["bars_since_last_exit"] < int(self.cooldown_bars_after_exit):
                st["last_fast_ma"] = fast_ma
                st["last_slow_ma"] = slow_ma
                st["last_rsi"] = rsi_value
                continue

            # ----- 5 entry conditions (all must hold) -----
            cond_trend = False
            if slow_ma > 0:
                cond_trend = (slow_ma - fast_ma) / slow_ma > 0.005 or fast_ma > slow_ma
            # A1 fix: split momentum into two OR'd regimes so the strategy
            # can take both "oversold-bounce" (RSI<35 & rising) AND
            # "trend-follow" (RSI in mid-strong zone & rising) entries.
            # The original single-clause version effectively required a
            # freshly-bouncing RSI alongside an up-trend, which is
            # empirically near-impossible on mega-caps outside of brief
            # 2022-Q1-style windows.
            cond_momentum_bounce = (
                rsi_value < float(self.rsi_oversold) + 5
                and rsi_value >= st["last_rsi"]
            )
            cond_momentum_trend = (
                float(self.rsi_oversold) + 20 <= rsi_value <= float(self.rsi_overbought)
                and rsi_value >= st["last_rsi"]
            )
            cond_momentum = cond_momentum_bounce or cond_momentum_trend
            cond_volume = vol_ratio > float(self.volume_ratio_threshold)
            cond_volatility = atrp <= float(self.atr_pct_max)
            cond_concurrent = held_count < int(self.max_concurrent_holdings)

            st["last_fast_ma"] = fast_ma
            st["last_slow_ma"] = slow_ma
            st["last_rsi"] = rsi_value

            if cond_trend and cond_momentum and cond_volume and cond_volatility and cond_concurrent:
                # Score = simple ranking signal: stronger trend + more volume.
                score = 0.0
                if slow_ma > 0:
                    score = (slow_ma - fast_ma) / slow_ma
                score += vol_ratio * 0.01
                candidates.append({
                    "symbol": sym,
                    "price": current_price,
                    "score": score,
                    "reasons": ["MA+RSI+Vol+ATR"],
                })

        # Pass 2 — sort candidates and serialise budget allocation
        # (re-read cash after every fill).  Allocator logic is inlined
        # here per the "no helper functions" constraint.
        if not candidates:
            return
        # Stable sort: highest score first.
        candidates.sort(key=lambda x: x["score"], reverse=True)

        if int(self.max_concurrent_holdings) <= 0 or float(self.pool_budget_pct) < 0:
            return
        per_symbol_budget = nav_snapshot * float(self.pool_budget_pct) / int(self.max_concurrent_holdings)
        slice_value = per_symbol_budget * float(self.position_pct)
        cash_floor = nav_snapshot * float(self.cash_buffer_pct)

        for c in candidates:
            if self._orders_today >= int(self.max_orders_per_day):
                break
            sym = c["symbol"]
            price = float(c["price"])
            if price <= 0:
                continue
            avail_cash = float(cash(currency=Currency.USD) or 0.0)
            spendable = avail_cash - cash_floor
            if spendable < slice_value:
                self._state[sym]["last_signal"] = "现金缓冲不足"
                continue
            order_value = slice_value
            if order_value > spendable:
                order_value = spendable
            qty = int(order_value // price)
            if qty <= 0:
                continue
            # Final guard — would this fill break the cash buffer?
            if (avail_cash - qty * price) < cash_floor - 1e-9:
                continue
            self._enter_position(sym, price, c["reasons"])
            self._orders_today += 1

    # ----------------------------------------------------------------- #
    # Order helpers — names aligned with NVDA reference; signatures
    # extended to accept ``symbol`` so a single helper serves the pool.
    # ----------------------------------------------------------------- #

    def _enter_position(self, symbol, price, reasons):
        st = self._state[symbol]
        held_qty = int(position_holding_qty(symbol=symbol) or 0)

        # Add-on guard (mirrors NVDA reference).
        if held_qty > 0:
            if st["bars_since_last_entry"] < int(self.min_add_interval):
                st["last_signal"] = "加仓间隔不足"
                return
            if st["last_entry_price"] > 0:
                loss_pct = (st["last_entry_price"] - price) / st["last_entry_price"]
                if loss_pct < float(self.min_add_loss_pct):
                    st["last_signal"] = "加仓跌幅不足"
                    return

        max_slices = int(self.max_slices) if int(self.max_slices) > 0 else 5
        if st["used_slices"] >= max_slices:
            st["last_signal"] = "已满仓"
            return

        if held_qty == 0 or st["base_capital"] <= 0:
            st["base_capital"] = float(net_asset(currency=Currency.USD) or 0.0)

        pct = float(self.position_pct) if float(self.position_pct) > 0 else (1.0 / max_slices)
        slice_value = st["base_capital"] * pct
        avail_cash = float(cash(currency=Currency.USD) or 0.0)
        if avail_cash < slice_value * 0.5:
            st["last_signal"] = "资金不足"
            return
        order_value = slice_value if slice_value < avail_cash else avail_cash
        qty = int(order_value // price)
        if qty <= 0:
            st["last_signal"] = "资金不足"
            return

        buy_price = price * 1.001
        reason_str = ",".join(reasons)
        msg = "BUY {sym} qty={q} price={p:.2f} 第{n}/{m}份 原因:{r}".format(
            sym=symbol, q=qty, p=buy_price,
            n=st["used_slices"] + 1, m=max_slices, r=reason_str,
        )

        if self.LIVE_SUBMIT:
            place_limit(symbol=symbol, price=buy_price, qty=qty,
                        side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
            if held_qty > 0 and st["entry_price"] > 0:
                total_cost = st["entry_price"] * held_qty + price * qty
                st["entry_price"] = total_cost / (held_qty + qty)
            else:
                st["entry_price"] = price
                st["highest_price"] = price
            st["used_slices"] += 1
            st["bars_since_last_entry"] = 0
            st["last_entry_price"] = price
            alert(title="实盘买入", content=msg)
        else:
            if held_qty > 0 and st["entry_price"] > 0:
                total_cost = st["entry_price"] * held_qty + price * qty
                st["entry_price"] = total_cost / (held_qty + qty)
            else:
                st["entry_price"] = price
                st["highest_price"] = price
            st["used_slices"] += 1
            st["bars_since_last_entry"] = 0
            st["last_entry_price"] = price
            alert(title="模拟买入", content=msg)

        st["last_signal"] = "买入第{n}/{m}份 {r}".format(
            n=st["used_slices"], m=max_slices, r=reason_str
        )

    def _exit_position(self, symbol, price, reason):
        st = self._state[symbol]
        held_qty = int(position_holding_qty(symbol=symbol) or 0)
        if held_qty <= 0:
            return
        sell_price = price * 0.999
        pnl_pct = 0.0
        if st["entry_price"] > 0:
            pnl_pct = (price / st["entry_price"] - 1.0) * 100
        msg = "SELL {sym} qty={q} price={p:.2f} 原因:{r} 盈亏:{pn:.1f}%".format(
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
        st["base_capital"] = 0.0
        st["last_signal"] = "卖出 {r} 盈亏:{pn:.1f}%".format(r=reason, pn=pnl_pct)


# --------------------------------------------------------------------- #
# Manual smoke (no live submit, no Futu connection):
#     python3 phase2/strategy/us_multi_symbol_phase2_strategy_futumd.py --check
# --------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    import sys as _sys
    if "--check" in _sys.argv:
        s = Strategy()
        s.initialize()
        print("[futumd-strategy] LIVE_SUBMIT={lv} | pool={n} | "
              "max_concurrent={mc} | pool_budget_pct={pb}".format(
                  lv=s.LIVE_SUBMIT, n=len(s._pool),
                  mc=s.max_concurrent_holdings, pb=s.pool_budget_pct,
              ))
        raise SystemExit(0)
    raise SystemExit(0)
