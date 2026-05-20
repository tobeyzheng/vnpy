# -*- coding: utf-8 -*-
# 趋势动量增强策略 (Trend + Momentum Enhanced)
# 基于 us_nvda_1d_strategy_multifactor.py 的精简多因子框架升级，引入：
#   1) ATR 动态阈值（动态 RSI 超卖阈、动态 MA gap）
#   2) 信号强度评分系统（强信号 2 份 / 弱信号 1 份）
#   3) 4 层实战过滤器（位置过滤 / 量能过滤 / 时间过滤 / 波动率过滤）
#   4) RSI 从超卖区回升检测（避免接飞刀）
#   5) 受限次数的"超卖加仓"路径
# 设计目标：在 NVDA 这类高波动品种上提升金叉信号胜率与首次入场质量。
# 兼容运行框架：与 us_nvda_1d_strategy_multifactor.py 同样的 declare/handle_data 接口。

class Strategy(StrategyBase):

    def initialize(self):
        declare_strategy_type(AlgoStrategyType.SECURITY)
        self.trigger_symbols()
        self.custom_indicator()
        self.global_variables()

        # 运行时状态
        self.entry_price = 0.0
        self.last_signal = ""
        self.used_slices = 0                # 已买入份数（受 max_slices 限制）
        self.bars_since_last_entry = 0      # 距离上次加仓的K线数
        self.last_entry_price = 0.0         # 上次加仓的价格
        self.highest_price = 0.0            # 持仓期间最高价
        self.bars_since_last_signal = 0     # 距离上次首次入场信号的K线数（时间过滤）
        self.oversold_addon_used = 0        # 已使用的超卖加仓次数
        self.last_rsi = 50.0                # 上一根的 RSI（用于回升检测）

    def trigger_symbols(self):
        self.target = declare_trig_symbol()

    def custom_indicator(self):
        pass

    def global_variables(self):
        # ====== 均线参数 ======
        self.fast_window = show_variable(5, GlobalType.INT)
        self.slow_window = show_variable(20, GlobalType.INT)
        self.long_window = show_variable(60, GlobalType.INT)   # 用于位置过滤的长周期 MA

        # ====== RSI 参数 ======
        self.rsi_window = show_variable(14, GlobalType.INT)
        self.rsi_oversold = show_variable(35, GlobalType.FLOAT)     # 基础超卖阈值
        self.rsi_overbought = show_variable(70, GlobalType.FLOAT)   # 超买阈值

        # ====== 成交量确认 ======
        self.volume_ratio_threshold = show_variable(1.2, GlobalType.FLOAT)
        self.volume_window = show_variable(20, GlobalType.INT)

        # ====== 风险控制 ======
        self.stop_loss_pct = show_variable(0.05, GlobalType.FLOAT)
        self.take_profit_pct = show_variable(0.10, GlobalType.FLOAT)
        self.trailing_drawdown_pct = show_variable(0.05, GlobalType.FLOAT)

        # ====== 仓位管理 ======
        self.position_pct = show_variable(0.2, GlobalType.FLOAT)    # 单份资金占总资产比例
        self.max_slices = show_variable(5, GlobalType.INT)          # 最大份数

        # ====== 加仓策略 ======
        self.min_add_interval = show_variable(10, GlobalType.INT)
        self.min_add_loss_pct = show_variable(0.02, GlobalType.FLOAT)
        self.oversold_addon_max = show_variable(1, GlobalType.INT)  # 超卖加仓最多次数

        # ====== ATR 动态阈值开关 ======
        self.OPT_DYNAMIC_THRESHOLD = show_variable(True, GlobalType.BOOL)
        self.atr_window = show_variable(14, GlobalType.INT)
        # ATR/Price 相对值，用于划分 高波动/趋势/震荡 三档
        self.atr_high_vol = show_variable(0.04, GlobalType.FLOAT)   # > 4% 视为高波动
        self.atr_low_vol = show_variable(0.015, GlobalType.FLOAT)   # < 1.5% 视为低波动/震荡

        # ====== 信号强度评分开关 ======
        self.OPT_SIGNAL_SCORE = show_variable(True, GlobalType.BOOL)
        self.score_strong = show_variable(70, GlobalType.INT)       # 强信号阈值
        self.score_weak = show_variable(40, GlobalType.INT)         # 入场最低分

        # ====== 实战过滤器开关 ======
        self.OPT_POSITION_FILTER = show_variable(True, GlobalType.BOOL)   # 位置过滤
        self.position_high_pct = show_variable(0.95, GlobalType.FLOAT)    # 距 N 日高 < 5% 视为高位
        self.position_lookback = show_variable(60, GlobalType.INT)
        self.OPT_TIME_FILTER = show_variable(True, GlobalType.BOOL)       # 时间过滤
        self.min_signal_interval = show_variable(5, GlobalType.INT)       # 两次首次入场最小间隔
        self.OPT_VOL_FILTER = show_variable(True, GlobalType.BOOL)        # 波动率过滤
        self.atr_extreme = show_variable(0.06, GlobalType.FLOAT)          # ATR/Price > 6% 拒绝入场

        # ====== RSI 回升检测开关 ======
        self.OPT_RSI_RECOVERY = show_variable(True, GlobalType.BOOL)

        # ====== 实盘开关 ======
        self.LIVE_SUBMIT = show_variable(True, GlobalType.BOOL)

        print(f"TrendMomentum cfg: fast={self.fast_window} slow={self.slow_window} "
              f"rsi_w={self.rsi_window} rsi_os={self.rsi_oversold} "
              f"vol_thr={self.volume_ratio_threshold} max_slices={self.max_slices}")

    # ---------------- 基础指标 ----------------
    def _sma(self, values, window):
        if window <= 0 or len(values) < window:
            return 0.0
        return sum(float(x) for x in values[-window:]) / window

    def _rsi(self, prices, window):
        if window <= 1 or len(prices) < window + 1:
            return 50.0
        changes = [float(prices[i]) - float(prices[i-1]) for i in range(1, len(prices))]
        gains = [max(0, c) for c in changes[:window]]
        losses = [max(0, -c) for c in changes[:window]]
        avg_gain = sum(gains) / window
        avg_loss = sum(losses) / window
        for c in changes[window:]:
            g = max(0, c)
            l = max(0, -c)
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
        avg_vol = sum(float(v) for v in volumes[-window-1:-2]) / window
        if avg_vol == 0:
            return 1.0
        return recent_vol / avg_vol

    def _atr_pct(self, highs, lows, closes, window):
        """ATR/Close，作为相对波动率代理。无 high/low 时退化为收盘绝对涨跌幅均值。"""
        if window <= 0 or len(closes) < window + 1:
            return 0.0
        trs = []
        n = len(closes)
        # 使用 max(window) 根 TR，TR 定义为 max(H-L, |H-Cprev|, |L-Cprev|)
        for i in range(n - window, n):
            if i <= 0:
                continue
            h = float(highs[i]) if i < len(highs) and highs[i] else float(closes[i])
            l = float(lows[i]) if i < len(lows) and lows[i] else float(closes[i])
            cp = float(closes[i-1])
            tr = max(h - l, abs(h - cp), abs(l - cp))
            trs.append(tr)
        if not trs:
            return 0.0
        atr = sum(trs) / len(trs)
        last_close = float(closes[-1]) or 1.0
        return atr / last_close

    # ---------------- 动态阈值 ----------------
    def _dynamic_oversold(self, base_threshold, atr_pct_val):
        """波动越大，超卖阈值越宽（更容易触发），反之收紧。"""
        if not self.OPT_DYNAMIC_THRESHOLD:
            return base_threshold
        if atr_pct_val >= self.atr_high_vol:
            return base_threshold + 5.0   # 高波动：35 -> 40
        if atr_pct_val <= self.atr_low_vol:
            return base_threshold - 5.0   # 震荡：35 -> 30
        return base_threshold

    def _dynamic_ma_gap(self, atr_pct_val):
        """金叉所需的 (slow-fast)/slow 阈值动态化，避免高波动期假突破。"""
        if not self.OPT_DYNAMIC_THRESHOLD:
            return 0.01
        if atr_pct_val >= self.atr_high_vol:
            return 0.02   # 高波动：要求 2%
        if atr_pct_val <= self.atr_low_vol:
            return 0.005  # 震荡：要求 0.5%
        return 0.01

    # ---------------- 信号评分 ----------------
    def _signal_score(self, fast_ma, slow_ma, long_ma, rsi_value, vol_ratio,
                      ma_gap_threshold):
        """
        总分 100：
          MA 金叉强度  : 0-40
          RSI 超卖深度 : 0-30
          成交量放大   : 0-20
          趋势对齐     : 0-10  （fast_ma > long_ma）
        """
        score = 0.0

        # 金叉强度：以 (slow_ma - fast_ma)/slow_ma 与阈值的相对值衡量
        if slow_ma > 0:
            gap = (slow_ma - fast_ma) / slow_ma
            if gap > ma_gap_threshold:
                # 阈值线性缩放，超出 3*阈值 给满分 40
                ratio = min(gap / (ma_gap_threshold * 3.0), 1.0)
                score += 40.0 * ratio

        # RSI 超卖深度
        if rsi_value < self.rsi_oversold:
            depth = (self.rsi_oversold - rsi_value) / max(self.rsi_oversold, 1.0)
            score += 30.0 * min(depth * 2.0, 1.0)

        # 成交量放大
        if vol_ratio >= self.volume_ratio_threshold:
            v_ratio = min((vol_ratio - 1.0) / 1.5, 1.0)
            score += 20.0 * v_ratio

        # 趋势对齐
        if long_ma > 0 and fast_ma > long_ma:
            score += 10.0

        return score

    # ---------------- 实战过滤器 ----------------
    def _passes_position_filter(self, current_price, closes):
        if not self.OPT_POSITION_FILTER:
            return True
        n = int(self.position_lookback)
        if len(closes) < n + 1:
            return True
        recent_high = max(float(x) for x in closes[-n:])
        if recent_high <= 0:
            return True
        # 当前价 / 区间最高 < position_high_pct 才允许入场（避免追高）
        return (current_price / recent_high) < self.position_high_pct

    def _passes_time_filter(self):
        if not self.OPT_TIME_FILTER:
            return True
        return self.bars_since_last_signal >= self.min_signal_interval

    def _passes_vol_filter(self, atr_pct_val):
        if not self.OPT_VOL_FILTER:
            return True
        return atr_pct_val < self.atr_extreme

    # ---------------- 主流程 ----------------
    def handle_data(self):
        self.bars_since_last_entry += 1
        self.bars_since_last_signal += 1
        symbol = self.target

        warmup = max(self.fast_window, self.slow_window, self.long_window,
                     self.rsi_window, self.atr_window,
                     self.position_lookback, self.volume_window) + 5

        closes, highs, lows, volumes = [], [], [], []
        k = int(warmup)
        while k > 0:
            c = bar_close(symbol=symbol, bar_type=BarType.K_DAY,
                          select=k, session_type=THType.RTH)
            h = bar_high(symbol=symbol, bar_type=BarType.K_DAY,
                         select=k, session_type=THType.RTH)
            l = bar_low(symbol=symbol, bar_type=BarType.K_DAY,
                        select=k, session_type=THType.RTH)
            v = bar_volume(symbol=symbol, bar_type=BarType.K_DAY,
                           select=k, session_type=THType.RTH)
            closes.append(float(c) if c else 0.0)
            highs.append(float(h) if h else 0.0)
            lows.append(float(l) if l else 0.0)
            volumes.append(float(v) if v else 0.0)
            k -= 1

        if len(closes) < warmup:
            self.last_signal = "数据不足"
            return

        current_price_val = closes[-1]
        if current_price_val <= 0:
            self.last_signal = "无效价格"
            return

        # ====== 指标计算 ======
        fast_ma = self._sma(closes, self.fast_window)
        slow_ma = self._sma(closes, self.slow_window)
        long_ma = self._sma(closes, self.long_window)
        rsi_value = self._rsi(closes, self.rsi_window)
        vol_ratio = self._volume_ratio(volumes, self.volume_window)
        atr_pct_val = self._atr_pct(highs, lows, closes, self.atr_window)

        # 动态阈值
        dyn_oversold = self._dynamic_oversold(self.rsi_oversold, atr_pct_val)
        dyn_ma_gap = self._dynamic_ma_gap(atr_pct_val)

        held_qty = int(position_holding_qty(symbol=symbol) or 0)

        # ====== 持仓状态：先做止盈止损与超买退出 ======
        if held_qty > 0:
            if self.highest_price == 0.0 or current_price_val > self.highest_price:
                self.highest_price = current_price_val

            pnl_pct = (current_price_val / self.entry_price - 1.0) * 100 if self.entry_price > 0 else 0.0

            # 移动止盈：达到激活阈值后，从最高点回撤超过比例则退出
            if pnl_pct >= self.take_profit_pct * 100:
                drawdown_pct = (self.highest_price - current_price_val) / self.highest_price if self.highest_price > 0 else 0
                if drawdown_pct >= self.trailing_drawdown_pct:
                    self._exit_position(symbol, current_price_val,
                                        f"移动止盈(回撤{drawdown_pct*100:.1f}%)")
                    self.last_rsi = rsi_value
                    return

            # RSI 超买退出
            if rsi_value > self.rsi_overbought:
                self._exit_position(symbol, current_price_val, "RSI超买")
                self.last_rsi = rsi_value
                return

            self.last_signal = f"持有中 RSI:{rsi_value:.1f} 盈亏:{pnl_pct:.1f}%"
            # 不 return，允许下文进入加仓判断

        # ====== 入场/加仓信号判定 ======
        # 1) 趋势金叉：基于动态 MA gap
        is_golden_cross = (slow_ma > 0) and ((slow_ma - fast_ma) / slow_ma > dyn_ma_gap) and (fast_ma > 0)
        # 2) 超卖（基础）
        is_rsi_oversold = rsi_value < dyn_oversold
        # 3) 超卖回升（避免接飞刀）
        is_rsi_recovering = (rsi_value > self.last_rsi) and (self.last_rsi < dyn_oversold)
        # 4) 量能确认
        is_volume_up = vol_ratio > self.volume_ratio_threshold

        # 最终的"超卖入场"条件：开启回升检测时使用回升信号，否则沿用基础超卖
        oversold_entry_signal = is_rsi_recovering if self.OPT_RSI_RECOVERY else is_rsi_oversold

        # 评分
        if self.OPT_SIGNAL_SCORE:
            score = self._signal_score(fast_ma, slow_ma, long_ma, rsi_value,
                                       vol_ratio, dyn_ma_gap)
        else:
            score = 100.0 if (is_golden_cross and is_rsi_oversold and is_volume_up) else 0.0

        # 入场条件（OR 逻辑，但都需 量能 + 评分门槛 + 过滤器）
        base_signal_ok = (is_golden_cross or oversold_entry_signal) and is_volume_up
        score_ok = score >= self.score_weak

        # 过滤器
        passes_pos = self._passes_position_filter(current_price_val, closes)
        passes_time = self._passes_time_filter() if held_qty == 0 else True  # 仅首次入场受时间过滤限制
        passes_vol = self._passes_vol_filter(atr_pct_val)

        # 调试输出
        bar_time = device_time(TimeZone.DEVICE_TIME_CCT)
        bar_time_str = bar_time.strftime("%Y-%m-%d") if bar_time else ""
        debug_info = (f"[{bar_time_str}] cur:{current_price_val:.2f} "
                      f"MA{self.fast_window}:{fast_ma:.2f} MA{self.slow_window}:{slow_ma:.2f} "
                      f"MA{self.long_window}:{long_ma:.2f} RSI:{rsi_value:.1f} "
                      f"量比:{vol_ratio:.2f} ATR%:{atr_pct_val*100:.2f} "
                      f"dynOS:{dyn_oversold:.1f} dynGap:{dyn_ma_gap*100:.2f}% score:{score:.0f}")
        print(debug_info)

        # ---------- 决策 ----------
        if base_signal_ok and score_ok and passes_pos and passes_time and passes_vol:
            reasons = []
            if is_golden_cross:
                reasons.append("趋势金叉")
            if oversold_entry_signal:
                reasons.append("RSI回升" if self.OPT_RSI_RECOVERY else "RSI超卖")
            reasons.append(f"score={score:.0f}")
            self._enter_position(symbol, current_price_val, reasons,
                                 score=score, is_oversold_addon=is_rsi_oversold)
        elif held_qty == 0:
            # 给出未入场原因，便于调试
            why = []
            if not base_signal_ok:
                why.append("无金叉/超卖且量能")
            if not score_ok:
                why.append(f"score<{self.score_weak}")
            if not passes_pos:
                why.append("位置过高")
            if not passes_time:
                why.append("时间过滤")
            if not passes_vol:
                why.append("波动率过高")
            self.last_signal = f"等待信号({','.join(why)}) {debug_info}"

        # 末尾更新 last_rsi（用于下一根的回升检测）
        self.last_rsi = rsi_value

    # ---------------- 下单 ----------------
    def _enter_position(self, symbol, price, reasons, score=0.0, is_oversold_addon=False):
        held_qty = int(position_holding_qty(symbol=symbol) or 0)

        # === 加仓约束 ===
        if held_qty > 0:
            if self.bars_since_last_entry < self.min_add_interval:
                self.last_signal = f"加仓间隔不足({self.bars_since_last_entry}/{self.min_add_interval})"
                return
            loss_pct = (self.last_entry_price - price) / self.last_entry_price if self.last_entry_price > 0 else 0
            # 超卖加仓路径：限制次数，不强制要求亏损达 min_add_loss_pct
            if is_oversold_addon and self.oversold_addon_used < self.oversold_addon_max:
                pass  # 允许超卖加仓
            else:
                if loss_pct < self.min_add_loss_pct:
                    self.last_signal = f"加仓跌幅不足({loss_pct*100:.1f}%/{self.min_add_loss_pct*100:.1f}%)"
                    return

        if self.used_slices >= self.max_slices:
            self.last_signal = f"已满仓({self.max_slices}份用完)"
            return

        # === 仓位规模：使用 position_pct × max_slices 控制 ===
        total_assets = float(net_asset(currency=Currency.USD) or 0)
        slice_value = total_assets * self.position_pct  # 每份资金 = 总资产 × position_pct

        # 强信号 2 份，弱信号 1 份；剩余份数不足时取 min
        slices_this_time = 1
        if self.OPT_SIGNAL_SCORE and score >= self.score_strong:
            slices_this_time = 2
        slices_this_time = min(slices_this_time, self.max_slices - self.used_slices)

        order_value = slice_value * slices_this_time
        cash_avail = float(cash(currency=Currency.USD) or 0)
        order_value = min(order_value, cash_avail)
        qty = int(order_value // price)

        if qty <= 0:
            self.last_signal = "资金不足"
            return

        buy_price = price * 1.001
        reason_str = ",".join(reasons)
        msg = (f"BUY {symbol} qty={qty} price={buy_price:.2f} "
               f"第{self.used_slices+1}~{self.used_slices+slices_this_time}/{self.max_slices}份 "
               f"原因:{reason_str}")

        if self.LIVE_SUBMIT:
            place_limit(symbol=symbol, price=buy_price, qty=qty,
                        side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
            if held_qty > 0 and self.entry_price > 0:
                total_cost = self.entry_price * held_qty + price * qty
                self.entry_price = total_cost / (held_qty + qty)
            else:
                self.entry_price = price
                self.highest_price = price
            self.used_slices += slices_this_time
            self.bars_since_last_entry = 0
            self.last_entry_price = price
            if held_qty == 0:
                self.bars_since_last_signal = 0   # 重置首次入场时间过滤
            if is_oversold_addon and held_qty > 0:
                self.oversold_addon_used += 1
            alert(title="实盘买入", content=msg)
        else:
            if held_qty > 0 and self.entry_price > 0:
                total_cost = self.entry_price * held_qty + price * qty
                self.entry_price = total_cost / (held_qty + qty)
            else:
                self.entry_price = price
                self.highest_price = price
            self.used_slices += slices_this_time
            self.bars_since_last_entry = 0
            self.last_entry_price = price
            if held_qty == 0:
                self.bars_since_last_signal = 0
            if is_oversold_addon and held_qty > 0:
                self.oversold_addon_used += 1
            alert(title="模拟买入", content=msg)

        self.last_signal = f"买入{slices_this_time}份(累计{self.used_slices}/{self.max_slices}) {reason_str}"

    def _exit_position(self, symbol, price, reason):
        held_qty = int(position_holding_qty(symbol=symbol) or 0)
        if held_qty <= 0:
            return

        sell_price = price * 0.999
        pnl_pct = (price / self.entry_price - 1.0) * 100 if self.entry_price > 0 else 0
        msg = f"SELL {symbol} qty={held_qty} price={sell_price:.2f} 原因:{reason} 盈亏:{pnl_pct:.1f}%"

        if self.LIVE_SUBMIT:
            close_positions(symbol=symbol, qty=held_qty)
            alert(title="实盘卖出", content=msg)
        else:
            alert(title="模拟卖出", content=msg)

        # 平仓重置
        self.entry_price = 0.0
        self.used_slices = 0
        self.bars_since_last_entry = 0
        self.last_entry_price = 0.0
        self.highest_price = 0.0
        self.oversold_addon_used = 0
        self.last_signal = f"卖出 {reason} 盈亏:{pnl_pct:.1f}%"
