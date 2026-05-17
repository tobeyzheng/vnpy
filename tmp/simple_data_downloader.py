#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
简化版数据下载器 - 使用vnpy_tushare下载回测所需的历史数据
"""

import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

# 添加vnpy路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from vnpy.trader.setting import SETTINGS
from vnpy.trader.datafeed import get_datafeed
from vnpy.trader.object import HistoryRequest, BarData, Interval
from vnpy.trader.constant import Exchange


class SimpleDataDownloader:
    """简化版数据下载器"""
    
    def __init__(self):
        """初始化数据下载器"""
        # 获取数据服务
        self.datafeed = get_datafeed()
        
        # 数据目录
        self.data_dir = Path("./tmp/data")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"✅ 数据下载器已初始化")
        print(f"📊 数据服务: {SETTINGS.get('datafeed.name', '未配置')}")
        print(f"📁 数据目录: {self.data_dir}")
    
    def download_stock_data(self, 
                           symbol: str, 
                           exchange: str = "SSE",
                           interval: str = "DAILY",
                           start_date: str = "2020-01-01",
                           end_date: str = "2024-12-31") -> bool:
        """下载股票数据"""
        
        vt_symbol = f"{symbol}.{exchange}"
        
        print(f"📥 下载数据: {vt_symbol} {interval}")
        print(f"📅 时间范围: {start_date} 到 {end_date}")
        
        try:
            # 创建历史数据请求
            req = HistoryRequest(
                symbol=symbol,
                exchange=exchange,
                interval=Interval(interval),
                start=datetime.strptime(start_date, "%Y-%m-%d"),
                end=datetime.strptime(end_date, "%Y-%m-%d")
            )
            
            # 查询历史数据
            bars = self.datafeed.query_bar_history(req)
            
            if not bars:
                print(f"❌ 未获取到数据: {vt_symbol}")
                return False
            
            print(f"✅ 获取到 {len(bars)} 条K线数据")
            
            # 保存数据到文件
            self._save_data_to_file(bars, vt_symbol, interval)
            
            return True
            
        except Exception as e:
            print(f"❌ 下载失败: {e}")
            return False
    
    def download_multiple_stocks(self, 
                                symbols: List[tuple],
                                interval: str = "DAILY",
                                start_date: str = "2020-01-01",
                                end_date: str = "2024-12-31") -> Dict[str, Any]:
        """批量下载多个股票数据"""
        
        results = {
            "success": [],
            "failed": [],
            "total": len(symbols)
        }
        
        print(f"📥 批量下载 {len(symbols)} 个标的")
        
        for i, (symbol, exchange) in enumerate(symbols, 1):
            print(f"\n[{i}/{len(symbols)}] 下载 {symbol}.{exchange}")
            
            success = self.download_stock_data(
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
        
        return results
    
    def download_a_shares(self) -> Dict[str, Any]:
        """下载A股主要指数和股票数据"""
        
        a_share_symbols = [
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
        ]
        
        print("📈 下载A股数据")
        
        return self.download_multiple_stocks(a_share_symbols)
    
    def download_hk_shares(self) -> Dict[str, Any]:
        """下载港股数据"""
        
        hk_symbols = [
            ("00700", "HKEX"),  # 腾讯控股
            ("00941", "HKEX"),  # 中国移动
            ("01299", "HKEX"),  # 友邦保险
            ("02318", "HKEX"),  # 中国平安
            ("03988", "HKEX"),  # 中国银行
        ]
        
        print("📈 下载港股数据")
        
        return self.download_multiple_stocks(hk_symbols)
    
    def download_us_shares(self) -> Dict[str, Any]:
        """下载美股数据"""
        
        us_symbols = [
            ("AAPL", "NASDAQ"),  # 苹果
            ("MSFT", "NASDAQ"),  # 微软
            ("GOOGL", "NASDAQ"), # 谷歌
            ("AMZN", "NASDAQ"),  # 亚马逊
            ("TSLA", "NASDAQ"),  # 特斯拉
        ]
        
        print("📈 下载美股数据")
        
        return self.download_multiple_stocks(us_symbols)
    
    def _save_data_to_file(self, bars: List[BarData], vt_symbol: str, interval: str) -> None:
        """保存数据到文件"""
        
        # 创建文件路径
        file_path = self.data_dir / f"{vt_symbol}_{interval}.csv"
        
        # 转换为CSV格式
        csv_lines = ["datetime,open,high,low,close,volume,turnover"]
        
        for bar in bars:
            csv_line = (
                f"{bar.datetime.strftime('%Y-%m-%d %H:%M:%S')},"
                f"{bar.open_price:.2f},"
                f"{bar.high_price:.2f},"
                f"{bar.low_price:.2f},"
                f"{bar.close_price:.2f},"
                f"{bar.volume:.0f},"
                f"{bar.turnover:.2f}"
            )
            csv_lines.append(csv_line)
        
        # 写入文件
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(csv_lines))
        
        print(f"💾 数据已保存: {file_path}")
    
    def list_downloaded_files(self) -> List[str]:
        """列出已下载的数据文件"""
        
        files = []
        for file_path in self.data_dir.glob("*.csv"):
            files.append(file_path.name)
        
        return sorted(files)
    
    def check_data_status(self, symbol: str, exchange: str = "SSE") -> Dict[str, Any]:
        """检查数据状态"""
        
        vt_symbol = f"{symbol}.{exchange}"
        
        status = {
            "symbol": vt_symbol,
            "daily_exists": False,
            "minute_exists": False,
            "daily_size": 0,
            "minute_size": 0,
        }
        
        # 检查日线数据
        daily_file = self.data_dir / f"{vt_symbol}_DAILY.csv"
        if daily_file.exists():
            status["daily_exists"] = True
            status["daily_size"] = daily_file.stat().st_size
        
        # 检查分钟数据
        minute_file = self.data_dir / f"{vt_symbol}_MINUTE.csv"
        if minute_file.exists():
            status["minute_exists"] = True
            status["minute_size"] = minute_file.stat().st_size
        
        return status


def main():
    """主函数 - 演示使用方法"""
    
    # 创建数据下载器
    downloader = SimpleDataDownloader()
    
    # 演示：下载A股数据
    print("=" * 50)
    print("1. 下载A股数据")
    print("=" * 50)
    
    results = downloader.download_a_shares()
    
    if results["success"]:
        print(f"✅ A股数据下载完成，成功 {len(results['success'])} 个")
    
    # 演示：下载港股数据
    print("\n" + "=" * 50)
    print("2. 下载港股数据")
    print("=" * 50)
    
    results = downloader.download_hk_shares()
    
    if results["success"]:
        print(f"✅ 港股数据下载完成，成功 {len(results['success'])} 个")
    
    # 演示：下载美股数据
    print("\n" + "=" * 50)
    print("3. 下载美股数据")
    print("=" * 50)
    
    results = downloader.download_us_shares()
    
    if results["success"]:
        print(f"✅ 美股数据下载完成，成功 {len(results['success'])} 个")
    
    # 演示：列出已下载文件
    print("\n" + "=" * 50)
    print("4. 已下载文件列表")
    print("=" * 50)
    
    files = downloader.list_downloaded_files()
    
    if files:
        for i, file in enumerate(files, 1):
            print(f"{i}. {file}")
    else:
        print("❌ 未找到已下载的文件")
    
    print("\n" + "=" * 50)
    print("🎯 使用说明")
    print("=" * 50)
    print("""
使用方法:

1. 下载单个股票:
   downloader.download_stock_data("000300", "SSE")

2. 批量下载:
   symbols = [("000300", "SSE"), ("000001", "SSE")]
   downloader.download_multiple_stocks(symbols)

3. 下载A股数据:
   downloader.download_a_shares()

4. 下载港股数据:
   downloader.download_hk_shares()

5. 下载美股数据:
   downloader.download_us_shares()

6. 列出已下载文件:
   downloader.list_downloaded_files()

7. 检查数据状态:
   downloader.check_data_status("000300", "SSE")

支持的交易所:
• SSE - 上海证券交易所
• SZSE - 深圳证券交易所
• HKEX - 香港交易所
• NASDAQ - 纳斯达克
• NYSE - 纽约证券交易所

数据频率:
• DAILY - 日线数据
• MINUTE - 分钟数据
• HOUR - 小时数据
""")


if __name__ == "__main__":
    main()