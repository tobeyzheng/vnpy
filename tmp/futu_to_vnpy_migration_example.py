#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
富途策略到vnpy回测的迁移示例
将strategy_simple_multifactor.py转换为vnpy兼容格式
"""

from vnpy.alpha.strategy.template import AlphaStrategy
from vnpy.trader.object import BarData, TradeData, OrderData
from vnpy.trader.constant import Direction, Offset, Interval
from typing import Dict, List
import numpy as np


class FutuMultiFactorVnpyStrategy(AlphaStrategy):
    """
    富途多因子策略的vnpy适配版本
    基于strategy_simple_multifactor.py的核心逻辑
    """

    def __init__(self, strategy_engine, strategy_name, vt_symbols, setting):
        """构造函数"""
        super().__init__(strategy_engine, strategy_name, vt_symbols, setting)

        # 策略参数（从global_variables迁移）
        self.fast_window = 5      # 快线周期
        self.slow_window = 20      # 慢线周期
        self.rsi_window = 14      # RSI周期
        self.rsi_oversold = 35    # RSI超卖阈值
        self.rsi_overbought = 65  # RSI超买阈值
        self.volume_ratio_threshold = 1.2  # 成交量倍数阈值
        self.stop_loss_pct = 0.05  # 止损比例
        self.take_profit_pct = 0.15  # 止盈比例
        self.position_pct = 0.2   # 仓位比例

        # 运行时状态（从initialize迁移）
        self.entry_price = 0.0
        self.last_signal = ""
        self.history_data = {}  # 历史数据缓存

        # 设置策略参数
        for key, value in setting.items():
            if hasattr(self, key):
                setattr(self, key, value)

    def on_init(self):
        """策略初始化（替代initialize函数）"""
        print(f"策略初始化: {self.strategy_name}")
        print(f"交易标的: {self.vt_symbols}")
        print(f"参数设置: fast_window={self.fast_window}, slow_window={self.slow_window}")

        # 初始化历史数据缓存
        for vt_symbol in self.vt_symbols:
            self.history_data[vt_symbol] = {
                'closes': [],
                'volumes': [],
                'timestamps': []
            }

    def on_bars(self, bars: Dict[str, BarData]):
        """K线数据回调（替代handle_data函数）"""
        for vt_symbol, bar in bars.items():
            if vt_symbol not in self.history_data:
                continue

            # 更新历史数据
            self._update_history_data(vt_symbol, bar)

            # 执行策略逻辑
            self._execute_strategy_logic(vt_symbol, bar)

    def _update_history_data(self, vt_symbol: str, bar: BarData):
        """更新历史数据缓存（替代bar_close/bar_volume）"""
        data = self.history_data[vt_symbol]

        # 添加新数据
        data['closes'].append(bar.close_price)
        data['volumes'].append(bar.volume)
        data['timestamps'].append(bar.datetime)

        # 保持数据长度不超过最大窗口
        max_window = max(self.fast_window, self.slow_window, self.rsi_window, 20)
        if len(data['closes']) > max_window * 2:
            data['closes'] = data['closes'][-max_window*2:]
            data['volumes'] = data['volumes'][-max_window*2:]
            data['timestamps'] = data['timestamps'][-max_window*2:]

    def _execute_strategy_logic(self, vt_symbol: str, bar: BarData):
        """执行策略逻辑（替代handle_data的核心逻辑）"""
        data = self.history_data[vt_symbol]

        if len(data['closes']) < max(self.fast_window, self.slow_window, self.rsi_window):
            self.last_signal = "数据不足"
            return

        current_price = data['closes'][-1]
        if current_price <= 0:
            self.last_signal = "无效价格"
            return

        # 计算技术指标
        closes = data['closes']
        volumes = data['volumes']

        fast_ma = self._sma(closes, self.fast_window)
        slow_ma = self._sma(closes, self.slow_window)
        rsi_value = self._rsi(closes, self.rsi_window)
        vol_ratio = self._volume_ratio(volumes, 20)

        # 获取当前持仓（替代position_holding_qty）
        held_qty = self.pos_data.get(vt_symbol, 0)

        # 持仓状态处理
        if held_qty > 0:
            self._handle_position(vt_symbol, current_price, rsi_value, held_qty)
        else:
            self._handle_no_position(vt_symbol, current_price, fast_ma, slow_ma, rsi_value, vol_ratio)

    def _handle_position(self, vt_symbol: str, current_price: float, rsi_value: float, held_qty: float):
        """处理持仓状态"""
        # 计算盈亏百分比
        pnl_pct = (current_price / self.entry_price - 1.0) * 100

        # 止盈止损检查
        if pnl_pct <= -self.stop_loss_pct * 100:
            self._exit_position(vt_symbol, current_price, "止损")
            return
        elif pnl_pct >= self.take_profit_pct * 100:
            self._exit_position(vt_symbol, current_price, "止盈")
            return

        # RSI超买平仓
        if rsi_value > self.rsi_overbought:
            self._exit_position(vt_symbol, current_price, "RSI超买")
            return

        self.last_signal = f"持有中 RSI:{rsi_value:.1f} 盈亏:{pnl_pct:.1f}%"

    def _handle_no_position(self, vt_symbol: str, current_price: float, fast_ma: float,
                           slow_ma: float, rsi_value: float, vol_ratio: float):
        """处理空仓状态"""
        entry_conditions = []

        # 条件1: 快线上穿慢线（金叉）
        if fast_ma > slow_ma:
            entry_conditions.append("金叉信号")

        # 条件2: RSI从超卖区域回升
        if rsi_value > self.rsi_oversold:
            entry_conditions.append("RSI超卖回升")

        # 条件3: 成交量放大确认
        if vol_ratio > self.volume_ratio_threshold:
            entry_conditions.append("放量确认")

        # 满足至少两个条件才入场
        if len(entry_conditions) >= 2:
            self._enter_position(vt_symbol, current_price, entry_conditions)
        else:
            self.last_signal = f"等待信号 MA:{fast_ma:.2f}/{slow_ma:.2f} RSI:{rsi_value:.1f} 量比:{vol_ratio:.1f}"

    def _enter_position(self, vt_symbol: str, price: float, reasons: List[str]):
        """进入仓位（替代富途的买入逻辑）"""
        # 计算买入数量（替代cash()和仓位计算）
        capital = self.strategy_engine.capital
        order_value = capital * self.position_pct
        qty = int(order_value // price)

        if qty <= 0:
            self.last_signal = "资金不足"
            return

        # 使用vnpy的交易接口（替代place_limit）
        order_ids = self.buy(vt_symbol, price, qty)

        reason_str = ",".join(reasons)
        self.entry_price = price
        self.last_signal = f"买入 {reason_str}"

        print(f"买入信号: {vt_symbol} 价格:{price:.2f} 数量:{qty} 原因:{reason_str}")

    def _exit_position(self, vt_symbol: str, price: float, reason: str):
        """退出仓位（替代富途的平仓逻辑）"""
        held_qty = self.pos_data.get(vt_symbol, 0)

        if held_qty <= 0:
            return

        # 使用vnpy的交易接口（替代close_positions）
        order_ids = self.sell(vt_symbol, price, held_qty)

        pnl_pct = (price / self.entry_price - 1.0) * 100
        self.entry_price = 0.0
        self.last_signal = f"卖出 {reason} 盈亏:{pnl_pct:.1f}%"

        print(f"卖出信号: {vt_symbol} 价格:{price:.2f} 数量:{held_qty} 原因:{reason} 盈亏:{pnl_pct:.1f}%")

    # 技术指标计算函数（直接从富途策略迁移）
    def _sma(self, values: List[float], window: int) -> float:
        """计算简单移动平均"""
        if window <= 0 or len(values) < window:
            return 0.0
        total = sum(values[-window:])
        return total / window

    def _rsi(self, prices: List[float], window: int) -> float:
        """计算RSI"""
        if window <= 1 or len(prices) < window + 1:
            return 50.0

        gains = []
        losses = []

        for i in range(len(prices) - window, len(prices)):
            if i <= 0:
                continue
            change = prices[i] - prices[i-1]
            if change >= 0:
                gains.append(change)
            else:
                losses.append(-change)

        if len(gains) < window or len(losses) < window:
            return 50.0

        avg_gain = sum(gains) / window
        avg_loss = sum(losses) / window

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def _volume_ratio(self, volumes: List[float], window: int) -> float:
        """计算成交量比率"""
        if window <= 0 or len(volumes) < window:
            return 1.0

        recent_vol = volumes[-1]
        avg_vol = sum(volumes[-window:]) / window

        if avg_vol == 0:
            return 1.0

        return recent_vol / avg_vol

    def on_trade(self, trade: TradeData):
        """交易回调"""
        print(f"交易完成: {trade.vt_symbol} {trade.direction.value} {trade.volume} @ {trade.price}")


# 使用示例
if __name__ == "__main__":
    # 策略参数设置
    strategy_setting = {
        'fast_window': 5,
        'slow_window': 20,
        'rsi_window': 14,
        'rsi_oversold': 35,
        'rsi_overbought': 65,
        'volume_ratio_threshold': 1.2,
        'stop_loss_pct': 0.05,
        'take_profit_pct': 0.15,
        'position_pct': 0.2
    }

    print("富途策略到vnpy迁移示例完成")
    print("策略核心逻辑已成功转换为vnpy兼容格式")