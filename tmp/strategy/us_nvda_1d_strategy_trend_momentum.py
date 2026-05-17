# -*- coding: utf-8 -*-
# 趋势动量策略 - 修正版多因子策略
# 核心改进：
#   1. 修正金叉定义（fast_ma > slow_ma，原策略误用死叉）
#   2. 入场逻辑改为 OR 组合（趋势跟随 OR 超卖反弹，二选一即可）
#   3. 启用止损保护（原策略 if False 硬禁用）
#   4. RSI 阈值调整（超卖 35 / 超买 75）
#   5. 盈利加仓替代越跌越买（趋势确认后追加）
#   6. 单标的上限 60%（3×20%），原策略满仓风险
#   7. 移动止盈更合理（激活 15%，回撤 8%）

class Strategy(StrategyBase):

    def initialize(self):
        declare_strategy_type(AlgoStrategyType.SECURITY)
        self.trigger_symbols()
        self.custom_indicator()
        self.global_variables()

        # Runtime state
        self.entry_price = 0.0
        self.last_signal = ""
        self.used_slices = 0
        self.bars_since_last_entry = 0
        self.last_entry_price = 0.0
        self.highest_price = 0.0

    def trigger_symbols(self):
        self.target = declare_trig_symbol()

    def custom_indicator(self):
        pass

    def global_variables(self):
        # MA parameters - classic 10/50 for daily trend
        self.fast_window = show_variable(10, GlobalType.INT)
        self.slow_window = show_variable(50, GlobalType.INT)

        # RSI parameters
        self.rsi_window = show_variable(14, GlobalType.INT)
        self.rsi_oversold = show_variable(35, GlobalType.FLOAT)   # oversold threshold
        self.rsi_overbought = show_variable(75, GlobalType.FLOAT) # overbought exit

        # Volume confirmation
        self.volume_ratio_threshold = show_variable(1.2, GlobalType.FLOAT)

        # Stop loss / take profit
        self.stop_loss_pct = show_variable(0.08, GlobalType.FLOAT)       # stop loss 8%
        self.take_profit_pct = show_variable(0.15, GlobalType.FLOAT)     # trailing TP activation 15%
        self.trailing_drawdown_pct = show_variable(0.08, GlobalType.FLOAT) # trailing TP drawdown 8%

        # Position sizing - max 3 slices × 20% = 60% of capital
        self.position_pct = show_variable(0.2, GlobalType.FLOAT)
        self.max_slices = show_variable(3, GlobalType.INT)

        # Add-on (pyramid) parameters - add only on profit
        self.min_add_interval = show_variable(10, GlobalType.INT)   # min bars between add-ons
        self.min_add_profit_pct = show_variable(0.03, GlobalType.FLOAT)  # add only when profit >= 3%

        # Live switch
        self.LIVE_SUBMIT = show_variable(True, GlobalType.BOOL)

    # ---- Helper: simple moving average ----
    def _sma(self, values, window):
        if window <= 0 or len(values) < window:
            return 0.0
        total = sum(float(x) for x in values[-window:])
        return total / window

    # ---- Helper: RSI (Wilder smoothing) ----
    def _rsi(self, prices, window):
        if window <= 1 or len(prices) < window + 1:
            return 50.0

        changes = [float(prices[i]) - float(prices[i - 1]) for i in range(1, len(prices))]

        gains = [max(0, c) for c in changes[:window]]
        losses = [max(0, -c) for c in changes[:window]]

        avg_gain = sum(gains) / window
        avg_loss = sum(losses) / window

        for change in changes[window:]:
            gain = max(0, change)
            loss = max(0, -change)
            avg_gain = (avg_gain * (window - 1) + gain) / window
            avg_loss = (avg_loss * (window - 1) + loss) / window

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    # ---- Helper: volume ratio ----
    def _volume_ratio(self, volumes, window):
        if window <= 0 or len(volumes) < window + 1:
            return 1.0

        recent_vol = float(volumes[-2])
        avg_vol = sum(float(v) for v in volumes[-window - 1:-2]) / window

        if avg_vol == 0:
            return 1.0

        return recent_vol / avg_vol

    def handle_data(self):
        self.bars_since_last_entry += 1
        symbol = self.target

        # Load historical data
        warmup = max(self.fast_window, self.slow_window, self.rsi_window) + 5

        closes = []
        volumes = []
        k = int(warmup)
        while k > 0:
            close_val = bar_close(symbol=symbol, bar_type=BarType.K_DAY,
                                  select=k, session_type=THType.ALL)
            volume_val = bar_volume(symbol=symbol, bar_type=BarType.K_DAY,
                                    select=k, session_type=THType.ALL)
            closes.append(float(close_val) if close_val else 0.0)
            volumes.append(float(volume_val) if volume_val else 0.0)
            k = k - 1

        if len(closes) < warmup or len(volumes) < warmup:
            self.last_signal = "Insufficient data"
            return

        current_price = closes[-1]
        if current_price <= 0:
            self.last_signal = "Invalid price"
            return

        # Calculate indicators
        fast_ma = self._sma(closes, self.fast_window)
        slow_ma = self._sma(closes, self.slow_window)
        rsi_value = self._rsi(closes, self.rsi_window)
        vol_ratio = self._volume_ratio(volumes, 20)

        held_qty = int(position_holding_qty(symbol=symbol) or 0)

        # ---- Holding position: exit logic ----
        if held_qty > 0:
            # Update highest price (use self.highest_price for global tracking)
            if self.highest_price == 0.0 or current_price > self.highest_price:
                self.highest_price = current_price

            pnl_pct = (current_price / self.entry_price - 1.0) * 100

            # Stop loss (ENABLED - was disabled with if False in original)
            if pnl_pct <= -self.stop_loss_pct * 100:
                self._exit_position(symbol, current_price, f"Stop loss ({pnl_pct:.1f}%)")
                return

            # Trailing take profit
            if pnl_pct >= self.take_profit_pct * 100:
                drawdown_pct = (self.highest_price - current_price) / self.highest_price
                if drawdown_pct >= self.trailing_drawdown_pct:
                    self._exit_position(symbol, current_price,
                                        f"Trailing TP (dd={drawdown_pct * 100:.1f}%)")
                    return

            # RSI overbought exit
            if rsi_value > self.rsi_overbought:
                self._exit_position(symbol, current_price, f"RSI overbought ({rsi_value:.1f})")
                return

            self.last_signal = f"Holding RSI:{rsi_value:.1f} PnL:{pnl_pct:.1f}%"
            # Don't return - allow add-on check below

        # ---- Entry / Add-on logic ----
        # CORRECTED: golden cross = fast_ma > slow_ma (was reversed in original)
        is_golden_cross = fast_ma > slow_ma
        is_rsi_oversold = rsi_value < self.rsi_oversold
        is_volume_up = vol_ratio > self.volume_ratio_threshold

        entry_conditions = []

        # OR logic: trend entry OR reversal entry
        is_trend_entry = is_golden_cross and is_volume_up
        is_reversal_entry = is_rsi_oversold and is_volume_up

        if is_trend_entry:
            entry_conditions.append("GoldenCross+Volume")
        if is_reversal_entry:
            entry_conditions.append("RSIOversold+Volume")

        # Debug output
        debug_info = (f"cur:{current_price:.2f} MA{self.fast_window}:{fast_ma:.2f} "
                      f"MA{self.slow_window}:{slow_ma:.2f} RSI:{rsi_value:.1f} "
                      f"VolRatio:{vol_ratio:.1f}")
        print(debug_info)

        if len(entry_conditions) > 0:
            self._enter_position(symbol, current_price, entry_conditions)
        elif held_qty == 0:
            self.last_signal = f"Waiting {debug_info}"

    def _enter_position(self, symbol, price, reasons):
        held_qty = int(position_holding_qty(symbol=symbol) or 0)

        # Add-on check: only add on profit (trend confirmation)
        if held_qty > 0:
            if self.bars_since_last_entry < self.min_add_interval:
                self.last_signal = (f"Add-on interval too short "
                                    f"({self.bars_since_last_entry}/{self.min_add_interval})")
                return

            profit_pct = (price - self.last_entry_price) / self.last_entry_price \
                if self.last_entry_price > 0 else 0
            if profit_pct < self.min_add_profit_pct:
                self.last_signal = (f"Add-on profit insufficient "
                                    f"({profit_pct * 100:.1f}%/{self.min_add_profit_pct * 100:.1f}%)")
                return

        # Position sizing: max_slices × (total_assets / 5)
        total_assets = float(net_asset(currency=Currency.USD) or 0)
        slice_value = total_assets / 5.0

        if self.used_slices >= self.max_slices:
            self.last_signal = f"Max position reached ({self.used_slices}/{self.max_slices})"
            return

        order_value = slice_value
        cash_avail = float(cash(currency=Currency.USD) or 0)
        order_value = min(order_value, cash_avail)
        qty = int(order_value // price)

        if qty <= 0:
            self.last_signal = "Insufficient funds"
            return

        buy_price = price * 1.001
        reason_str = ",".join(reasons)

        msg = (f"BUY {symbol} qty={qty} price={buy_price:.2f} "
               f"slice {self.used_slices + 1}/{self.max_slices} reason:{reason_str}")

        if self.LIVE_SUBMIT:
            place_limit(symbol=symbol, price=buy_price, qty=qty,
                        side=OrderSide.BUY, time_in_force=TimeInForce.DAY)

            if held_qty > 0 and self.entry_price > 0:
                total_cost = self.entry_price * held_qty + price * qty
                self.entry_price = total_cost / (held_qty + qty)
            else:
                self.entry_price = price
                self.highest_price = price

            self.used_slices += 1
            self.bars_since_last_entry = 0
            self.last_entry_price = price
            alert(title="Live buy", content=msg)
        else:
            if held_qty > 0 and self.entry_price > 0:
                total_cost = self.entry_price * held_qty + price * qty
                self.entry_price = total_cost / (held_qty + qty)
            else:
                self.entry_price = price
                self.highest_price = price
            self.used_slices += 1
            self.bars_since_last_entry = 0
            self.last_entry_price = price
            alert(title="Sim buy", content=msg)

        self.last_signal = f"Buy slice {self.used_slices}/{self.max_slices} {reason_str}"

    def _exit_position(self, symbol, price, reason):
        held_qty = int(position_holding_qty(symbol=symbol) or 0)

        if held_qty <= 0:
            return

        sell_price = price * 0.999
        pnl_pct = (price / self.entry_price - 1.0) * 100 if self.entry_price > 0 else 0

        msg = (f"SELL {symbol} qty={held_qty} price={sell_price:.2f} "
               f"reason:{reason} PnL:{pnl_pct:.1f}%")

        if self.LIVE_SUBMIT:
            close_positions(symbol=symbol, qty=held_qty)
            alert(title="Live sell", content=msg)
        else:
            alert(title="Sim sell", content=msg)

        self.entry_price = 0.0
        self.used_slices = 0
        self.bars_since_last_entry = 0
        self.last_entry_price = 0.0
        self.highest_price = 0.0
        self.last_signal = f"Sell {reason} PnL:{pnl_pct:.1f}%"