#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试策略交易条件判断逻辑
"""

def test_strategy_conditions():
    """测试策略的入场条件判断逻辑"""

    # 模拟数据
    closes = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0, 109.0,
              110.0, 111.0, 112.0, 113.0, 114.0, 115.0, 116.0, 117.0, 118.0, 119.0, 120.0]
    volumes = [1000, 1200, 1100, 1300, 1400, 1500, 1600, 1700, 1800, 1900,
               2000, 2100, 2200, 2300, 2400, 2500, 2600, 2700, 2800, 2900, 3000]

    # 策略参数
    fast_window = 5
    slow_window = 20
    rsi_window = 14
    rsi_oversold = 35
    volume_ratio_threshold = 1.2

    # 计算指标
    fast_ma = sum(closes[-fast_window:]) / fast_window
    slow_ma = sum(closes[-slow_window:]) / slow_window

    # 简单RSI计算
    gains = sum(max(closes[i] - closes[i-1], 0) for i in range(len(closes)-rsi_window, len(closes)))
    losses = sum(max(closes[i-1] - closes[i], 0) for i in range(len(closes)-rsi_window, len(closes)))
    avg_gain = gains / rsi_window
    avg_loss = losses / rsi_window
    rs = avg_gain / avg_loss if avg_loss > 0 else float('inf')
    rsi_value = 100 - (100 / (1 + rs)) if avg_loss > 0 else 100

    # 成交量比率
    recent_vol = volumes[-1]
    avg_vol = sum(volumes[-20:]) / 20
    vol_ratio = recent_vol / avg_vol

    # 检查交易条件
    entry_conditions = []

    # 条件1: 快线上穿慢线（金叉）
    if fast_ma > slow_ma:
        entry_conditions.append("金叉信号")

    # 条件2: RSI从超卖区域回升
    if rsi_value > rsi_oversold:
        entry_conditions.append("RSI超卖回升")

    # 条件3: 成交量放大确认
    if vol_ratio > volume_ratio_threshold:
        entry_conditions.append("放量确认")

    print("=== 策略条件测试结果 ===")
    print(f"快线MA({fast_window}): {fast_ma:.2f}")
    print(f"慢线MA({slow_window}): {slow_ma:.2f}")
    print(f"RSI({rsi_window}): {rsi_value:.1f}")
    print(f"成交量比率: {vol_ratio:.1f}")
    print(f"满足条件: {entry_conditions}")
    print(f"条件数量: {len(entry_conditions)}")
    print(f"是否触发交易: {'是' if len(entry_conditions) >= 2 else '否'}")

    # 测试不同场景
    print("\n=== 不同场景测试 ===")

    # 场景1: 理想情况（三个条件都满足）
    print("场景1 - 理想情况:")
    print(f"  MA5 > MA20: {fast_ma > slow_ma}")
    print(f"  RSI > 35: {rsi_value > rsi_oversold}")
    print(f"  量比 > 1.2: {vol_ratio > volume_ratio_threshold}")

    # 场景2: 只有两个条件满足
    print("\n场景2 - 两个条件满足:")
    print(f"  MA5 > MA20: {fast_ma > slow_ma}")
    print(f"  RSI > 35: {rsi_value > rsi_oversold}")
    print(f"  量比 > 1.2: {False}")  # 假设量比不满足

    # 场景3: 只有一个条件满足
    print("\n场景3 - 一个条件满足:")
    print(f"  MA5 > MA20: {fast_ma > slow_ma}")
    print(f"  RSI > 35: {False}")  # 假设RSI不满足
    print(f"  量比 > 1.2: {False}")  # 假设量比不满足

if __name__ == "__main__":
    test_strategy_conditions()