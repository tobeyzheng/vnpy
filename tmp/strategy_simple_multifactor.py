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
        self.rsi_oversold = show_variable(35, GlobalType.FLOAT)  # 超卖阈值（从30调至35）
        self.rsi_overbought = show_variable(65, GlobalType.FLOAT)  # 超买阈值（从70调至65）

        # 成交量确认 - 降低要求，1.2倍即可确认
        self.volume_ratio_threshold = show_variable(1.2, GlobalType.FLOAT)  # 成交量倍数

        # 止损止盈 - 调整为更合理的风险收益比
        self.stop_loss_pct = show_variable(0.05, GlobalType.FLOAT)  # 止损5%
        self.take_profit_pct = show_variable(0.15, GlobalType.FLOAT)  # 止盈15%

        # 仓位管理 - 调整为更保守的20%单次投入
        self.position_pct = show_variable(0.2, GlobalType.FLOAT)  # 单次投入资金比例

        # 实盘开关 - 默认关闭，避免误操作
        self.LIVE_SUBMIT = show_variable(False, GlobalType.BOOL)

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

        gains = []
        losses = []

        # 计算价格变化
        for i in range(len(prices) - window, len(prices)):
            if i <= 0:
                continue
            change = float(prices[i]) - float(prices[i-1])
            if change >= 0:
                gains.append(change)
            else:
                losses.append(-change)

        if len(gains) < window or len(losses) < window:
            return 50.0

        # 计算平均增益和平均损失
        avg_gain = sum(gains) / window
        avg_loss = sum(losses) / window

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    # 辅助函数 - 计算成交量比率
    def _volume_ratio(self, volumes, window):
        if window <= 0 or len(volumes) < window:
            return 1.0

        recent_vol = float(volumes[-1])
        avg_vol = sum(float(v) for v in volumes[-window:]) / window

        if avg_vol == 0:
            return 1.0

        return recent_vol / avg_vol

    def handle_data(self):
        symbol = self.target

        # 获取历史数据
        warmup = max(self.fast_window, self.slow_window, self.rsi_window, 20)

        closes = []
        volumes = []
        for i in range(warmup):
            close_val = bar_close(symbol=symbol, bar_type=BarType.K_1M,
                                 select=i+1, session_type=THType.ALL)
            volume_val = bar_volume(symbol=symbol, bar_type=BarType.K_1M,
                                   select=i+1, session_type=THType.ALL)
            closes.append(float(close_val) if close_val else 0.0)
            volumes.append(float(volume_val) if volume_val else 0.0)

        if len(closes) < warmup or len(volumes) < warmup:
            self.last_signal = "数据不足"
            return

        current_price_val = closes[-1]
        if current_price_val <= 0:
            self.last_signal = "无效价格"
            return

        # 计算技术指标
        fast_ma = self._sma(closes, self.fast_window)
        slow_ma = self._sma(closes, self.slow_window)
        rsi_value = self._rsi(closes, self.rsi_window)
        vol_ratio = self._volume_ratio(volumes, 20)

        # 获取当前持仓
        held_qty = int(position_holding_qty(symbol=symbol) or 0)

        # 持仓状态处理
        if held_qty > 0:
            # 计算盈亏百分比
            pnl_pct = (current_price_val / self.entry_price - 1.0) * 100

            # 止盈止损检查
            if pnl_pct <= -self.stop_loss_pct * 100:
                self._exit_position(symbol, current_price_val, "止损")
                return
            elif pnl_pct >= self.take_profit_pct * 100:
                self._exit_position(symbol, current_price_val, "止盈")
                return

            # RSI超买平仓
            if rsi_value > self.rsi_overbought:
                self._exit_position(symbol, current_price_val, "RSI超买")
                return

            self.last_signal = f"持有中 RSI:{rsi_value:.1f} 盈亏:{pnl_pct:.1f}%"
            return

        # 空仓状态 - 寻找入场机会
        entry_conditions = []

        # 条件1: 快线上穿慢线（金叉） - 放宽条件
        if fast_ma > slow_ma:
            entry_conditions.append("金叉信号")

        # 条件2: RSI从超卖区域回升 - 放宽条件
        if rsi_value > self.rsi_oversold:
            entry_conditions.append("RSI超卖回升")

        # 条件3: 成交量放大确认
        if vol_ratio > self.volume_ratio_threshold:
            entry_conditions.append("放量确认")

        # 调试输出当前指标状态
        debug_info = f"MA5:{fast_ma:.2f} MA20:{slow_ma:.2f} RSI:{rsi_value:.1f} 量比:{vol_ratio:.1f} 条件数:{len(entry_conditions)}"

        # 满足至少两个条件才入场
        if len(entry_conditions) >= 2:
            self._enter_position(symbol, current_price_val, entry_conditions)
        else:
            self.last_signal = f"等待信号 {debug_info}"

    def _enter_position(self, symbol, price, reasons):
        # 计算买入数量
        cash_avail = float(cash() or 0)
        order_value = cash_avail * self.position_pct
        qty = int(order_value // price)

        if qty <= 0:
            self.last_signal = "资金不足"
            return

        buy_price = price * 1.001  # 加一点点确保成交
        reason_str = ",".join(reasons)

        msg = f"BUY {symbol} qty={qty} price={buy_price:.2f} 原因:{reason_str}"

        if self.LIVE_SUBMIT:
            place_limit(symbol=symbol, price=buy_price, qty=qty,
                        side=OrderSide.BUY, time_in_force=TimeInForce.DAY)
            self.entry_price = price
            alert(title="实盘买入", content=msg)
        else:
            alert(title="模拟买入", content=msg)

        self.last_signal = f"买入 {reason_str}"

    def _exit_position(self, symbol, price, reason):
        held_qty = int(position_holding_qty(symbol=symbol) or 0)

        if held_qty <= 0:
            return

        sell_price = price * 0.999  # 减一点点确保成交
        pnl_pct = (price / self.entry_price - 1.0) * 100

        msg = f"SELL {symbol} qty={held_qty} price={sell_price:.2f} 原因:{reason} 盈亏:{pnl_pct:.1f}%"

        if self.LIVE_SUBMIT:
            close_positions(symbol=symbol, qty=held_qty)
            alert(title="实盘卖出", content=msg)
        else:
            alert(title="模拟卖出", content=msg)

        self.entry_price = 0.0
        self.last_signal = f"卖出 {reason} 盈亏:{pnl_pct:.1f}%"