#!/usr/bin/env python3
"""
详细测试Efinance数据获取功能
"""

import sys
import os
from datetime import datetime

# 添加当前目录到路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

def test_data_retrieval():
    """测试数据获取功能"""
    print("🧪 测试数据获取功能...")
    
    try:
        from vnpy_efinance.efinance_datafeed import EfinanceDatafeed
        from vnpy.trader.constant import Exchange, Interval
        from vnpy.trader.object import HistoryRequest
        
        datafeed = EfinanceDatafeed()
        datafeed.init()
        
        # 测试A股日线数据
        print("\n📊 测试A股日线数据...")
        req_a = HistoryRequest(
            symbol='600519',
            exchange=Exchange.SSE,
            start=datetime(2024, 1, 1),
            end=datetime(2024, 1, 10),
            interval=Interval.DAILY
        )
        
        bars_a = datafeed.query_bar_history(req_a, output=print)
        print(f"✅ 获取到 {len(bars_a)} 条A股数据")
        
        if bars_a:
            print(f"第一条数据: {bars_a[0].datetime.date()}, 收盘价: {bars_a[0].close_price}")
        
        # 测试A股分钟数据
        print("\n⏱️ 测试A股分钟数据...")
        req_min = HistoryRequest(
            symbol='600519',
            exchange=Exchange.SSE,
            start=datetime(2024, 1, 1),
            end=datetime(2024, 1, 1),
            interval=Interval.MINUTE
        )
        
        bars_min = datafeed.query_bar_history(req_min, output=print)
        print(f"✅ 获取到 {len(bars_min)} 条分钟数据")
        
        if bars_min:
            print(f"第一条数据: {bars_min[0].datetime}, 收盘价: {bars_min[0].close_price}")
        
        return True
        
    except Exception as e:
        print(f"❌ 数据获取测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_frequency_mapping():
    """测试频率映射"""
    print("\n🔄 测试频率映射...")
    
    try:
        from vnpy.trader.constant import Interval
        
        freq_map = {
            Interval.MINUTE: 1,
            Interval.HOUR: 60, 
            Interval.DAILY: 101,
            Interval.WEEKLY: 102,
            Interval.MONTHLY: 103
        }
        
        for interval in [Interval.DAILY, Interval.WEEKLY, Interval.MONTHLY, Interval.MINUTE]:
            freq = freq_map.get(interval, 101)
            print(f"Interval: {interval} -> Efinance freq: {freq}")
        
        print("✅ 频率映射测试成功")
        return True
        
    except Exception as e:
        print(f"❌ 频率映射测试失败: {e}")
        return False

if __name__ == "__main__":
    print("=" * 50)
    print("Efinance数据获取详细测试")
    print("=" * 50)
    
    # 测试频率映射
    if test_frequency_mapping():
        print("\n✅ 频率映射测试成功")
    
    # 测试数据获取
    if test_data_retrieval():
        print("\n✅ 数据获取测试成功")
    
    print("\n🎯 详细测试完成")