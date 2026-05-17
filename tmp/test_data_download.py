#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试数据下载脚本 - 验证vn.py数据服务配置
"""

import os
import sys
from pathlib import Path
from datetime import datetime, timedelta

# 添加vnpy路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from vnpy.trader.setting import SETTINGS
from vnpy.trader.datafeed import get_datafeed
from vnpy.trader.object import HistoryRequest, Interval
from vnpy.trader.constant import Exchange

# 交易所映射
exchange_map = {
    "SSE": Exchange.SSE,
    "SZSE": Exchange.SZSE,
    "HKEX": Exchange.SEHK,  # 香港交易所
    "NASDAQ": Exchange.NASDAQ,
    "NYSE": Exchange.NYSE
}


def check_datafeed_config():
    """检查数据服务配置"""
    
    print("🔍 检查数据服务配置")
    print("=" * 50)
    
    # 检查数据服务名称
    datafeed_name = SETTINGS.get("datafeed.name", "未配置")
    print(f"📊 数据服务: {datafeed_name}")
    
    # 检查用户名/Token
    username = SETTINGS.get("datafeed.username", "未配置")
    print(f"👤 用户名/Token: {'*' * len(username) if username else '未配置'}")
    
    # 检查密码
    password = SETTINGS.get("datafeed.password", "未配置")
    print(f"🔑 密码: {'*' * len(password) if password else '未配置'}")
    
    # 获取数据服务实例
    try:
        datafeed = get_datafeed()
        print(f"✅ 数据服务实例创建成功")
        return datafeed
    except Exception as e:
        print(f"❌ 数据服务实例创建失败: {e}")
        return None


def test_single_download(datafeed, symbol: str = "000001", exchange: str = "SSE"):
    """测试单个标的的数据下载"""
    
    print(f"\n📥 测试数据下载: {symbol}.{exchange}")
    print("-" * 50)
    
    try:
        # 创建历史数据请求
        req = HistoryRequest(
            symbol=symbol,
            exchange=exchange_map[exchange],
            interval=Interval.DAILY,
            start=datetime(2024, 1, 1),
            end=datetime(2024, 12, 31)
        )
        
        print(f"📅 请求时间范围: {req.start} 到 {req.end}")
        print(f"⏰ 数据频率: {req.interval}")
        
        # 查询历史数据
        bars = datafeed.query_bar_history(req)
        
        if not bars:
            print(f"❌ 未获取到数据")
            return False
        
        print(f"✅ 成功获取到 {len(bars)} 条K线数据")
        
        # 显示前几条数据
        print(f"\n📊 数据样本:")
        for i, bar in enumerate(bars[:5]):
            print(f"  {bar.datetime.strftime('%Y-%m-%d')}: "
                  f"开{bar.open_price:.2f} 高{bar.high_price:.2f} "
                  f"低{bar.low_price:.2f} 收{bar.close_price:.2f} "
                  f"量{bar.volume:.0f}")
        
        # 显示最后一条数据
        if len(bars) > 5:
            last_bar = bars[-1]
            print(f"  ...")
            print(f"  {last_bar.datetime.strftime('%Y-%m-%d')}: "
                  f"开{last_bar.open_price:.2f} 高{last_bar.high_price:.2f} "
                  f"低{last_bar.low_price:.2f} 收{last_bar.close_price:.2f} "
                  f"量{last_bar.volume:.0f}")
        
        return True
        
    except Exception as e:
        print(f"❌ 数据下载失败: {e}")
        return False


def test_multiple_symbols(datafeed):
    """测试多个标的的数据下载"""
    
    print(f"\n📈 测试多个标的的数据下载")
    print("=" * 50)
    
    # 测试标的列表
    test_symbols = [
        ("000001", "SSE"),    # 上证指数
        ("000300", "SSE"),    # 沪深300
        ("399001", "SZSE"),   # 深证成指
        ("600519", "SSE"),   # 贵州茅台
        ("00700", "HKEX"),   # 腾讯控股
    ]
    
    results = {}
    
    for symbol, exchange in test_symbols:
        success = test_single_download(datafeed, symbol, exchange)
        results[f"{symbol}.{exchange}"] = success
        print()
    
    # 统计结果
    print("📊 测试结果汇总:")
    print("-" * 50)
    
    success_count = sum(1 for result in results.values() if result)
    total_count = len(results)
    
    print(f"✅ 成功: {success_count}/{total_count}")
    
    for symbol, success in results.items():
        status = "✅" if success else "❌"
        print(f"{status} {symbol}")
    
    return success_count > 0


def save_sample_data(datafeed, symbol: str = "000300", exchange: str = "SSE"):
    """保存样本数据到文件"""
    
    print(f"\n💾 保存样本数据: {symbol}.{exchange}")
    print("-" * 50)
    
    try:
        # 获取较长时间范围的数据
        req = HistoryRequest(
            symbol=symbol,
            exchange=exchange_map[exchange],
            interval=Interval.DAILY,
            start=datetime(2020, 1, 1),
            end=datetime(2024, 12, 31)
        )
        
        bars = datafeed.query_bar_history(req)
        
        if not bars:
            print(f"❌ 未获取到数据")
            return False
        
        # 创建数据目录
        data_dir = Path("./tmp/data")
        data_dir.mkdir(parents=True, exist_ok=True)
        
        # 保存为CSV文件
        csv_file = data_dir / f"{symbol}_{exchange}_daily.csv"
        
        with open(csv_file, 'w', encoding='utf-8') as f:
            # 写入表头
            f.write("datetime,open,high,low,close,volume,turnover\n")
            
            # 写入数据
            for bar in bars:
                line = (
                    f"{bar.datetime.strftime('%Y-%m-%d')},"
                    f"{bar.open_price:.2f},"
                    f"{bar.high_price:.2f},"
                    f"{bar.low_price:.2f},"
                    f"{bar.close_price:.2f},"
                    f"{bar.volume:.0f},"
                    f"{bar.turnover:.2f}"
                )
                f.write(line + "\n")
        
        print(f"✅ 数据已保存: {csv_file}")
        print(f"📊 数据条数: {len(bars)}")
        print(f"📅 时间范围: {bars[0].datetime.strftime('%Y-%m-%d')} 到 {bars[-1].datetime.strftime('%Y-%m-%d')}")
        
        return True
        
    except Exception as e:
        print(f"❌ 数据保存失败: {e}")
        return False


def main():
    """主函数"""
    
    print("=" * 60)
    print("vn.py 数据服务测试")
    print("=" * 60)
    
    # 1. 检查数据服务配置
    datafeed = check_datafeed_config()
    
    if not datafeed:
        print("\n❌ 数据服务配置有问题，请检查vt_setting.json文件")
        return
    
    # 2. 测试单个标的下载
    print("\n1. 测试单个标的下载")
    success = test_single_download(datafeed, "000300", "SSE")
    
    if not success:
        print("\n❌ 单个标的下载失败，请检查数据服务配置")
        return
    
    # 3. 测试多个标的下载
    print("\n2. 测试多个标的下载")
    success = test_multiple_symbols(datafeed)
    
    if not success:
        print("\n❌ 多个标的下载测试失败")
        return
    
    # 4. 保存样本数据
    print("\n3. 保存样本数据")
    success = save_sample_data(datafeed, "000300", "SSE")
    
    if success:
        print("\n✅ 数据服务测试完成")
        
        print("\n" + "=" * 60)
        print("🎯 后续使用建议")
        print("=" * 60)
        print("""
1. 数据服务已配置成功，可以开始回测

2. 支持的标的类型:
   • A股: 000001.SSE, 000300.SSE, 399001.SZSE等
   • 港股: 00700.HKEX, 00941.HKEX等
   • 美股: AAPL.NASDAQ, MSFT.NASDAQ等

3. 支持的数据频率:
   • DAILY - 日线数据
   • MINUTE - 分钟数据
   • HOUR - 小时数据

4. 数据文件位置:
   • 样本数据: ./tmp/data/000300_SSE_daily.csv

5. 下一步:
   • 运行 run_local_backtest.py 进行回测
   • 修改策略参数进行优化
   • 测试不同标的和参数组合
""")
    else:
        print("\n❌ 样本数据保存失败")


if __name__ == "__main__":
    main()