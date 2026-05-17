# -*- coding: utf-8 -*-
# 精简多因子策略 - 专注经典技术指标组合
# 核心因子：双均线交叉 + RSI超买超卖 + 成交量确认

class Strategy(StrategyBase):

    def initialize(self):
        declare_strategy_type(AlgoStrategyType.SECURITY)
        self.trigger_symbols()
        self.custom_indicator()
        self.global_variables()

        # 运行时状态
        self.entry_price = 0.0
        self.last_signal = ""
        self.used_slices = 0  # 记录已买入的份数
        self.bars_since_last_entry = 0  # 距离上次加仓的K线数
        self.last_entry_price = 0.0     # 上次加仓的价格
        self.highest_price = 0.0        # 持仓期间最高价

    def trigger_symbols(self):
        self.target = declare_trig_symbol()

    def custom_indicator(self):
        # 指标计算在handle_data中实时进行
        pass

    def global_variables(self):
        # 均线参数 - 调整为经典5-20组合，更适合短线交易
        self.fast_window = show_variable(5, GlobalType.INT)
        self.slow_window = show_variable(20, GlobalType.INT)

        # RSI参数 - 保持经典14天，微调阈值减少假信号
        self.rsi_window = show_variable(14, GlobalType.INT)
        self.rsi_oversold = show_variable(20, GlobalType.FLOAT)  # 超卖阈值（从30调至35）
        self.rsi_overbought = show_variable(65, GlobalType.FLOAT)  # 超买阈值（从70调至65）

        # 成交量确认 - 降低要求，1.2倍即可确认
        self.volume_ratio_threshold = show_variable(1.2, GlobalType.FLOAT)  # 成交量倍数

        # 止损止盈 - 调整为更合理的风险收益比
        self.stop_loss_pct = show_variable(0.05, GlobalType.FLOAT)  # 止损5%
        self.take_profit_pct = show_variable(0.10, GlobalType.FLOAT)  # 移动止盈激活阈值（10%）
        self.trailing_drawdown_pct = show_variable(0.05, GlobalType.FLOAT)  # 移动止盈回撤比例（5%）

        # 仓位管理 - 调整为更保守的20%单次投入
        self.position_pct = show_variable(0.2, GlobalType.FLOAT)  # 单次投入资金比例

        # 补仓策略参数
        self.min_add_interval = show_variable(10, GlobalType.INT)  # 最小加仓间隔（K线数）
        self.min_add_loss_pct = show_variable(0.02, GlobalType.FLOAT)  # 最小加仓亏损比例（2%）

        # 实盘开关 - 默认关闭，避免误操作
        self.LIVE_SUBMIT = show_variable(True, GlobalType.BOOL)

        print(f"{self.fast_window} {self.slow_window} {self.rsi_window} {self.rsi_oversold} {self.volume_ratio_threshold}")

    # 辅助函数 - 计算简单移动平均
    def _sma(self, values, window):
        if window <= 0 or len(values) < window:
            return 0.0
        total = sum(float(x) for x in values[-window:])
        return total / window

    # 辅助函数 - 计算RSI
    def _rsi(self, prices, window):
        if window <= 1 or len(prices) < window + 1:
            return 50.0

        # 计算所有的价格变化
        changes = [float(prices[i]) - float(prices[i-1]) for i in range(1, len(prices))]

        # 初始的平均增益和损失（前 window 个周期的简单平均）
        gains = [max(0, change) for change in changes[:window]]
        losses = [max(0, -change) for change in changes[:window]]

        avg_gain = sum(gains) / window
        avg_loss = sum(losses) / window

        # 使用 Wilder 平滑法递推计算后续的平均增益和损失
        for change in changes[window:]:
            gain = max(0, change)
            loss = max(0, -change)
            avg_gain = (avg_gain * (window - 1) + gain) / window
            avg_loss = (avg_loss * (window - 1) + loss) / window

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    # 辅助函数 - 计算成交量比率
    def _volume_ratio(self, volumes, window):
        # 需要 window + 1 根数据，因为要排除当前这根
        if window <= 0 or len(volumes) < window + 1:
            return 1.0

        recent_vol = float(volumes[-2])
        # 计算过去 window 根K线（不包含当前K线）的平均成交量
        avg_vol = sum(float(v) for v in volumes[-window-1:-2]) / window

        if avg_vol == 0:
            return 1.0

        return recent_vol / avg_vol

    def handle_data(self):
        self.bars_since_last_entry += 1
        symbol = self.target

        # 获取历史数据
        warmup = max(self.fast_window, self.slow_window, self.rsi_window)+5

        closes = []
        volumes = []
        # for i in range(warmup):
        k = int(warmup)
        while k > 0:
            close_val = bar_close(symbol=symbol, bar_type=BarType.K_10M,
                                 select=k, session_type=THType.ALL)
            volume_val = bar_volume(symbol=symbol, bar_type=BarType.K_10M,
                                   select=k, session_type=THType.ALL)
            closes.append(float(close_val) if close_val else 0.0)
            volumes.append(float(volume_val) if volume_val else 0.0)
            k = k - 1
            # print(f"{k} val:{close_val} vol:{volume_val}")

        if len(closes) < warmup or len(volumes) < warmup:
            self.last_signal = "数据不足"
            # alert(title="数据不足", content="")
            return

        current_price_val = closes[-1]
        # print(f"current_price_val:{current_price_val} ")
        if current_price_val <= 0:
            self.last_signal = "无效价格"
            # alert(title="无效价格", content="")
            return

        # 计算技术指标
        fast_ma = self._sma(closes, self.fast_window)
        slow_ma = self._sma(closes, self.slow_window)
        rsi_value = self._rsi(closes, self.rsi_window)
        vol_ratio = self._volume_ratio(volumes, 20)

        # 获取当前持仓
        held_qty = int(position_holding_qty(symbol=symbol) or 0)
        # print(f"current_price_val:{current_price_val} {fast_ma} {slow_ma} {rsi_value} {vol_ratio}")

        # 持仓状态处理
        if held_qty > 0:
            # 更新最高价
            highest_price = 0
            for v in closes[-10:-1]:
                if v > highest_price:
                    highest_price = v

            if self.highest_price == 0.0 or current_price_val > self.highest_price:
                self.highest_price = current_price_val

            # 计算盈亏百分比
            pnl_pct = (current_price_val / self.entry_price - 1.0) * 100

            # 止盈止损检查
            if False and pnl_pct <= -self.stop_loss_pct * 100:
                self._exit_position(symbol, current_price_val, "止损")
                return

            # 移动止盈：收益率大于阈值且发生大于回撤比例的回撤
            if pnl_pct >= self.take_profit_pct * 100:
                drawdown_pct = (highest_price - current_price_val) / highest_price
                # drawdown_pct = (fast_ma - current_price_val) / fast_ma
                # drawdown_pct = (self.highest_price - current_price_val) / self.highest_price
                if drawdown_pct >= self.trailing_drawdown_pct:
                    self._exit_position(symbol, current_price_val, f"移动止盈(回撤{drawdown_pct*100:.1f}%)")
                    return

            # RSI超买平仓
            if rsi_value > self.rsi_overbought :
                self._exit_position(symbol, current_price_val, "RSI超买")
                return

            self.last_signal = f"持有中 RSI:{rsi_value:.1f} 盈亏:{pnl_pct:.1f}%"
            # 不 return，允许继续检查开仓条件进行分批建仓

        # 寻找入场机会
        # is_golden_cross = fast_ma > slow_ma
        is_golden_cross = (slow_ma - fast_ma)/slow_ma > 0.01
        is_rsi_oversold = rsi_value < self.rsi_oversold
        is_volume_up = vol_ratio > self.volume_ratio_threshold

        entry_conditions = []

        # 开仓条件改为"RSI超卖 + 放量" or "金叉 + 放量"
        # if is_rsi_oversold and is_volume_up:
        #     entry_conditions.append("RSI超卖+放量")
        # elif is_golden_cross and is_volume_up:
        #     entry_conditions.append("金叉+放量")
        if is_golden_cross and is_rsi_oversold and is_volume_up:
            entry_conditions.append("RSI超卖+放量")

        # 调试输出当前指标状态
        debug_info = f"cur:{current_price_val:.2f} MA5:{fast_ma:.2f} MA20:{slow_ma:.2f} RSI:{rsi_value:.1f} 量比:{vol_ratio:.1f}"
        print(debug_info)

        # 满足任一组合条件即入场
        if len(entry_conditions) > 0:
            self._enter_position(symbol, current_price_val, entry_conditions)
        elif held_qty == 0:
            self.last_signal = f"等待信号 {debug_info}"

    def _enter_position(self, symbol, price, reasons):
        # 获取当前持仓，用于判断是否为加仓
        held_qty = int(position_holding_qty(symbol=symbol) or 0)

        # 如果是加仓，检查补仓条件
        if held_qty > 0:
            if self.bars_since_last_entry < self.min_add_interval:
                self.last_signal = f"加仓间隔不足({self.bars_since_last_entry}/{self.min_add_interval})"
                return

            loss_pct = (self.last_entry_price - price) / self.last_entry_price if self.last_entry_price > 0 else 0
            if loss_pct < self.min_add_loss_pct:
                self.last_signal = f"加仓跌幅不足({loss_pct*100:.1f}%/{self.min_add_loss_pct*100:.1f}%)"
                return

        # 计算买入数量：总资产分为5份，每次最多买入1份
        total_assets = float(net_asset(currency=Currency.HKD) or 0)
        slice_value = total_assets / 5.0  # 每份资金

        if self.used_slices >= 5:
            self.last_signal = "已满仓（5份已用完）"
            return

        # 本次买入1份
        order_value = slice_value
        cash_avail = float(cash(currency=Currency.HKD) or 0)
        order_value = min(order_value, cash_avail)  # 不超过可用现金
        qty = int(order_value // price)
        qty = int(qty/100)*100

        if qty < 100:
            self.last_signal = "资金不足"
            return

        buy_price = price * 1.001  # 加一点点确保成交
        reason_str = ",".join(reasons)

        msg = f"BUY {symbol} qty={qty} price={buy_price:.2f} 第{self.used_slices+1}/5份 原因:{reason_str}"

        if self.LIVE_SUBMIT:
            place_limit(symbol=symbol, price=buy_price, qty=qty,
                        side=OrderSide.BUY, time_in_force=TimeInForce.DAY)

            # 更新加权平均成本价
            if held_qty > 0 and self.entry_price > 0:
                total_cost = self.entry_price * held_qty + price * qty
                self.entry_price = total_cost / (held_qty + qty)
            else:
                self.entry_price = price
                self.highest_price = price  # 首次建仓时初始化最高价

            self.used_slices += 1
            self.bars_since_last_entry = 0
            self.last_entry_price = price
            alert(title="实盘买入", content=msg)
        else:
            # 模拟模式下也更新状态
            if held_qty > 0 and self.entry_price > 0:
                total_cost = self.entry_price * held_qty + price * qty
                self.entry_price = total_cost / (held_qty + qty)
            else:
                self.entry_price = price
                self.highest_price = price  # 首次建仓时初始化最高价
            self.used_slices += 1
            self.bars_since_last_entry = 0
            self.last_entry_price = price
            alert(title="模拟买入", content=msg)

        self.last_signal = f"买入第{self.used_slices}/5份 {reason_str}"

    def _exit_position(self, symbol, price, reason):
        held_qty = int(position_holding_qty(symbol=symbol) or 0)

        if held_qty <= 0:
            return

        sell_price = price * 0.999  # 减一点点确保成交
        pnl_pct = (price / self.entry_price - 1.0) * 100 if self.entry_price > 0 else 0

        msg = f"SELL {symbol} qty={held_qty} price={sell_price:.2f} 原因:{reason} 盈亏:{pnl_pct:.1f}%"

        if self.LIVE_SUBMIT:
            close_positions(symbol=symbol, qty=held_qty)
            alert(title="实盘卖出", content=msg)
        else:
            alert(title="模拟卖出", content=msg)

        self.entry_price = 0.0
        self.used_slices = 0  # 平仓后重置已用份数
        self.bars_since_last_entry = 0
        self.last_entry_price = 0.0
        self.highest_price = 0.0  # 平仓后重置最高价
        self.last_signal = f"卖出 {reason} 盈亏:{pnl_pct:.1f}%"
