#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
富途策略迁移到vnpy的回测运行示例
展示如何在vnpy中实际运行迁移后的多因子策略
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

# 添加项目路径
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from vnpy.alpha.strategy.backtesting import BacktestingEngine
from vnpy.alpha.lab import AlphaLab
from vnpy.trader.constant import Interval

# 导入我们迁移的策略
from futu_to_vnpy_migration_example import FutuMultiFactorVnpyStrategy


def setup_backtest_engine():
    """设置回测引擎"""
    # 创建AlphaLab实例
    lab = AlphaLab()

    # 创建回测引擎
    engine = BacktestingEngine(lab)

    # 设置回测参数
    engine.vt_symbols = ["AAPL.SMART"]  # 苹果股票
    engine.start = datetime(2024, 1, 1)   # 回测开始时间
    engine.end = datetime(2025, 1, 1)     # 回测结束时间
    engine.capital = 100000               # 初始资金10万

    # 设置交易参数
    engine.long_rates = {"AAPL.SMART": 0.0003}  # 买入手续费率
    engine.short_rates = {"AAPL.SMART": 0.0003} # 卖出手续费率
    engine.sizes = {"AAPL.SMART": 1.0}         # 合约乘数
    engine.priceticks = {"AAPL.SMART": 0.01}    # 最小价格变动

    # 设置回测间隔（1分钟K线）
    engine.interval = Interval.MINUTE

    return engine


def configure_strategy():
    """配置策略参数"""
    strategy_setting = {
        # 均线参数（经典5-20组合）
        'fast_window': 5,
        'slow_window': 20,

        # RSI参数
        'rsi_window': 14,
        'rsi_oversold': 35,    # 超卖阈值
        'rsi_overbought': 65,   # 超买阈值

        # 成交量确认
        'volume_ratio_threshold': 1.2,

        # 风险控制
        'stop_loss_pct': 0.05,     # 止损5%
        'take_profit_pct': 0.15,   # 止盈15%

        # 仓位管理
        'position_pct': 0.2        # 单次投入20%
    }

    return strategy_setting


def run_backtest():
    """运行回测"""
    print("=" * 60)
    print("富途多因子策略 - vnpy回测运行示例")
    print("=" * 60)

    # 1. 设置回测引擎
    print("\n[1/4] 设置回测引擎...")
    engine = setup_backtest_engine()

    # 2. 配置策略参数
    print("[2/4] 配置策略参数...")
    strategy_setting = configure_strategy()

    # 3. 设置策略
    print("[3/4] 设置策略...")
    engine.strategy_class = FutuMultiFactorVnpyStrategy
    engine.setting = strategy_setting

    # 4. 运行回测
    print("[4/4] 运行回测...")

    try:
        # 加载历史数据
        print("   - 加载历史数据...")
        engine.load_data()

        # 运行回测
        print("   - 执行回测计算...")
        engine.run_backtesting()

        # 计算回测结果
        print("   - 计算回测指标...")
        engine.calculate_result()

        # 显示回测结果
        print("\n" + "=" * 60)
        print("回测结果汇总")
        print("=" * 60)

        # 基本统计
        print(f"总收益率: {engine.result['total_return']:.2%}")
        print(f"年化收益率: {engine.result['annual_return']:.2%}")
        print(f"最大回撤: {engine.result['max_drawdown']:.2%}")
        print(f"夏普比率: {engine.result['sharpe_ratio']:.2f}")
        print(f"总交易次数: {engine.result['total_trade_count']}")
        print(f"胜率: {engine.result['win_rate']:.2%}")

        # 策略参数回顾
        print("\n策略参数:")
        for key, value in strategy_setting.items():
            print(f"  {key}: {value}")

        return True

    except Exception as e:
        print(f"回测执行失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def save_backtest_report():
    """保存回测报告"""
    # 这里可以添加保存详细回测报告的逻辑
    # 包括：
    # 1. 交易记录
    # 2. 资金曲线
    # 3. 持仓变化
    # 4. 风险指标

    report_content = """
# 富途多因子策略回测报告

## 策略概述
- 策略类型: 多因子技术分析策略
- 核心因子: 双均线交叉 + RSI超买超卖 + 成交量确认
- 数据频率: 1分钟K线
- 回测周期: 2024年全年

## 迁移说明
本策略基于富途量化平台的策略框架，已成功迁移到vnpy回测框架。

## 技术实现
- 使用vnpy的AlphaStrategy基类
- 适配了富途特有的API接口
- 保持了原有的策略逻辑完整性
"""

    report_path = Path(__file__).parent / "futu_migration_backtest_report.md"
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report_content)

    print(f"回测报告已保存至: {report_path}")


if __name__ == "__main__":
    # 运行回测
    success = run_backtest()

    if success:
        # 保存回测报告
        save_backtest_report()

        print("\n" + "=" * 60)
        print("回测完成！策略已成功迁移到vnpy框架")
        print("=" * 60)
        print("\n下一步建议:")
        print("1. 验证回测结果的合理性")
        print("2. 调整策略参数进行优化")
        print("3. 测试不同市场标的")
        print("4. 考虑实盘部署前的进一步验证")
    else:
        print("\n回测失败，请检查错误信息并修正策略代码")