#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据下载器 - 使用vnpy_tushare下载回测所需的历史数据
支持A股、港股、美股等市场数据
"""

import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import json

# 添加vnpy路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from vnpy.trader.setting import SETTINGS
from vnpy.trader.datafeed import get_datafeed
from vnpy.trader.object import HistoryRequest, BarData, Interval
from vnpy.trader.constant import Exchange
from vnpy.alpha.lab import AlphaLab


class DataDownloader:
    """数据下载器"""
    
    def __init__(self, lab_path: str = "./tmp/alpha_lab"):
        """初始化数据下载器"""
        self.lab_path = Path(lab_path)
        self.lab_path.mkdir(parents=True, exist_ok=True)
        
        # 初始化AlphaLab
        self.lab = AlphaLab(str(self.lab_path))
        
        # 获取数据服务
        self.datafeed = get_datafeed()
        
        # 数据服务配置
        self.datafeed_name = SETTINGS.get("datafeed.name", "未配置")
        self.datafeed_username = SETTINGS.get("datafeed.username", "")
        
        print(f"✅ 数据下载器已初始化")
        print(f"📊 数据服务: {self.datafeed_name}")
        print(f"👤 用户名: {self.datafeed_username}")
        print(f"📁 数据目录: {self.lab_path}")
    
    def get_market_symbols(self, market: str = "A股") -> List[str]:
        """获取市场标的列表"""
        
        symbols_map = {
            "A股": [
                # 主要指数
                ("000001", "SSE"),  # 上证指数
                ("000300", "SSE"),  # 沪深300
                ("000905", "SSE"),  # 中证500
                ("399001", "SZSE"), # 深证成指
                ("399006", "SZSE"), # 创业板指
                
                # 蓝筹股
                ("600519", "SSE"),  # 贵州茅台
                ("601318", "SSE"),  # 中国平安
                ("601398", "SSE"),  # 工商银行
                ("000858", "SZSE"), # 五粮液
                ("000333", "SZSE"), # 美的集团
                
                # 成长股
                ("300750", "SZSE"), # 宁德时代
                ("002415", "SZSE"), # 海康威视
                ("000001", "SZSE"),  # 平安银行
                ("600036", "SSE"),  # 招商银行
                ("601888", "SSE"),  # 中国中免
            ],
            "港股": [
                ("00700", "HKEX"),  # 腾讯控股
                ("00941", "HKEX"),  # 中国移动
                ("01299", "HKEX"),  # 友邦保险
                ("02318", "HKEX"),  # 中国平安
                ("03988", "HKEX"),  # 中国银行
            ],
            "美股": [
                ("AAPL", "NASDAQ"),  # 苹果
                ("MSFT", "NASDAQ"),  # 微软
                ("GOOGL", "NASDAQ"), # 谷歌
                ("AMZN", "NASDAQ"),  # 亚马逊
                ("TSLA", "NASDAQ"),  # 特斯拉
            ]
        }
        
        return symbols_map.get(market, [])
    
    def download_batch_data(self, 
                           symbols: List[tuple],
                           interval: Interval = Interval.DAILY,
                           start_date: str = "2020-01-01",
                           end_date: str = "2024-12-31") -> Dict[str, Any]:
        """批量下载数据"""
        
        results = {
            "success": [],
            "failed": [],
            "total": len(symbols)
        }
        
        print(f"📥 开始批量下载数据")
        print(f"📊 标的数量: {len(symbols)}")
        print(f"📅 时间范围: {start_date} 到 {end_date}")
        print(f"⏰ 频率: {interval.value}")
        
        for i, (symbol, exchange) in enumerate(symbols, 1):
            print(f"\n[{i}/{len(symbols)}] 下载 {symbol}.{exchange}")
            
            success = self.download_single_data(
                symbol=symbol,
                exchange=exchange,
                interval=interval,
                start_date=start_date,
                end_date=end_date
            )
            
            if success:
                results["success"].append(f"{symbol}.{exchange}")
            else:
                results["failed"].append(f"{symbol}.{exchange}")
        
        print(f"\n✅ 批量下载完成")
        print(f"✅ 成功: {len(results['success'])} 个")
        print(f"❌ 失败: {len(results['failed'])} 个")
        
        if results["failed"]:
            print(f"失败标的: {', '.join(results['failed'])}")
        
        return results
    
    def download_single_data(self,
                           symbol: str,
                           exchange: str = "SSE",
                           interval: Interval = Interval.DAILY,
                           start_date: str = "2020-01-01",
                           end_date: str = "2024-12-31") -> bool:
        """下载单个标的的数据"""
        
        vt_symbol = f"{symbol}.{exchange}"
        
        print(f"📥 下载: {vt_symbol} {interval.value}")
        
        # 创建历史数据请求
        req = HistoryRequest(
            symbol=symbol,
            exchange=exchange,
            interval=interval,
            start=datetime.strptime(start_date, "%Y-%m-%d"),
            end=datetime.strptime(end_date, "%Y-%m-%d")
        )
        
        try:
            # 查询历史数据
            bars = self.datafeed.query_bar_history(req)
            
            if not bars:
                print(f"❌ 未获取到数据: {vt_symbol}")
                return False
            
            print(f"✅ 获取到 {len(bars)} 条K线数据")
            
            # 保存到AlphaLab
            self.lab.save_bar_data(bars)
            
            print(f"💾 数据已保存: {vt_symbol}")
            return True
            
        except Exception as e:
            print(f"❌ 下载失败: {vt_symbol} - {e}")
            return False
    
    def download_index_data(self) -> bool:
        """下载主要指数数据"""
        
        index_symbols = [
            ("000001", "SSE"),   # 上证指数
            ("000300", "SSE"),   # 沪深300
            ("000905", "SSE"),   # 中证500
            ("399001", "SZSE"),  # 深证成指
            ("399006", "SZSE"),  # 创业板指
            ("399005", "SZSE"),  # 中小板指
        ]
        
        print("📊 下载主要指数数据")
        
        results = self.download_batch_data(
            symbols=index_symbols,
            interval=Interval.DAILY,
            start_date="2020-01-01",
            end_date="2024-12-31"
        )
        
        return len(results["success"]) > 0
    
    def download_stock_data(self, market: str = "A股") -> bool:
        """下载股票数据"""
        
        stock_symbols = self.get_market_symbols(market)
        
        if not stock_symbols:
            print(f"❌ 未找到 {market} 的标的列表")
            return False
        
        print(f"📈 下载 {market} 股票数据")
        
        results = self.download_batch_data(
            symbols=stock_symbols,
            interval=Interval.DAILY,
            start_date="2020-01-01",
            end_date="2024-12-31"
        )
        
        return len(results["success"]) > 0
    
    def download_minute_data(self, symbol: str, exchange: str = "SSE") -> bool:
        """下载分钟线数据"""
        
        # 获取最近30天的分钟数据
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)
        
        print(f"⏰ 下载分钟数据: {symbol}.{exchange}")
        
        success = self.download_single_data(
            symbol=symbol,
            exchange=exchange,
            interval=Interval.MINUTE,
            start_date=start_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d")
        )
        
        return success
    
    def check_data_status(self, symbol: str, exchange: str = "SSE") -> Dict[str, Any]:
        """检查数据状态"""
        
        vt_symbol = f"{symbol}.{exchange}"
        
        print(f"🔍 检查数据状态: {vt_symbol}")
        
        # 检查日线数据
        daily_file = self.lab_path / "daily" / f"{vt_symbol}.parquet"
        minute_file = self.lab_path / "minute" / f"{vt_symbol}.parquet"
        
        status = {
            "symbol": vt_symbol,
            "daily_exists": daily_file.exists(),
            "minute_exists": minute_file.exists(),
            "daily_size": daily_file.stat().st_size if daily_file.exists() else 0,
            "minute_size": minute_file.stat().st_size if minute_file.exists() else 0,
        }
        
        if status["daily_exists"]:
            print(f"✅ 日线数据: {status['daily_size']:,} bytes")
        else:
            print("❌ 日线数据: 不存在")
        
        if status["minute_exists"]:
            print(f"✅ 分钟数据: {status['minute_size']:,} bytes")
        else:
            print("❌ 分钟数据: 不存在")
        
        return status
    
    def list_downloaded_symbols(self) -> List[str]:
        """列出已下载的标的"""
        
        daily_path = self.lab_path / "daily"
        symbols = []
        
        if daily_path.exists():
            for file_path in daily_path.glob("*.parquet"):
                symbol = file_path.stem
                symbols.append(symbol)
        
        return sorted(symbols)


def main():
    """主函数 - 演示使用方法"""
    
    # 创建数据下载器
    downloader = DataDownloader()
    
    # 演示：下载指数数据
    print("=" * 50)
    print("1. 下载指数数据")
    print("=" * 50)
    
    success = downloader.download_index_data()
    
    if success:
        print("✅ 指数数据下载完成")
    
    # 演示：下载A股股票数据
    print("\n" + "=" * 50)
    print("2. 下载A股股票数据")
    print("=" * 50)
    
    success = downloader.download_stock_data("A股")
    
    if success:
        print("✅ A股股票数据下载完成")
    
    # 演示：检查数据状态
    print("\n" + "=" * 50)
    print("3. 检查数据状态")
    print("=" * 50)
    
    status = downloader.check_data_status("000300", "SSE")
    
    # 演示：列出已下载标的
    print("\n" + "=" * 50)
    print("4. 已下载标的列表")
    print("=" * 50)
    
    symbols = downloader.list_downloaded_symbols()
    
    if symbols:
        for i, symbol in enumerate(symbols, 1):
            print(f"{i}. {symbol}")
    else:
        print("❌ 未找到已下载的标的")
    
    print("\n" + "=" * 50)
    print("🎯 使用说明")
    print("=" * 50)
    print("""
使用方法:

1. 批量下载数据:
   downloader.download_batch_data([("000300", "SSE"), ("000001", "SSE")])

2. 下载单个标的:
   downloader.download_single_data("000300", "SSE")

3. 下载指数数据:
   downloader.download_index_data()

4. 下载股票数据:
   downloader.download_stock_data("A股")

5. 检查数据状态:
   downloader.check_data_status("000300", "SSE")

6. 列出已下载标的:
   downloader.list_downloaded_symbols()

支持的交易所:
• SSE - 上海证券交易所
• SZSE - 深圳证券交易所
• HKEX - 香港交易所
• NYSE - 纽约证券交易所
• NASDAQ - 纳斯达克

数据频率:
• Interval.DAILY - 日线数据
• Interval.MINUTE - 分钟数据
• Interval.HOUR - 小时数据
""")


if __name__ == "__main__":
    main()