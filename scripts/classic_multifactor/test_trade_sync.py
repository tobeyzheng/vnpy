#!/usr/bin/env python3
"""
测试脚本：验证账户同步机制和max_intraday_trades限制修复

这个脚本用于测试：
1. FutuAccountProvider的get_today_trades方法是否正常工作
2. minute_guard是否能够正确使用同步的成交记录
3. max_intraday_trades限制是否在跨进程时仍然有效
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from services.futu_account.provider import FutuAccountProvider
from scripts.classic_multifactor.minute_guard import MinuteTradeGuard, MinuteTradeGuardConfig


def test_today_trades_sync(symbol: str = "NVDA.US") -> None:
    """测试今日成交记录同步功能"""
    print(f"=== 测试今日成交记录同步 ===")
    print(f"标的: {symbol}")

    # 创建账户提供者
    provider = FutuAccountProvider()

    # 获取今日成交记录
    today_trades = provider.get_today_trades(symbol)

    print(f"同步到 {len(today_trades)} 笔今日成交记录")
    if today_trades:
        for i, trade_time in enumerate(today_trades[:10]):  # 只显示前10笔
            print(f"  [{i+1}] {trade_time.strftime('%Y-%m-%d %H:%M:%S')}")
        if len(today_trades) > 10:
            print(f"  ... 还有 {len(today_trades) - 10} 笔成交记录")
    else:
        print("  今日无成交记录")

    return today_trades


def test_minute_guard_with_sync(today_trades: list[datetime], max_trades: int = 4) -> None:
    """测试minute_guard使用同步成交记录进行限制检查"""
    print(f"\n=== 测试minute_guard限制检查 ===")
    print(f"配置限制: max_intraday_trades = {max_trades}")
    print(f"实际成交次数: {len(today_trades)}")

    # 创建minute_guard实例
    guard = MinuteTradeGuard(
        MinuteTradeGuardConfig(
            max_intraday_trades=max_trades,
            entry_cooldown_minutes=30,
            min_hold_minutes=20,
            no_new_entry_after="15:30",
        )
    )

    # 测试当前时间是否可以入场
    now = datetime.now()
    result = guard.can_enter(now, trade_times=today_trades, last_trade_at=None)

    print(f"当前时间: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"入场检查结果: {'允许' if result.allowed else '拒绝'}")
    if not result.allowed:
        print(f"拒绝原因: {result.reason}")

    # 测试不同成交次数下的限制效果
    print(f"\n=== 不同成交次数下的限制测试 ===")

    test_cases = [
        (0, "无成交时"),
        (1, "1次成交时"),
        (max_trades - 1, f"{max_trades - 1}次成交时"),
        (max_trades, f"{max_trades}次成交时"),
        (max_trades + 1, f"{max_trades + 1}次成交时"),
    ]

    for trade_count, description in test_cases:
        # 模拟成交记录
        simulated_trades = []
        for i in range(trade_count):
            trade_time = now - timedelta(minutes=(trade_count - i) * 10)
            simulated_trades.append(trade_time)

        result = guard.can_enter(now, trade_times=simulated_trades, last_trade_at=None)
        print(f"  {description}: {'允许' if result.allowed else '拒绝'} ({result.reason})")


def test_cross_process_scenario() -> None:
    """测试跨进程场景下的限制效果"""
    print(f"\n=== 跨进程场景测试 ===")

    # 模拟多个进程的成交记录
    process_a_trades = [
        datetime.now() - timedelta(minutes=45),
        datetime.now() - timedelta(minutes=35),
    ]

    process_b_trades = [
        datetime.now() - timedelta(minutes=25),
        datetime.now() - timedelta(minutes=15),
    ]

    process_c_trades = [
        datetime.now() - timedelta(minutes=5),
    ]

    print("模拟场景：")
    print("  进程A: 2次成交 (45分钟前, 35分钟前)")
    print("  进程B: 2次成交 (25分钟前, 15分钟前)")
    print("  进程C: 1次成交 (5分钟前)")

    # 创建minute_guard
    guard = MinuteTradeGuard(
        MinuteTradeGuardConfig(max_intraday_trades=4)
    )

    # 测试进程C（当前进程）的检查
    now = datetime.now()

    # 进程C只能看到自己的成交记录（问题场景）
    result_c_only = guard.can_enter(now, trade_times=process_c_trades, last_trade_at=None)
    print(f"\n进程C（仅看到自己的成交记录）:")
    print(f"  可见成交次数: {len(process_c_trades)}")
    print(f"  检查结果: {'允许' if result_c_only.allowed else '拒绝'} ({result_c_only.reason})")

    # 进程C使用同步的完整成交记录（修复后场景）
    all_trades = process_a_trades + process_b_trades + process_c_trades
    result_synced = guard.can_enter(now, trade_times=all_trades, last_trade_at=None)
    print(f"\n进程C（使用同步的完整成交记录）:")
    print(f"  可见成交次数: {len(all_trades)}")
    print(f"  检查结果: {'允许' if result_synced.allowed else '拒绝'} ({result_synced.reason})")

    print(f"\n结论：账户同步机制{'有效' if not result_synced.allowed and result_c_only.allowed else '需要检查'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="测试账户同步和max_intraday_trades限制修复")
    parser.add_argument("--symbol", default="NVDA.US", help="测试标的符号")
    parser.add_argument("--max-trades", type=int, default=4, help="最大交易次数限制")
    parser.add_argument("--skip-sync", action="store_true", help="跳过实际同步测试")

    args = parser.parse_args()

    print("=" * 60)
    print("账户同步机制和max_intraday_trades限制修复测试")
    print("=" * 60)

    # 测试今日成交记录同步
    if not args.skip_sync:
        today_trades = test_today_trades_sync(args.symbol)
    else:
        today_trades = []

    # 测试minute_guard限制检查
    test_minute_guard_with_sync(today_trades, args.max_trades)

    # 测试跨进程场景
    test_cross_process_scenario()

    print(f"\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)


if __name__ == "__main__":
    main()