"""
测试Efinance数据服务
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime, timedelta
from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.object import HistoryRequest

# 临时导入Efinance数据服务
from vnpy_efinance.efinance_datafeed import EfinanceDatafeed


def test_efinance_datafeed():
    """测试Efinance数据服务"""
    print("🧪 开始测试Efinance数据服务...")
    
    # 创建数据服务实例
    datafeed = EfinanceDatafeed()
    
    # 测试初始化
    print("\n1. 测试初始化...")
    if datafeed.init(output=print):
        print("✅ 初始化成功")
    else:
        print("❌ 初始化失败")
        return False
    
    # 测试A股数据获取
    print("\n2. 测试A股数据获取...")
    req_a = HistoryRequest(
        symbol="000001",
        exchange=Exchange.SSE,
        start=datetime(2024, 1, 1),
        end=datetime(2024, 1, 10),
        interval=Interval.DAILY
    )
    
    bars_a = datafeed.query_bar_history(req_a, output=print)
    if bars_a:
        print(f"✅ 成功获取 {len(bars_a)} 条A股数据")
        print(f"   第一条数据: {bars_a[0].datetime.date()}, 收盘价: {bars_a[0].close_price}")
    else:
        print("❌ A股数据获取失败")
    
    # 测试港股数据获取
    print("\n3. 测试港股数据获取...")
    req_hk = HistoryRequest(
        symbol="00700",
        exchange=Exchange.HKFE,
        start=datetime(2024, 1, 1),
        end=datetime(2024, 1, 10),
        interval=Interval.DAILY
    )
    
    bars_hk = datafeed.query_bar_history(req_hk, output=print)
    if bars_hk:
        print(f"✅ 成功获取 {len(bars_hk)} 条港股数据")
        print(f"   第一条数据: {bars_hk[0].datetime.date()}, 收盘价: {bars_hk[0].close_price}")
    else:
        print("⚠️ 港股数据获取失败（可能权限限制）")
    
    # 测试美股数据获取
    print("\n4. 测试美股数据获取...")
    req_us = HistoryRequest(
        symbol="AAPL",
        exchange=Exchange.NASDAQ,
        start=datetime(2024, 1, 1),
        end=datetime(2024, 1, 10),
        interval=Interval.DAILY
    )
    
    bars_us = datafeed.query_bar_history(req_us, output=print)
    if bars_us:
        print(f"✅ 成功获取 {len(bars_us)} 条美股数据")
        print(f"   第一条数据: {bars_us[0].datetime.date()}, 收盘价: {bars_us[0].close_price}")
    else:
        print("⚠️ 美股数据获取失败（可能权限限制）")
    
    # 测试分钟数据获取
    print("\n5. 测试分钟数据获取...")
    req_min = HistoryRequest(
        symbol="000001",
        exchange=Exchange.SSE,
        start=datetime(2024, 1, 1),
        end=datetime(2024, 1, 1),
        interval=Interval.MINUTE
    )
    
    bars_min = datafeed.query_bar_history(req_min, output=print)
    if bars_min:
        print(f"✅ 成功获取 {len(bars_min)} 条分钟数据")
        print(f"   第一条数据: {bars_min[0].datetime}, 收盘价: {bars_min[0].close_price}")
    else:
        print("⚠️ 分钟数据获取失败（可能权限限制）")
    
    print("\n🎯 Efinance数据服务测试完成")
    return True


if __name__ == "__main__":
    test_efinance_datafeed()